"""
Concurrent Dual-Take Omni Flash Video Renderer & QA Loop (Owned by P2).

Feature & Use Cases:
- Renders all `was_regenerated=True` scenes concurrently across Camera A and
  Camera B using `asyncio.gather` (`360p`, `4s` per clip for live demo speed).
- Runs 2 parallel takes per shot, extracts tail frames, evaluates both takes via
  `judge()`, keeps the higher-confidence take, and triggers up to
  `config.MAX_QA_RETRIES` corrective re-renders if both fail threshold.
"""

import asyncio
from typing import Awaitable, Callable, Optional

from backend import config
from backend.director.hydrogen_judge import judge, write_repair_constraint
from backend.logger import log_function_call, log_genai_call
from backend.schemas import BranchState, QAResult, SceneState


@log_function_call
async def render_single_camera_shot(
    scene: SceneState,
    cam_id: str,
    prev_tail_path: Optional[str],
    reference_images: list[str],
    extra_constraint: str = "",
) -> tuple[str, Optional[str], list[QAResult], float, int]:
    """
    Generate 2 parallel Omni Flash takes for one scene + camera, score via `judge()`,
    and return `(final_url, tail_frame_url, qa_results, best_score, retries)`.
    """
    # TODO (P2 - T0/T1): Benchmark single Omni Flash 360p/4s call & implement real dual-take call.
    prompt = f"Camera {cam_id}: {scene.narration}. Mood: {scene.vocal_emotion}. {extra_constraint}".strip()
    log_genai_call(
        config.MODEL_VIDEO,
        {"prompt": prompt, "resolution": config.VIDEO_RESOLUTION, "duration": config.VIDEO_DURATION_SEC},
        {"dual_take": config.DUAL_TAKE_COUNT, "refs": reference_images, "prev_tail": prev_tail_path},
        {"mock": True},
    )

    qa_results = await judge(
        scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"],
        context_text=prompt,
    )
    score = sum(r.confidence for r in qa_results if r.passed) / max(len(qa_results), 1)
    retries = 0
    if score < config.QA_PASS_THRESHOLD and retries < config.MAX_QA_RETRIES:
        _ = await write_repair_constraint(scene, qa_results)
        retries += 1

    final_url = f"/assets/finals/scene_{scene.scene_id}_{cam_id}.mp4"
    tail_url = f"/assets/tails/scene_{scene.scene_id}_tail.png"
    return final_url, tail_url, qa_results, round(score, 3), retries


@log_function_call
async def render_branch_scenes(
    branch: BranchState,
    tail_inspector: Optional[Callable[[str], Awaitable[str]]] = None,
) -> BranchState:
    """Concurrently render all scenes marked `was_regenerated=True` in `branch`."""
    ref_images = [a.portrait_url for a in list(branch.actors_registry.values())[: config.MAX_ACTOR_REFERENCE_IMAGES]]
    ref_images += [p.image_url for p in branch.props_registry.values()]

    target_scenes = [s for s in branch.scenes if s.was_regenerated]

    async def _render_scene(scene: SceneState) -> None:
        cam_a_task = render_single_camera_shot(scene, "cam_a", None, ref_images)
        cam_b_task = render_single_camera_shot(scene, "cam_b", None, ref_images)
        res_a, res_b = await asyncio.gather(cam_a_task, cam_b_task)

        scene.media.cam_a_final_url = res_a[0]
        scene.media.cam_b_final_url = res_b[0]
        scene.media.tail_frame_url = res_a[1]
        scene.media.qa_results = res_a[2]
        scene.media.qa_score = round((res_a[3] + res_b[3]) / 2, 3)
        scene.media.qa_retries = max(res_a[4], res_b[4])

        if tail_inspector and scene.media.tail_frame_url:
            scene.environment_notes = await tail_inspector(scene.media.tail_frame_url)

    await asyncio.gather(*(_render_scene(s) for s in target_scenes))
    return branch
