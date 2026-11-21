"""
Unified Judge & Hydrogen Multimodal Hear/See Reasoning Module (Owned by P1).

Feature & Use Cases:
- Single `judge()` abstraction backed by Gemini 4 Hydrogen structured output
  (swappable to Decisions API when `config.USE_DECISIONS_API` is enabled):
  1. `check_contradiction()`: Verifies edit at scene k doesn't contradict 1..k-1.
  2. `filter_affected_scenes()`: Marks `needs_rerender` only on causally affected scenes.
  3. Dual-take video QA scoring & candidate selection (`selected_candidate_index`).
- Also implements Hydrogen 3s `.webm` audio+gesture parsing (`parse_director_input`),
  corrective prompt writing (`write_repair_constraint`), and tail-frame inspection
  (`inspect_tail_frame`).
"""

from pathlib import Path
from typing import Any, Optional

from backend import config
from backend.director import prompts
from backend.director.genai_client import generate_structured, generate_text, part_from_bytes, part_from_path
from backend.logger import log_function_call, log_genai_call
from backend.schemas import DirectorInputParsed, JudgeItem, JudgeResult, Scene


def _resolve_media_path(media_url_or_path: str) -> Optional[Path]:
    """Resolve `/assets/...`, `/shared-assets/...`, or filesystem path to an existing Path."""
    p = Path(media_url_or_path)
    if p.exists():
        return p
    if media_url_or_path.startswith("/assets/"):
        candidate = config.ASSETS_DIR / media_url_or_path.removeprefix("/assets/")
        if candidate.exists():
            return candidate
    if media_url_or_path.startswith("/shared-assets/"):
        candidate = config.SHARED_ASSETS_DIR / media_url_or_path.removeprefix("/shared-assets/")
        if candidate.exists():
            return candidate
    return None


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
    if not questions:
        return JudgeResult(overall_score=1.0, all_passed=True, items=[])

    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        items = [
            JudgeItem(question=q, passed=True, confidence=0.93, reason="Mock verified.")
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

    numbered = "\n".join(f"{idx + 1}. {q}" for idx, q in enumerate(questions))
    prompt_text = f"Context / State:\n{context_text or 'See attached media.'}\n\nVerification Questions:\n{numbered}"
    contents: list[Any] = [prompt_text]
    for path_str in media_paths or []:
        resolved = _resolve_media_path(path_str)
        if resolved:
            contents.append(part_from_path(resolved))

    res = await generate_structured(
        model=config.MODEL_JUDGE,
        system_instruction=prompts.UNIFIED_JUDGE_PROMPT,
        contents=contents,
        response_schema=JudgeResult,
        temperature=0.1,
    )
    if res.items:
        res.overall_score = round(
            sum(item.confidence if item.passed else (1.0 - item.confidence) for item in res.items) / len(res.items),
            3,
        )
        res.all_passed = all(item.passed for item in res.items)
    return res


@log_function_call
async def parse_director_input(
    clip_bytes: Optional[bytes] = None,
    clip_mime_type: str = "video/webm",
    text_fallback: Optional[str] = None,
    target_scene_id: int = 2,
) -> DirectorInputParsed:
    """Parse a 3s webcam+mic `.webm` clip (or text fallback) via Hydrogen Hear/See Agent."""
    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        if clip_bytes:
            log_genai_call(
                config.MODEL_JUDGE,
                {"system": prompts.HYDROGEN_HEAR_SEE_PROMPT[:160], "clip_bytes_len": len(clip_bytes)},
                {"mock": True},
                {},
            )
        return DirectorInputParsed(
            target_scene_id=target_scene_id,
            instruction=text_fallback or "温柔轻声安抚诺克斯，把星辰信物放在桌上接住花瓶",
            vocal_emotion="gentle_whisper" if clip_bytes else "neutral",
            gesture_intent="beckon_soothe" if clip_bytes else "none",
            direction_hint="center" if clip_bytes else "none",
            has_physical_prop=False,
        )

    contents: list[Any] = [
        f"Target scene hint: Scene {target_scene_id}. Text hint (if provided): {text_fallback or 'None'}."
    ]
    if clip_bytes:
        contents.append(part_from_bytes(clip_bytes, clip_mime_type))

    return await generate_structured(
        model=config.MODEL_JUDGE,
        system_instruction=prompts.HYDROGEN_HEAR_SEE_PROMPT,
        contents=contents,
        response_schema=DirectorInputParsed,
        temperature=0.2,
    )


@log_function_call
async def check_contradiction(edit_instruction: str, prior_scenes: list[Scene]) -> tuple[bool, str]:
    """Check if `edit_instruction` contradicts facts already established before scene k."""
    if not prior_scenes:
        return False, ""
    prior_summary = " | ".join(f"Scene {s.scene_id}: {s.narration}" for s in prior_scenes)
    questions = prompts.build_contradiction_questions(prior_summary, edit_instruction)
    res = await judge(questions, context_text=prior_summary)
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
    later = [s for s in scenes if s.scene_id > forked_from_scene]
    if not later:
        return affected

    questions: list[str] = []
    for s in later:
        questions.extend(prompts.build_affected_scene_questions(s.scene_id, s.narration, rewrite_summary))
    res = await judge(questions, context_text=rewrite_summary)
    for scene, item in zip(later, res.items):
        if item.passed:
            affected.append(scene.scene_id)
    return affected


@log_function_call
async def inspect_tail_frame(scene_id: int, tail_image_path: str) -> str:
    """Inspect a rendered video's tail frame via Hydrogen and return emergent environment notes."""
    prompt = prompts.HYDROGEN_TAIL_OBSERVER_PROMPT.format(scene_id=scene_id)
    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        log_genai_call(config.MODEL_JUDGE, {"prompt": prompt, "tail_image_path": tail_image_path}, {"mock": True}, "warm_starlight_reflection")
        return "warm_starlight_reflection"

    resolved = _resolve_media_path(tail_image_path)
    if not resolved:
        return "glass_conservatory_night"
    return await generate_text(
        model=config.MODEL_JUDGE,
        system_instruction="Output ONLY 5-12 snake_case environment keywords, no punctuation or explanation.",
        contents=[prompt, part_from_path(resolved)],
        temperature=0.2,
    )


@log_function_call
async def write_repair_constraint(scene: Scene, judge_result: JudgeResult) -> str:
    """Generate a targeted visual correction constraint when both Omni Flash takes fail `judge()`."""
    failed_items = "; ".join(f"{i.question} ({i.reason})" for i in judge_result.items if not i.passed)
    prompt = prompts.HYDROGEN_REPAIR_PROMPT.format(
        failed_items=failed_items,
        scene_json=scene.model_dump_json(),
    )
    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        constraint = f"Keep Lia and Nyx strictly on grid; fix: {failed_items}".strip()
        log_genai_call(config.MODEL_JUDGE, prompt, {"mock": True}, constraint)
        return constraint

    return await generate_text(
        model=config.MODEL_JUDGE,
        system_instruction="You write ultra-concise <=45-word visual repair prompts for video editing.",
        contents=[prompt],
        temperature=0.2,
    )
