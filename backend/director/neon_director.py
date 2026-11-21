"""
Gemini 4 Neon Stage Director Module (Owned by P1).

Feature & Use Cases:
- Uses `NEON_DIRECTOR_PROMPT` from `backend/director/prompts.py` to re-plan
  scenes `forked_from_scene..4` in one shot while leaving scenes
  `1..(forked_from_scene - 1)` completely untouched (`needs_rerender=False`).
- Preserves causal object permanence (e.g., broken vs. intact `crystal_vase`,
  live physical props like `star_token`) and maps vocal emotion / gestures to
  stage lighting and camera shake.
"""

from backend import config
from backend.director import prompts
from backend.logger import log_function_call, log_genai_call
from backend.schemas import DirectorInputParsed, PropRegistryItem, Scene


@log_function_call
async def plan_scenes(
    parsed_input: DirectorInputParsed,
    base_scenes: list[Scene],
    parent_branch_id: str,
    props_registry: dict[str, PropRegistryItem],
) -> list[Scene]:
    """
    Re-plan scenes from `parsed_input.target_scene_id` onward via Gemini 4 Neon.

    Scenes before `target_scene_id` keep `needs_rerender=False` and record
    `reused_from_branch=parent_branch_id`.
    """
    k = parsed_input.target_scene_id
    preserved: list[Scene] = []
    for s in base_scenes:
        if s.scene_id < k:
            copy_s = s.model_copy(deep=True)
            copy_s.needs_rerender = False
            copy_s.reused_from_branch = parent_branch_id
            preserved.append(copy_s)

    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        updated: list[Scene] = []
        for s in base_scenes:
            if s.scene_id >= k:
                copy_s = s.model_copy(deep=True)
                copy_s.needs_rerender = True
                copy_s.reused_from_branch = None
                copy_s.vocal_emotion = parsed_input.vocal_emotion
                copy_s.gesture_intent = parsed_input.gesture_intent
                copy_s.direction_hint = parsed_input.direction_hint
                if copy_s.scene_id == k:
                    copy_s.narration = f"[改写第 {k} 幕] {parsed_input.instruction}"
                else:
                    copy_s.narration = f"[因果延续] {copy_s.narration}"
                updated.append(copy_s)
        log_genai_call(
            config.MODEL_DIRECTOR,
            {"system": prompts.NEON_DIRECTOR_PROMPT[:200], "input": parsed_input.model_dump()},
            {"mock": True},
            {"updated_count": len(updated)},
        )
        return preserved + updated

    # TODO (P1 - T1): Call config.MODEL_DIRECTOR with prompts.NEON_DIRECTOR_PROMPT
    # and structured output matching `shared/schema.json` scenes array.
    updated = [s.model_copy(deep=True) for s in base_scenes if s.scene_id >= k]
    for s in updated:
        s.needs_rerender = True
        s.reused_from_branch = None
        if s.scene_id == k:
            s.narration = f"[Neon] {parsed_input.instruction}"
    log_genai_call(config.MODEL_DIRECTOR, parsed_input.model_dump(), {"fallback": True}, {"updated_count": len(updated)})
    return preserved + updated
