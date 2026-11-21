"""
Gemini 4 Neon Stage Director Module (Owned by P1).

Feature & Use Cases:
- Takes a rewrite instruction + existing branch scenes + props registry and
  deterministically re-plans scenes `forked_from_scene..N` in a single Neon call
  while preserving scenes `1..forked_from_scene-1` verbatim.
- Outputs structured `SceneState` objects conforming to `schema.json`.
"""

from backend import config
from backend.logger import log_function_call, log_genai_call
from backend.schemas import PropAsset, SceneState


@log_function_call
async def plan_scenes(
    instruction: str,
    base_scenes: list[SceneState],
    forked_from_scene: int,
    props_registry: dict[str, PropAsset],
) -> list[SceneState]:
    """
    Re-plan scenes from `forked_from_scene` onward using Gemini 4 Neon.

    Preserves all scenes prior to `forked_from_scene` unchanged, and updates
    actor grid positions, held/floor props, lighting, narration, and QA checks
    for scenes `>= forked_from_scene`.
    """
    preserved = [s.model_copy(deep=True) for s in base_scenes if s.scene_id < forked_from_scene]
    for s in preserved:
        s.was_regenerated = False

    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        # Mock deterministic rewrite for immediate P2/P3 parallel testing
        updated = []
        for s in base_scenes:
            if s.scene_id >= forked_from_scene:
                copy_s = s.model_copy(deep=True)
                copy_s.was_regenerated = True
                if copy_s.scene_id == forked_from_scene:
                    copy_s.narration = f"[改写] {instruction}"
                else:
                    copy_s.narration = f"[受改写影响续写] {copy_s.narration}"
                updated.append(copy_s)
        log_genai_call(config.MODEL_DIRECTOR, instruction, {"mock": True}, {"updated_count": len(updated)})
        return preserved + updated

    # TODO (P1 - T1): Replace with live google-genai call to config.MODEL_DIRECTOR
    # returning structured SceneState list, logged via log_genai_call().
    updated = [s.model_copy(deep=True) for s in base_scenes if s.scene_id >= forked_from_scene]
    for s in updated:
        s.was_regenerated = True
        if s.scene_id == forked_from_scene:
            s.narration = f"[Neon] {instruction}"
    log_genai_call(config.MODEL_DIRECTOR, instruction, {"fallback": True}, {"updated_count": len(updated)})
    return preserved + updated
