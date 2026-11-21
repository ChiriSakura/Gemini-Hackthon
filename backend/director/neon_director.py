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
from backend.schemas import DirectorInputParsed, PropRegistryItem, Scene, SceneMedia, StagePropState


class _NeonScenesResponse(BaseModel):
    """Structured wrapper for Gemini 4 Neon rewritten scenes `k..4`."""

    scenes: list[Scene]


@log_function_call
def validate_scene_invariants(scenes: list[Scene]) -> list[str]:
    """
    Hard structural invariant checks required by Proposal Section 10 ("状态崩"):
    1. All actor and stage-prop grid coords stay strictly inside the 3x3 stage `[0..2, 0..2]`.
    2. No duplicate actor IDs in a single scene.
    3. Every prop has at most ONE holder per scene and is not simultaneously loose in `stage_props`.
    4. Object permanence: once `crystal_vase` becomes `broken` in scene `j`, it cannot silently
       revert to `intact` in a later scene `j+1`.
    """
    errors: list[str] = []
    vase_broken_at: int | None = None
    for s in sorted(scenes, key=lambda sc: sc.scene_id):
        seen_actors: set[str] = set()
        held_props: dict[str, str] = {}
        for actor in s.actors:
            if actor.id in seen_actors:
                errors.append(f"S{s.scene_id}: duplicate actor '{actor.id}'")
            seen_actors.add(actor.id)
            if len(actor.grid) != 2 or not (0.0 <= actor.grid[0] <= 2.0 and 0.0 <= actor.grid[1] <= 2.0):
                errors.append(f"S{s.scene_id}: actor '{actor.id}' grid {actor.grid} outside [0..2, 0..2]")
            if actor.holding_prop:
                if actor.holding_prop in held_props:
                    errors.append(
                        f"S{s.scene_id}: prop '{actor.holding_prop}' held by both '{held_props[actor.holding_prop]}' and '{actor.id}'"
                    )
                else:
                    held_props[actor.holding_prop] = actor.id

        for prop in s.stage_props:
            if len(prop.grid) != 2 or not (0.0 <= prop.grid[0] <= 2.0 and 0.0 <= prop.grid[1] <= 2.0):
                errors.append(f"S{s.scene_id}: prop '{prop.id}' grid {prop.grid} outside [0..2, 0..2]")
            if prop.id in held_props and prop.state != "held":
                errors.append(
                    f"S{s.scene_id}: prop '{prop.id}' is simultaneously held by '{held_props[prop.id]}' and placed on stage"
                )
            if prop.id == "crystal_vase":
                if prop.state == "broken":
                    if vase_broken_at is None:
                        vase_broken_at = s.scene_id
                elif vase_broken_at is not None and s.scene_id > vase_broken_at and prop.state == "intact":
                    errors.append(
                        f"S{s.scene_id}: crystal_vase broke in S{vase_broken_at} and cannot revert to 'intact'"
                    )
    return errors


def _sanitize_scenes_invariants(scenes: list[Scene]) -> list[Scene]:
    """Deterministically clamp out-of-bounds coords and deduplicate prop holders as final safety net."""
    vase_broken_at: int | None = None
    for s in sorted(scenes, key=lambda sc: sc.scene_id):
        dedup_actors = []
        seen_actors: set[str] = set()
        held_props: set[str] = set()
        for actor in s.actors:
            if actor.id in seen_actors:
                continue
            seen_actors.add(actor.id)
            actor.grid = [
                round(min(max(float(actor.grid[0]), 0.0), 2.0), 2),
                round(min(max(float(actor.grid[1]), 0.0), 2.0), 2),
            ]
            if actor.holding_prop:
                if actor.holding_prop in held_props:
                    actor.holding_prop = None
                else:
                    held_props.add(actor.holding_prop)
            dedup_actors.append(actor)
        s.actors = dedup_actors

        clean_props = []
        for prop in s.stage_props:
            prop.grid = [
                round(min(max(float(prop.grid[0]), 0.0), 2.0), 2),
                round(min(max(float(prop.grid[1]), 0.0), 2.0), 2),
            ]
            if prop.id in held_props:
                continue
            if prop.id == "crystal_vase":
                if prop.state == "broken" and vase_broken_at is None:
                    vase_broken_at = s.scene_id
                elif vase_broken_at is not None and s.scene_id > vase_broken_at and prop.state == "intact":
                    prop.state = "broken"
                    prop.on_floor = True
            clean_props.append(prop)
        s.stage_props = clean_props
    return scenes


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
    `reused_from_branch=parent_branch_id`. Runs hard structural invariant
    checks (grid bounds, single prop holder, object permanence) with 1x
    automatic retry per Proposal Section 10.
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
                copy_s.media = SceneMedia()
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
        return _sanitize_scenes_invariants(preserved + updated)

    rules_block = ""
    if learned_rules:
        rules_block = "\nLearned continuity rules from prior QA:\n- " + "\n- ".join(learned_rules)

    base_user_prompt = (
        f"forked_from_scene = {k}\n"
        f"parent_branch_id = {parent_branch_id}\n"
        f"props_registry = {json.dumps({pid: p.model_dump() for pid, p in props_registry.items()}, ensure_ascii=False)}\n"
        f"parsed_directive = {parsed_input.model_dump_json()}\n"
        f"existing_scenes = {json.dumps([s.model_dump() for s in base_scenes], ensure_ascii=False)}\n"
        f"{rules_block}\n\n"
        f"Output ONLY the rewritten scenes with scene_id in {k}..{len(base_scenes)} inside `scenes`."
    )

    last_combined: list[Scene] = []
    invariant_errors: list[str] = []
    for attempt in range(2):
        prompt_text = base_user_prompt
        if invariant_errors:
            prompt_text += (
                "\n\nFIX THESE HARD STRUCTURAL INVARIANT VIOLATIONS FROM YOUR PREVIOUS DRAFT:\n- "
                + "\n- ".join(invariant_errors)
            )
        resp = await generate_structured(
            model=config.MODEL_DIRECTOR,
            system_instruction=prompts.NEON_DIRECTOR_PROMPT,
            contents=[prompt_text],
            response_schema=_NeonScenesResponse,
            temperature=0.25 if attempt == 0 else 0.1,
            thinking_level="LOW",
        )
        rewritten_by_id = {s.scene_id: s for s in resp.scenes if s.scene_id >= k}
        # R-17 backfill: ensure Neon cannot silently drop scenes k..N
        rewritten: list[Scene] = []
        for base_s in base_scenes:
            if base_s.scene_id < k:
                continue
            sc = rewritten_by_id.get(base_s.scene_id) or base_s.model_copy(deep=True)
            sc.needs_rerender = True
            sc.reused_from_branch = None
            sc.media = SceneMedia()
            for actor in sc.actors:
                if not actor.asset_url:
                    actor.asset_url = f"/assets/actors/{actor.id}.png"
            rewritten.append(sc)
        last_combined = [p.model_copy(deep=True) for p in preserved] + rewritten
        invariant_errors = validate_scene_invariants(last_combined)
        if not invariant_errors:
            return last_combined
        log_genai_call(
            config.MODEL_DIRECTOR,
            {"attempt": attempt + 1, "invariant_violations": invariant_errors},
            {"action": "retry_neon" if attempt == 0 else "sanitize_fallback"},
            {},
        )

    return _sanitize_scenes_invariants(last_combined)


@log_function_call
async def extend_single_scene(
    parsed_input: DirectorInputParsed,
    prev_scene: Scene,
    next_scene_id: int,
    props_registry: dict[str, PropRegistryItem],
    learned_rules: list[str] | None = None,
    thrown_prop_id: str | None = None,
) -> Scene:
    """
    Generate ONE next scene (`Scene N+1`) from a frozen sculpture snapshot in <3s (`thinking_level="LOW"`).

    Avoids multi-scene planning overhead (fixes R-41 latency) and supports unbounded open-ended
    storylines (`Design_Infinite_Sculpture.md`).
    """
    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        next_scene = prev_scene.model_copy(deep=True)
        next_scene.scene_id = next_scene_id
        next_scene.parent_scene_id = prev_scene.scene_id
        next_scene.end_freeze_mode = "sculpture"
        next_scene.needs_rerender = True
        next_scene.reused_from_branch = None
        next_scene.media = SceneMedia()
        next_scene.title = f"Act {next_scene_id} · Living Sculpture Twist"
        next_scene.narration = f"[Continued from S{prev_scene.scene_id}] {parsed_input.instruction}"
        next_scene.vocal_emotion = parsed_input.vocal_emotion
        next_scene.gesture_intent = parsed_input.gesture_intent
        next_scene.direction_hint = parsed_input.direction_hint
        if thrown_prop_id and not any(p.id == thrown_prop_id for p in next_scene.stage_props):
            next_scene.stage_props.append(
                StagePropState(
                    id=thrown_prop_id,
                    grid=[1.15, 1.1],
                    on_floor=False,
                    state="glowing",
                    entry_motion="throw_arc",
                    entry_origin=[1.0, 2.0, 2.4],
                )
            )
        return _sanitize_scenes_invariants([prev_scene.model_copy(deep=True), next_scene])[-1]

    rules_block = ""
    if learned_rules:
        rules_block = "\nGeneral stage continuity rules:\n- " + "\n- ".join(learned_rules)

    user_prompt = (
        f"next_scene_id = {next_scene_id}\n"
        f"thrown_prop_id = {thrown_prop_id or 'None'}\n"
        f"props_registry = {json.dumps({pid: p.model_dump() for pid, p in props_registry.items()}, ensure_ascii=False)}\n"
        f"parsed_directive = {parsed_input.model_dump_json()}\n"
        f"prev_scene = {prev_scene.model_dump_json()}\n"
        f"{rules_block}\n\n"
        f"Output ONLY a single Scene object with scene_id={next_scene_id} inside `scenes`."
    )
    resp = await generate_structured(
        model=config.MODEL_DIRECTOR,
        system_instruction=prompts.NEON_EXTEND_SCENE_PROMPT,
        contents=[user_prompt],
        response_schema=_NeonScenesResponse,
        temperature=0.25,
        thinking_level="LOW",
    )
    candidate = next((s for s in resp.scenes), None)
    if candidate is None:
        candidate = prev_scene.model_copy(deep=True)
        candidate.narration = parsed_input.instruction
        candidate.title = f"Act {next_scene_id} · Encore"

    candidate.scene_id = next_scene_id
    candidate.parent_scene_id = prev_scene.scene_id
    candidate.end_freeze_mode = "sculpture"
    candidate.needs_rerender = True
    candidate.reused_from_branch = None
    candidate.media = SceneMedia()
    for actor in candidate.actors:
        if not actor.asset_url:
            actor.asset_url = f"/assets/actors/{actor.id}.png"
    if thrown_prop_id and not any(p.id == thrown_prop_id for p in candidate.stage_props) and not any(
        a.holding_prop == thrown_prop_id for a in candidate.actors
    ):
        candidate.stage_props.append(
            StagePropState(
                id=thrown_prop_id,
                grid=[1.15, 1.1],
                on_floor=False,
                state="glowing",
                entry_motion="throw_arc",
                entry_origin=[1.0, 2.0, 2.4],
            )
        )
    return _sanitize_scenes_invariants([prev_scene.model_copy(deep=True), candidate])[-1]
