"""
Pre-Cache Generator for Default Branch `branch_v1` & Fallback `branch_v2_cached` (Owned by P2 / worker-1).

Feature & Use Cases:
- W1-T2A: Concurrently renders all 4 scenes of `shared/mock_branch_1.json` (360p, 4s)
  with SI metric physical anchoring and Decisions API QA, persisting `branch_v1.json`.
- W1-T2C: Generates `backend/assets/props/crystal_vase_broken.png` via `create_prop()`
  and pre-caches `shared/mock_branch_2.json` Scene 2..4 into `branch_v2_cached.json`
  (reusing Scene 1 from `branch_v1`, zero prefix rerender).
"""

import asyncio
import shutil
import time

from backend import config
from backend.director.hydrogen_judge import inspect_tail_frame
from backend.director.state_tree import save_branch
from backend.logger import log_function_call
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import render_scene_media
from backend.schemas import BranchState


@log_function_call
async def cache_all() -> tuple[BranchState, BranchState]:
    """Generate `crystal_vase_broken.png`, cache `branch_v2_cached`, and cache `branch_v1`."""
    # 1. Generate transparent shattered blue-rose crystal vase prop (W1-T2C)
    _, broken_item = await create_prop(
        name="crystal_vase_broken",
        description="Shattered crystal vase glass shards lying flat on floor with scattered blue rose petals",
        prop_id="crystal_vase_broken",
    )
    print(f"[Prop] crystal_vase_broken -> {broken_item.image_url}")

    # 2. Pre-cache Branch 2 (HE) Scene 2..4 into dedicated branch_v2_scene_* files
    branch_v2 = BranchState.model_validate_json(config.SHARED_MOCK_BRANCH_2.read_text(encoding="utf-8"))
    ref_images_v2 = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "star_token.png"),
    ]

    async def _cache_v2_scene(scene) -> None:
        if scene.scene_id == 1:
            scene.media.cam_a_final_url = "/assets/finals/scene_1_cam_a.mp4"
            scene.media.cam_b_final_url = "/assets/finals/scene_1_cam_b.mp4"
            scene.media.cam_a_tail_frame_url = "/assets/tails/scene_1_cam_a_tail.png"
            scene.media.cam_b_tail_frame_url = "/assets/tails/scene_1_cam_b_tail.png"
            scene.needs_rerender = False
            scene.reused_from_branch = "branch_v1"
            return

        media = await render_scene_media(scene, ref_images_v2, tail_inspector=inspect_tail_frame)
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

    for scene in branch_v2.scenes:
        await _cache_v2_scene(scene)
    branch_v2_cached = branch_v2.model_copy(update={"branch_id": "branch_v2_cached"})
    save_branch(branch_v2_cached)
    print(f"[Branch 2] Saved {branch_v2_cached.branch_id} ({len(branch_v2_cached.scenes)} scenes)")

    # 3. Pre-cache Branch 1 (BE) Scene 1..4 with SI metric prompt + Decisions QA
    branch_v1 = BranchState.model_validate_json(config.SHARED_MOCK_BRANCH_1.read_text(encoding="utf-8"))
    ref_images_v1 = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
    ]

    for scene in branch_v1.scenes:
        scene.media = await render_scene_media(scene, ref_images_v1, tail_inspector=inspect_tail_frame)
        scene.needs_rerender = False
    save_branch(branch_v1)
    print(f"[Branch 1] Saved {branch_v1.branch_id} ({len(branch_v1.scenes)} scenes)")
    return branch_v1, branch_v2_cached


if __name__ == "__main__":
    t0 = time.perf_counter()
    asyncio.run(cache_all())
    print(f"All W1-T2A + W1-T2C pre-caching completed in {time.perf_counter() - t0:.2f}s")
