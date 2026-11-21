"""
Unified Judge (Decisions API v1beta + Hydrogen Fallback) & Multimodal Hear/See Module (Owned by P1).

Feature & Use Cases:
- Implements `judge()` backed primarily by **Decisions API (`gemini-decision-1` via v1beta, ~0.64s)**
  with automatic fallback to **Gemini 4 Hydrogen structured output** when in mock mode or
  if Decisions API rejects an input:
  1. `check_contradiction()`: Verifies edit at scene k doesn't contradict 1..k-1.
  2. `filter_affected_scenes()`: Marks `needs_rerender` only on causally affected scenes.
  3. `classify_rewrite_intent()`: Choice question routing (`move_position` / `prop_change` / `new_prop` / `emotion_lighting` / `other`).
  4. `score_clip_alignment()`: Score question (1-5 levels) for Best-of-2 candidate selection.
  5. Dual-take video QA scoring with low-confidence detection (`confidence < 0.70`).
- Also hosts Hydrogen 3s `.webm` audio+gesture parsing (`parse_director_input`),
  corrective prompt writing (`write_repair_constraint`), and tail-frame inspection.
"""

from pathlib import Path
from typing import Any, Optional

from backend import config
from backend.director import prompts
from backend.director.genai_client import (
    build_decision_input,
    create_decision,
    generate_structured,
    generate_text,
    part_from_bytes,
    part_from_path,
)
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
    Evaluate yes/no questions using Decisions API (`gemini-decision-1` v1beta) with
    automatic fallback to Gemini 4 Hydrogen structured output.

    Flags low-confidence answers (`confidence < config.DECISION_LOW_CONFIDENCE_THRESHOLD`)
    inside item `reason` for UI / Neon escalation.
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
            config.MODEL_DECISION if config.USE_DECISIONS_API else config.MODEL_JUDGE,
            {"questions": questions, "context": context_text},
            {"mock": True},
            res,
        )
        return res

    resolved_paths = [p for s in (media_paths or []) if (p := _resolve_media_path(s)) is not None]

    # Primary fast path: Decisions API v1beta (~0.64s, calibrated probability)
    if config.USE_DECISIONS_API:
        try:
            q_map = {f"q{i + 1}": {"type": "noul", "instructions": q} for i, q in enumerate(questions)}
            input_payload = build_decision_input(context_text, resolved_paths)
            decision_res = await create_decision(input_payload, q_map)
            answers = getattr(decision_res, "answers", None) or {}
            items: list[JudgeItem] = []
            for i, q in enumerate(questions):
                ans = answers.get(f"q{i + 1}")
                p_true = float(getattr(ans, "noul", 0.5)) if ans is not None else 0.5
                conf = float(getattr(ans, "confidence", max(p_true, 1.0 - p_true))) if ans is not None else 0.5
                passed = p_true >= 0.5
                uncertain_note = " [LOW_CONFIDENCE: escalate]" if conf < config.DECISION_LOW_CONFIDENCE_THRESHOLD else ""
                items.append(
                    JudgeItem(
                        question=q,
                        passed=passed,
                        confidence=round(conf, 3),
                        reason=f"Decision-1 P(true)={p_true:.2f}{uncertain_note}",
                    )
                )
            overall = round(
                sum(it.confidence if it.passed else (1.0 - it.confidence) for it in items) / len(items),
                3,
            )
            return JudgeResult(overall_score=overall, all_passed=all(it.passed for it in items), items=items)
        except Exception as exc:
            log_genai_call(config.MODEL_DECISION, {"questions": questions}, {"fallback_to_hydrogen": str(exc)}, {})

    # Fallback path: Gemini 4 Hydrogen structured JSON output (~4s)
    numbered = "\n".join(f"{idx + 1}. {q}" for idx, q in enumerate(questions))
    prompt_text = f"Context / State:\n{context_text or 'See attached media.'}\n\nVerification Questions:\n{numbered}"
    contents: list[Any] = [prompt_text] + [part_from_path(p) for p in resolved_paths]

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
async def classify_rewrite_intent(instruction: str) -> dict[str, Any]:
    """Route a rewrite directive via Decisions API `choice` question (with `other` fallback)."""
    choices = ["move_position", "prop_change", "new_prop", "emotion_lighting", "other"]
    if config.DEMO_MODE or not config.GEMINI_API_KEY or not config.USE_DECISIONS_API:
        return {"category": "other", "confidence": 0.9}
    try:
        q_map = {
            "route": {
                "type": "choice",
                "instructions": "Classify the primary director rewrite intent.",
                "criteria": {
                    "move_position": "Changes character grid positions or movement on stage",
                    "prop_change": "Changes existing prop state (save vase, drop/put down umbrella)",
                    "new_prop": "Introduces a brand new physical or generated prop",
                    "emotion_lighting": "Changes mood, vocal emotion, or stage lighting",
                    "other": "Complex multi-aspect rewrite or anything else",
                },
            }
        }
        res = await create_decision(instruction, q_map)
        ans = (getattr(res, "answers", None) or {}).get("route")
        return {
            "category": getattr(ans, "choice", "other"),
            "confidence": float(getattr(ans, "confidence", 0.5)),
        }
    except Exception as exc:
        log_genai_call(config.MODEL_DECISION, {"instruction": instruction}, {"choice_fallback": str(exc)}, {})
        return {"category": "other", "confidence": 0.5}


@log_function_call
async def score_clip_alignment(
    target_description: str,
    media_paths: Optional[list[str]] = None,
) -> float:
    """Score candidate clip alignment (0..4 normalized to 0..1) via Decisions API `score` question."""
    if config.DEMO_MODE or not config.GEMINI_API_KEY or not config.USE_DECISIONS_API:
        return 0.90
    resolved = [p for s in (media_paths or []) if (p := _resolve_media_path(s)) is not None]
    try:
        q_map = {
            "fit": {
                "type": "score",
                "instructions": f"Rate how accurately the media matches: {target_description}",
                "criteria": [
                    "Completely mismatched characters or props",
                    "Major errors in positions or prop states",
                    "Partially matches but key detail wrong",
                    "Matches actors, props, and framing well",
                    "Exact match with zero continuity errors",
                ],
            }
        }
        res = await create_decision(build_decision_input(target_description, resolved), q_map)
        ans = (getattr(res, "answers", None) or {}).get("fit")
        raw_score = float(getattr(ans, "score", 3.0))
        return round(min(max(raw_score / 4.0, 0.0), 1.0), 3)
    except Exception as exc:
        log_genai_call(config.MODEL_DECISION, {"target": target_description}, {"score_fallback": str(exc)}, {})
        return 0.85


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
