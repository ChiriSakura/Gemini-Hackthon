"""
Gemini 4 Neon Stage Director Module (Owned by P1).

Feature & Use Cases:
- Uses `NEON_DIRECTOR_PROMPT` from `backend/director/prompts.py` to re-plan
  scenes `forked_from_scene..4` in one shot via Gemini 4 Neon structured JSON
  output while leaving scenes `1..(forked_from_scene - 1)` untouched.
- Preserves causal object permanence (e.g., broken vs. intact `crystal_vase`,
  live physical props like `star_token`) and maps vocal emotion / gestures to
  stage lighting and camera shake.
"""

import json
from pydantic import BaseModel

from backend import config
from backend.director import prompts
from backend.director.genai_client import generate_structured
from backend.logger import log_function_call, log_genai_call
from backend.schemas import DirectorInputParsed, PropRegistryItem, Scene


class _NeonScenesResponse(BaseModel):
    """Structured wrapper for Gemini 4 Neon rewritten scenes `k..4`."""

    scenes: list[Scene]


@log_function_call
async def plan_scenes(
    parsed_input: DirectorInputParsed,
    base_scenes: list[Scene],
    parent_branch_id: str,
    props_registry: dict[str, PropRegistryItem],
    learned_rules: list[str] | None = None,
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
                    copy_s.narration = f"[Rewritten Scene {k}] {parsed_input.instruction}"
                else:
                    copy_s.narration = f"[Causal Continuation] {copy_s.narration}"
                updated.append(copy_s)
        log_genai_call(
            config.MODEL_DIRECTOR,
            {"system": prompts.NEON_DIRECTOR_PROMPT[:200], "input": parsed_input.model_dump()},
            {"mock": True},
            {"updated_count": len(updated)},
        )
        return preserved + updated

    rules_block = ""
    if learned_rules:
        rules_block = "\nLearned continuity rules from prior QA:\n- " + "\n- ".join(learned_rules)

    user_prompt = (
        f"forked_from_scene = {k}\n"
        f"parent_branch_id = {parent_branch_id}\n"
        f"props_registry = {json.dumps({pid: p.model_dump() for pid, p in props_registry.items()}, ensure_ascii=False)}\n"
        f"parsed_directive = {parsed_input.model_dump_json()}\n"
        f"existing_scenes = {json.dumps([s.model_dump() for s in base_scenes], ensure_ascii=False)}\n"
        f"{rules_block}\n\n"
        f"Output ONLY the rewritten scenes with scene_id in {k}..{len(base_scenes)} inside `scenes`."
    )
    resp = await generate_structured(
        model=config.MODEL_DIRECTOR,
        system_instruction=prompts.NEON_DIRECTOR_PROMPT,
        contents=[user_prompt],
        response_schema=_NeonScenesResponse,
        temperature=0.25,
    )
    rewritten = [s for s in resp.scenes if s.scene_id >= k]
    for s in rewritten:
        s.needs_rerender = True
        s.reused_from_branch = None
        for actor in s.actors:
            if not actor.asset_url:
                actor.asset_url = f"/assets/actors/{actor.id}.png"
    return preserved + rewritten
