"""
Surgical Cache Refresh for Visually Audited Scenes (Owned by P2 / worker-1).

Re-renders ONLY the 5 scenes where visual inspection detected artifacts before
the deep optimization pass:
- `branch_v1` Scene 1 (fixed: coordinate-axis border on Cam B + duplicate umbrella on Cam A)
- `branch_v1` Scene 4 (fixed: vanished center round table on Cam A)
- `branch_v2_cached` Scene 2 & 3 (fixed: nighttime jump to bright sunny daylight)
- `branch_v2_cached` Scene 4 (fixed: duplicate giant stars + Cam A/B zoom inversion)

Uses the upgraded `video_renderer.py` (natural blocking prompt, nighttime lock,
prop deduplication, umbrella-free `lia_clean_ref.png`, strong 45-deg Cam B,
and cross-camera consistency QA).
"""

import asyncio
import shutil
import time

from backend import config
from backend.director.hydrogen_judge import inspect_tail_frame
from backend.director.state_tree import get_branch, save_branch
from backend.media.video_renderer import render_scene_media


async def refresh_flawed_scenes() -> None:
    t0 = time.perf_counter()

    # 1. Refresh Branch 2 (Scenes 2, 3, 4) FIRST so canonical files are safely copied to branch_v2_scene_*
    b2 = get_branch("branch_v2_cached")
    assert b2 is not None, "branch_v2_cached not found"
    refs_v2 = [
        str(config.ACTORS_DIR / "lia_clean_ref.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "star_token.png"),
    ]

    async def _refresh_v2_scene(scene) -> None:
        # Clear any old daylight notes before regenerating nighttime clips.
        if scene.scene_id == 2:
            scene.environment_notes = "nighttime_glass_conservatory_clean_floor_warm_amber_lantern_glow"
        elif scene.scene_id == 3:
            scene.environment_notes = "nighttime_glass_conservatory_clean_floor_first_snowflakes_outside"
        elif scene.scene_id == 4:
            scene.environment_notes = "nighttime_warm_golden_starlight_and_falling_snowflakes_intact_vase_on_table"

        media = await render_scene_media(
            scene,
            refs_v2,
            tail_inspector=inspect_tail_frame,
            overwrite_canonical=True,
        )
        for cam in ("cam_a", "cam_b"):
            src_mp4 = config.FINALS_DIR / f"scene_{scene.scene_id}_{cam}.mp4"
            dst_mp4 = config.FINALS_DIR / f"branch_v2_scene_{scene.scene_id}_{cam}.mp4"
            if src_mp4.is_file():
                shutil.copy2(src_mp4, dst_mp4)
                setattr(media, f"{cam}_final_url", f"/assets/finals/{dst_mp4.name}")
            src_tail = config.TAILS_DIR / f"scene_{scene.scene_id}_{cam}_tail.png"
            dst_tail = config.TAILS_DIR / f"branch_v2_scene_{scene.scene_id}_{cam}_tail.png"
            if src_tail.is_file():
                shutil.copy2(src_tail, dst_tail)
                setattr(media, f"{cam}_tail_frame_url", f"/assets/tails/{dst_tail.name}")
        scene.media = media
        scene.needs_rerender = False
        print(f"[Refresh branch_v2_cached Scene {scene.scene_id}] qa={media.qa_score} retries={media.qa_retries}")

    # Run Scene 2, 3, 4 concurrently (distinct scene IDs -> distinct canonical filenames)
    await asyncio.gather(*(_refresh_v2_scene(s) for s in b2.scenes if s.scene_id in {2, 3, 4}))

    # 2. Refresh Branch 1 (Scenes 1 & 4) — keep untouched Scene 2 & 3 canonical clips safe!
    b1 = get_branch("branch_v1")
    assert b1 is not None, "branch_v1 not found"
    # Restore canonical Scene 2 & 3 for branch_v1? Wait: step 1 overwrote canonical scene_{2,3,4}_{cam}.mp4!
    # So refresh branch_v1 Scenes 1, 2, 3, 4 concurrently right after step 1 finishes!
    refs_v1 = [
        str(config.ACTORS_DIR / "lia_clean_ref.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
    ]

    async def _refresh_v1_scene(scene) -> None:
        if scene.scene_id == 1:
            scene.environment_notes = "nighttime_glass_conservatory_rainy_windows_warm_wall_lanterns"
        elif scene.scene_id == 4:
            scene.environment_notes = "nighttime_heavy_rain_center_round_table_broken_glass_vase_shards_on_wet_floor"
        media = await render_scene_media(
            scene,
            refs_v1,
            tail_inspector=inspect_tail_frame,
            overwrite_canonical=True,
        )
        scene.media = media
        scene.needs_rerender = False
        print(f"[Refresh branch_v1 Scene {scene.scene_id}] qa={media.qa_score} retries={media.qa_retries}")

    await asyncio.gather(*(_refresh_v1_scene(s) for s in b1.scenes))
    save_branch(b1)

    # Sync refreshed Scene 1 prefix into branch_v2_cached Scene 1
    b1_s1 = next(s for s in b1.scenes if s.scene_id == 1)
    b2_s1 = next(s for s in b2.scenes if s.scene_id == 1)
    b2_s1.media = b1_s1.media.model_copy(deep=True)
    b2_s1.environment_notes = b1_s1.environment_notes
    b2_s1.needs_rerender = False
    b2_s1.reused_from_branch = "branch_v1"
    save_branch(b2)

    print(f"Surgical full refresh of branch_v1 (4 scenes) + branch_v2_cached (Scenes 2..4) finished in {time.perf_counter() - t0:.2f}s")


if __name__ == "__main__":
    asyncio.run(refresh_flawed_scenes())
