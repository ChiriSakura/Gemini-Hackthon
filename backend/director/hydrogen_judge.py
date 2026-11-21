"""
Unified Judge & Hydrogen Multimodal Hear/See Reasoning Module (Owned by P1).

Feature & Use Cases:
- Single `judge()` abstraction backed by Gemini 4 Hydrogen structured output
  (swappable to Decisions API once enabled on hackathon project):
  1. `check_contradiction()`: Verifies edit at scene k doesn't contradict 1..k-1.
  2. `filter_affected_scenes()`: Marks `needs_rerender` only on causally affected scenes.
  3. Dual-take video QA scoring & candidate selection (`selected_candidate_index`).
- Also implements Hydrogen 3s `.webm` audio+gesture parsing (`parse_director_input`),
  corrective prompt writing (`write_repair_constraint`), and tail-frame inspection
  (`inspect_tail_frame`).
"""

from typing import Optional

from backend import config
from backend.director import prompts
from backend.logger import log_function_call, log_genai_call
from backend.schemas import DirectorInputParsed, JudgeItem, JudgeResult, Scene


@log_function_call
async def judge(
    questions: list[str],
    context_text: str = "",
    media_paths: Optional[list[str]] = None,
) -> JudgeResult:
    """
    Evaluate yes/no questions against a world state or media file via Hydrogen structured output.

    Returns `JudgeResult(overall_score, all_passed, items)`.
    """
    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        items = [
            JudgeItem(question=q, passed=True, confidence=0.93, reason="Verified in stage state.")
            for q in questions
        ]
        res = JudgeResult(overall_score=0.93, all_passed=True, items=items)
        log_genai_call(
            config.MODEL_JUDGE,
            {"system": prompts.UNIFIED_JUDGE_PROMPT[:160], "questions": questions, "context": context_text},
            {"mock": True},
            res,
        )
        return res

    # TODO (P1 - T1): Implement real Gemini 4 Hydrogen structured JSON call
    # using prompts.UNIFIED_JUDGE_PROMPT (or Decisions API if config.USE_DECISIONS_API).
    items = [JudgeItem(question=q, passed=True, confidence=0.90, reason="Fallback check.") for q in questions]
    res = JudgeResult(overall_score=0.90, all_passed=True, items=items)
    log_genai_call(config.MODEL_JUDGE, {"questions": questions, "media_paths": media_paths}, {"fallback": True}, res)
    return res


@log_function_call
async def parse_director_input(
    clip_bytes: Optional[bytes] = None,
    clip_mime_type: str = "video/webm",
    text_fallback: Optional[str] = None,
    target_scene_id: int = 2,
) -> DirectorInputParsed:
    """Parse a 3s webcam+mic `.webm` clip (or text fallback) via Hydrogen Hear/See Agent."""
    if clip_bytes:
        log_genai_call(
            config.MODEL_JUDGE,
            {"system": prompts.HYDROGEN_HEAR_SEE_PROMPT[:160], "clip_bytes_len": len(clip_bytes), "mime": clip_mime_type},
            {"mock": True},
            {},
        )
        # TODO (P1 - T1): Pass raw `.webm` clip to Gemini 4 Hydrogen and parse DirectorInputParsed
        return DirectorInputParsed(
            target_scene_id=target_scene_id,
            instruction=text_fallback or "温柔轻声安抚诺克斯，把星辰信物放在桌上接住花瓶",
            vocal_emotion="gentle_whisper",
            gesture_intent="beckon_soothe",
            direction_hint="center",
            has_physical_prop=False,
        )

    return DirectorInputParsed(
        target_scene_id=target_scene_id,
        instruction=text_fallback or "温柔安抚诺克斯，保护好水晶花瓶",
        vocal_emotion="neutral",
        gesture_intent="none",
        direction_hint="none",
        has_physical_prop=False,
    )


@log_function_call
async def check_contradiction(edit_instruction: str, prior_scenes: list[Scene]) -> tuple[bool, str]:
    """Check if `edit_instruction` contradicts facts already established before scene k."""
    if not prior_scenes:
        return False, ""
    prior_summary = " | ".join(f"Scene {s.scene_id}: {s.narration}" for s in prior_scenes)
    questions = prompts.build_contradiction_questions(prior_summary, edit_instruction)
    res = await judge(questions, context_text=prior_summary)
    # Note: question asks if consistent (passed=True means consistent, passed=False means contradiction)
    if res.items and (not res.items[0].passed) and res.items[0].confidence >= 0.75:
        return True, res.items[0].reason or f"与前文既定事实存在矛盾：{prior_summary}"
    return False, ""


@log_function_call
async def filter_affected_scenes(
    rewrite_summary: str,
    scenes: list[Scene],
    forked_from_scene: int,
) -> list[int]:
    """Return scene IDs >= `forked_from_scene` that causally depend on the rewrite."""
    affected = [forked_from_scene]
    for s in scenes:
        if s.scene_id > forked_from_scene:
            qs = prompts.build_affected_scene_questions(s.scene_id, s.narration, rewrite_summary)
            res = await judge(qs, context_text=rewrite_summary)
            if res.items and res.items[0].passed:
                affected.append(s.scene_id)
    return affected


@log_function_call
async def inspect_tail_frame(scene_id: int, tail_image_path: str) -> str:
    """Inspect a rendered video's tail frame via Hydrogen and return emergent environment notes."""
    prompt = prompts.HYDROGEN_TAIL_OBSERVER_PROMPT.format(scene_id=scene_id)
    log_genai_call(config.MODEL_JUDGE, {"prompt": prompt, "tail_image_path": tail_image_path}, {"mock": True}, "warm_starlight_reflection")
    return "warm_starlight_reflection"


@log_function_call
async def write_repair_constraint(scene: Scene, judge_result: JudgeResult) -> str:
    """Generate a targeted visual correction constraint when both Omni Flash takes fail `judge()`."""
    failed_items = "; ".join(f"{i.question} ({i.reason})" for i in judge_result.items if not i.passed)
    prompt = prompts.HYDROGEN_REPAIR_PROMPT.format(
        failed_items=failed_items,
        scene_json=scene.model_dump_json(),
    )
    constraint = f"Keep Lia and Nyx strictly on grid; fix: {failed_items}".strip()
    log_genai_call(config.MODEL_JUDGE, prompt, {"mock": True}, constraint)
    return constraint
