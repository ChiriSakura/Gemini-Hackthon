"""
Pre-Cache Generator for Default Branch `branch_v1` (Owned by P2 / worker-1).

Feature & Use Cases:
- Renders all 4 scenes of `shared/mock_branch_1.json` sequentially (1 -> 2 -> 3 -> 4)
  so each scene chains seamlessly from the previous scene's Camera A and Camera B
  tail frames, while running Camera A + Camera B x 2 takes concurrently per scene.
- Persists canonical MP4 clips under `backend/assets/finals/`, tail frames under
  `backend/assets/tails/`, and writes the populated `BranchState` JSON into
  `backend/assets/branches/branch_v1.json` for zero-latency demo playback and
  prefix reuse.
"""

import asyncio
import time

from backend import config
from backend.director.state_tree import save_branch
from backend.logger import log_function_call
from backend.media.video_renderer import render_scene_media
from backend.schemas import BranchState


@log_function_call
async def cache_branch_v1() -> BranchState:
    """Render and persist all 4 dual-camera scenes of `branch_v1` to disk."""
    branch = BranchState.model_validate_json(config.SHARED_MOCK_BRANCH_1.read_text(encoding="utf-8"))
    ref_images = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
    ]

    prev_tail_a: str | None = None
    prev_tail_b: str | None = None

    for scene in sorted(branch.scenes, key=lambda s: s.scene_id):
        t0 = time.perf_counter()
        media = await render_scene_media(
            scene=scene,
            reference_images=ref_images,
            prev_tail_a=prev_tail_a,
            prev_tail_b=prev_tail_b,
        )
        scene.media = media
        scene.needs_rerender = False
        prev_tail_a = media.cam_a_tail_frame_url
        prev_tail_b = media.cam_b_tail_frame_url
        elapsed = round(time.perf_counter() - t0, 2)
        print(
            f"[Scene {scene.scene_id}] {scene.title} cached in {elapsed}s | "
            f"cam_a={media.cam_a_final_url} | cam_b={media.cam_b_final_url} | qa={media.qa_score}"
        )

    save_branch(branch)
    return branch


if __name__ == "__main__":
    t_start = time.perf_counter()
    result = asyncio.run(cache_branch_v1())
    print(f"Done caching {result.branch_id} ({len(result.scenes)} scenes) in {time.perf_counter() - t_start:.2f}s")
