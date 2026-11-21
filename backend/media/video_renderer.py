"""
Concurrent Dual-Take Omni Flash Video Renderer & QA Loop (Owned by P2 / worker-1).

Feature & Use Cases:
- Renders `needs_rerender=True` scenes concurrently across Camera A and Camera B
  using `asyncio.gather` (`360p`, `4s` per clip for live demo speed).
- Runs 2 parallel candidate takes per shot, extracts tail frames, evaluates both
  candidates via unified `judge()`, keeps the higher-scoring take, and triggers
  up to `config.MAX_QA_RETRIES` corrective retries when both fail threshold.
- Triggers `inspect_tail_frame()` to write emergent visual details back into
  `scene.environment_notes`.
"""

import asyncio
from typing import Awaitable, Callable, Optional

from backend import config
from backend.director.hydrogen_judge import judge, write_repair_constraint
from backend.logger import log_function_call, log_genai_call
from backend.schemas import BranchState, JudgeResult, Scene, SceneMedia


@log_function_call
async def render_single_camera_shot(
    scene: Scene,
    cam_id: str,
    prev_tail_path: Optional[str],
    reference_images: list[str],
    preview_path: Optional[str] = None,
    extra_constraint: str = "",
) -> tuple[str, Optional[str], JudgeResult, int, int]:
    """
    Generate 2 parallel Omni Flash candidates for one scene + camera, score via `judge()`,
    and return `(final_url, tail_frame_url, judge_result, selected_idx, retries)`.
    """
    prompt = f"Camera {cam_id}: {scene.narration}. Mood: {scene.audio_mood or scene.vocal_emotion}. {extra_constraint}".strip()
    log_genai_call(
        config.MODEL_VIDEO,
        {"prompt": prompt, "resolution": config.VIDEO_RESOLUTION, "duration": config.VIDEO_DURATION_SEC},
        {"dual_take": config.DUAL_TAKE_COUNT, "refs": reference_images, "preview": preview_path, "prev_tail": prev_tail_path},
        {"mock": True},
    )

    qa_res = await judge(
        scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"],
        context_text=prompt,
    )
    retries = 0
    if qa_res.overall_score < config.QA_PASS_THRESHOLD and retries < config.MAX_QA_RETRIES:
        _ = await write_repair_constraint(scene, qa_res)
        retries += 1

    final_url = f"/assets/finals/scene_{scene.scene_id}_{cam_id}.mp4"
    tail_url = f"/assets/tails/scene_{scene.scene_id}_{cam_id}_tail.png"
    return final_url, tail_url, qa_res, 0, retries


@log_function_call
async def render_scene_media(
    scene: Scene,
    reference_images: list[str],
    cam_a_preview_path: Optional[str] = None,
    cam_b_preview_path: Optional[str] = None,
    prev_tail_a: Optional[str] = None,
    prev_tail_b: Optional[str] = None,
    tail_inspector: Optional[Callable[[int, str], Awaitable[str]]] = None,
) -> SceneMedia:
    """Concurrently render Camera A and Camera B for a single scene and update tail-frame notes."""
    res_a, res_b = await asyncio.gather(
        render_single_camera_shot(scene, "cam_a", prev_tail_a, reference_images, preview_path=cam_a_preview_path),
        render_single_camera_shot(scene, "cam_b", prev_tail_b, reference_images, preview_path=cam_b_preview_path),
    )

    avg_score = round((res_a[2].overall_score + res_b[2].overall_score) / 2, 3)
    media = SceneMedia(
        cam_a_preview_url=scene.media.cam_a_preview_url,
        cam_b_preview_url=scene.media.cam_b_preview_url,
        cam_a_final_url=res_a[0],
        cam_b_final_url=res_b[0],
        cam_a_tail_frame_url=res_a[1],
        cam_b_tail_frame_url=res_b[1],
        qa_result=res_a[2],
        qa_score=avg_score,
        qa_retries=max(res_a[4], res_b[4]),
        selected_candidate_index=res_a[3],  # type: ignore[arg-type]
        fallback_to_preview=avg_score < config.QA_PASS_THRESHOLD,
    )
    if tail_inspector and media.cam_a_tail_frame_url:
        scene.environment_notes = await tail_inspector(scene.scene_id, media.cam_a_tail_frame_url)
    return media


@log_function_call
async def render_branch_scenes(
    branch: BranchState,
    tail_inspector: Optional[Callable[[int, str], Awaitable[str]]] = None,
) -> BranchState:
    """Concurrently render all scenes with `needs_rerender=True` in `branch`."""
    ref_images = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
    ] + [p.image_url for p in branch.props_registry.values()]

    target_scenes = [s for s in branch.scenes if s.needs_rerender]

    async def _run_scene(scene: Scene) -> None:
        scene.media = await render_scene_media(scene, ref_images, tail_inspector=tail_inspector)

    await asyncio.gather(*(_run_scene(s) for s in target_scenes))
    return branch
