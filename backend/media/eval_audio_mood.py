"""
W1-T6E: Multimodal Audio-Mood Verification via `models/gemini-decision-1` (Owned by P2).

Feature & Use Cases:
- Feeds cached Omni Flash MP4s (native video + audio tokens) directly into
  `models/gemini-decision-1` (`v1beta`) to verify that each clip's audio & mood accurately
  matches `scene.audio_mood` AND sharply discriminates stormy/tense BE clips from
  warm/hopeful HE & Starlight clips (both positive match checks and negative cross-mood
  rejection checks).
- Saves full calibrated probabilities & latency stats to `backend/assets/eval_audio_mood.json`.
"""

import asyncio
import json
import time
from pathlib import Path

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.logger import log_function_call


AUDIO_EVAL_CASES = [
    {
        "case_id": "be_s2_storm_positive",
        "clip_url": "/assets/finals/scene_2_cam_a.mp4",
        "branch": "branch_v1",
        "scene_id": 2,
        "expected": True,
        "question": "Does this video's audio and atmosphere convey a tense stormy rainy night with dramatic/startling mood (rather than cheerful warm celebratory chimes)?",
    },
    {
        "case_id": "be_s2_warm_negative",
        "clip_url": "/assets/finals/scene_2_cam_a.mp4",
        "branch": "branch_v1",
        "scene_id": 2,
        "expected": False,
        "question": "Does this video's audio and atmosphere convey warm magical golden snowfall with gentle peaceful celebratory chimes?",
    },
    {
        "case_id": "he_s4_warm_positive",
        "clip_url": "/assets/finals/branch_v2_scene_4_cam_a.mp4",
        "branch": "branch_v2_cached",
        "scene_id": 4,
        "expected": True,
        "question": "Does this video's audio and atmosphere convey a warm, gentle, magical nighttime finale with soft glowing starlight/snow mood (rather than violent storm chaos)?",
    },
    {
        "case_id": "he_s4_storm_negative",
        "clip_url": "/assets/finals/branch_v2_scene_4_cam_a.mp4",
        "branch": "branch_v2_cached",
        "scene_id": 4,
        "expected": False,
        "question": "Does this video's audio and atmosphere depict a harsh thunderclap storm and shattering glass tragedy?",
    },
    {
        "case_id": "be_s4_cold_regret_positive",
        "clip_url": "/assets/finals/scene_4_cam_a.mp4",
        "branch": "branch_v1",
        "scene_id": 4,
        "expected": True,
        "question": "Does this video's audio and atmosphere convey a somber, quiet, cold rainy night after an accident (melancholic tone)?",
    },
    {
        "case_id": "v3_s4_starlight_positive",
        "clip_url": "/assets/finals/branch_v3_scene_4_cam_a.mp4",
        "branch": "branch_v3_starlight_cached",
        "scene_id": 4,
        "expected": True,
        "question": "Does this video's audio and atmosphere convey a magical, serene, warm starry-night conclusion inside the conservatory?",
    },
]


async def _eval_case(case: dict) -> dict:
    """Run one calibrated Decisions API v1beta check on a native MP4 file."""
    mp4_path = config.ASSETS_DIR / case["clip_url"].removeprefix("/assets/")
    assert mp4_path.is_file(), f"Missing clip: {mp4_path}"
    t0 = time.perf_counter()
    payload = build_decision_input(
        f"Evaluate BOTH the native audio soundtrack/SFX and visual atmosphere of {mp4_path.name}.",
        [mp4_path],
    )
    res = await create_decision(
        payload,
        {"audio_mood_match": {"type": "noul", "instructions": case["question"]}},
    )
    elapsed = round(time.perf_counter() - t0, 2)
    ans = (getattr(res, "answers", None) or {}).get("audio_mood_match")
    p_true = round(float(getattr(ans, "noul", 0.5)), 4) if ans is not None else 0.5
    predicted = p_true >= 0.5
    correct = predicted == case["expected"]
    return {
        "case_id": case["case_id"],
        "clip_url": case["clip_url"],
        "branch": case["branch"],
        "scene_id": case["scene_id"],
        "expected": case["expected"],
        "predicted": predicted,
        "p_true": p_true,
        "correct": correct,
        "latency_sec": elapsed,
    }


@log_function_call
async def run_audio_mood_eval() -> dict:
    """Run all 6 native MP4 audio-mood verification cases in parallel via Decisions API."""
    results = list(await asyncio.gather(*(_eval_case(c) for c in AUDIO_EVAL_CASES)))
    n_total = len(results)
    n_correct = sum(1 for r in results if r["correct"])
    pos_cases = [r for r in results if r["expected"] is True]
    neg_cases = [r for r in results if r["expected"] is False]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": "models/gemini-decision-1",
        "api_version": "v1beta",
        "input_modality": "native_mp4_video_plus_audio",
        "total_cases": n_total,
        "correct_count": n_correct,
        "accuracy": round(n_correct / n_total, 3) if n_total else 0.0,
        "positive_match_rate": round(sum(1 for r in pos_cases if r["correct"]) / len(pos_cases), 3) if pos_cases else 0.0,
        "negative_rejection_rate": round(sum(1 for r in neg_cases if r["correct"]) / len(neg_cases), 3) if neg_cases else 0.0,
        "avg_latency_sec": round(sum(r["latency_sec"] for r in results) / n_total, 2) if n_total else 0.0,
        "cases": results,
    }
    out_path = config.ASSETS_DIR / "eval_audio_mood.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    summary = asyncio.run(run_audio_mood_eval())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
