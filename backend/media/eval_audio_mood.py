"""
Native Omni Flash Audio-Visual Mood Alignment Benchmark via Decisions API
(Owned by P2 / worker-1, Zero-Conflict Optimization).

Feature & Use Cases:
- Directly feeds cached native-audio H.264 `.mp4` clips from `branch_v1` (Scenes 1..4)
  and `branch_v2_cached` (Scenes 2..4) into `v1beta` `models/gemini-decision-1`
  (consuming both video + 48kHz stereo AAC audio tokens) to quantitatively evaluate:
  1. `audio_matches_mood`: Does generated SFX/score match the scene's `audio_mood`?
  2. `audio_visual_sync`: Does the soundscape match visible rain/thunder vs warm snowfall?
  3. `clean_audio_no_glitches`: Free of garbled speech or harsh audio artifacts?
- Includes 4 negative audio-mood mismatch controls to verify genuine audio discrimination
  (TPR + TNR), saving full results to `backend/assets/eval_audio_mood.json`.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env", override=False)

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.director.state_tree import get_branch
from backend.logger import log_function_call

MOOD_DESCRIPTIONS = {
    ("branch_v1", 1): "soft gentle nighttime rain against glass conservatory windows with quiet melancholic ambience",
    ("branch_v1", 2): "dramatic rainy storm with thunderclap or shattering glass / tense startled impact sound",
    ("branch_v1", 3): "steady nighttime rainstorm with quiet melancholic or somber atmospheric music",
    ("branch_v1", 4): "heavy rainstorm ambience with sad, wistful, or quiet melancholic instrumental mood",
    ("branch_v2_cached", 2): "gentle soothing magical chimes, easing rain, or calm warm atmospheric music",
    ("branch_v2_cached", 3): "warm gentle music-box or soft calming chimes with peaceful atmosphere",
    ("branch_v2_cached", 4): "warm magical winter starlight chimes, soft strings, or peaceful snowfall ambience",
}

NEGATIVE_AUDIO_CONTROLS = [
    (
        "branch_v1",
        4,
        "cam_a",
        "/assets/finals/scene_4_cam_a.mp4",
        "Does the audio feature loud upbeat rock electric guitars, cheering stadium crowds, and party horns?",
    ),
    (
        "branch_v1",
        2,
        "cam_a",
        "/assets/finals/scene_2_cam_a.mp4",
        "Does the audio consist solely of cheerful sunny beach seagulls, ocean surf, and ukulele music?",
    ),
    (
        "branch_v2_cached",
        4,
        "cam_a",
        "/assets/finals/branch_v2_scene_4_cam_a.mp4",
        "Does the audio feature aggressive heavy-metal screaming, sirens, and loud explosions?",
    ),
    (
        "branch_v2_cached",
        3,
        "cam_b",
        "/assets/finals/branch_v2_scene_3_cam_b.mp4",
        "Does the audio feature loud construction jackhammers and honking city traffic rush-hour noise?",
    ),
]


@log_function_call
async def run_audio_mood_benchmark() -> dict:
    """Evaluate native Omni Flash audio-visual alignment across all 14 canonical clips + 4 negative controls."""
    t_all = time.perf_counter()
    b1 = get_branch("branch_v1")
    b2 = get_branch("branch_v2_cached")
    assert b1 is not None and b2 is not None

    jobs: list[dict] = []
    for branch_id, scene in [(b1.branch_id, s) for s in b1.scenes] + [
        (b2.branch_id, s) for s in b2.scenes if s.scene_id >= 2
    ]:
        mood_desc = MOOD_DESCRIPTIONS.get((branch_id, scene.scene_id), scene.audio_mood or scene.lighting)
        for cam_id in ("cam_a", "cam_b"):
            clip_url = getattr(scene.media, f"{cam_id}_final_url")
            if not clip_url:
                continue
            jobs.append(
                {
                    "branch_id": branch_id,
                    "scene_id": scene.scene_id,
                    "cam_id": cam_id,
                    "clip_url": clip_url,
                    "audio_mood": scene.audio_mood,
                    "mood_desc": mood_desc,
                }
            )

    sem = asyncio.Semaphore(5)

    async def _eval_positive(job: dict) -> dict:
        async with sem:
            local_mp4 = config.ASSETS_DIR / job["clip_url"].removeprefix("/assets/")
            q_map = {
                "audio_matches_mood": {
                    "type": "noul",
                    "instructions": f"Does the video's native audio track match this intended soundscape/mood: {job['mood_desc']}?",
                },
                "audio_visual_sync": {
                    "type": "noul",
                    "instructions": "Is the audio atmosphere coherent and synchronized with the visible weather and emotional tone on screen?",
                },
                "clean_audio_no_glitches": {
                    "type": "noul",
                    "instructions": "Is the audio free of garbled unintended speech or harsh digital distortion glitches?",
                },
            }
            t0 = time.perf_counter()
            payload = build_decision_input(
                f"Evaluate both the native audio track and visuals of clip {job['clip_url']}.",
                [local_mp4],
            )
            res = await create_decision(payload, q_map)
            elapsed = round(time.perf_counter() - t0, 2)
            answers = getattr(res, "answers", None) or {}
            checks = {}
            for k in q_map:
                ans = answers.get(k)
                p_true = round(float(getattr(ans, "noul", 0.5)), 3) if ans is not None else 0.5
                conf = round(float(getattr(ans, "confidence", max(p_true, 1.0 - p_true))), 3) if ans is not None else 0.5
                checks[k] = {"p_true": p_true, "confidence": conf, "passed": p_true >= 0.5}
            return {**job, "elapsed_sec": elapsed, "checks": checks}

    async def _eval_negative(row: tuple) -> dict:
        branch_id, scene_id, cam_id, clip_url, neg_q = row
        async with sem:
            local_mp4 = config.ASSETS_DIR / clip_url.removeprefix("/assets/")
            t0 = time.perf_counter()
            payload = build_decision_input(
                f"Listen closely to the native audio track of {clip_url} and answer truthfully.",
                [local_mp4],
            )
            res = await create_decision(payload, {"neg": {"type": "noul", "instructions": neg_q}})
            elapsed = round(time.perf_counter() - t0, 2)
            ans = (getattr(res, "answers", None) or {}).get("neg")
            p_true = round(float(getattr(ans, "noul", 0.5)), 3) if ans is not None else 0.5
            predicted = p_true >= 0.5
            return {
                "branch_id": branch_id,
                "scene_id": scene_id,
                "cam_id": cam_id,
                "clip_url": clip_url,
                "question": neg_q,
                "p_true": p_true,
                "predicted_label": predicted,
                "expected_label": False,
                "correct_rejection": predicted is False,
                "elapsed_sec": elapsed,
            }

    pos_results, neg_results = await asyncio.gather(
        asyncio.gather(*(_eval_positive(j) for j in jobs)),
        asyncio.gather(*(_eval_negative(r) for r in NEGATIVE_AUDIO_CONTROLS)),
    )
    pos_list = list(pos_results)
    neg_list = list(neg_results)

    mood_pass = sum(1 for r in pos_list if r["checks"]["audio_matches_mood"]["passed"])
    sync_pass = sum(1 for r in pos_list if r["checks"]["audio_visual_sync"]["passed"])
    clean_pass = sum(1 for r in pos_list if r["checks"]["clean_audio_no_glitches"]["passed"])
    total_pos_checks = len(pos_list) * 3
    passed_pos_checks = mood_pass + sync_pass + clean_pass
    neg_correct = sum(1 for r in neg_list if r["correct_rejection"])

    summary = {
        "benchmark": "Omni Flash Native Audio-Visual Alignment via Decisions API",
        "model": config.MODEL_DECISION,
        "positive_clips_evaluated": len(pos_list),
        "negative_controls_evaluated": len(neg_list),
        "audio_mood_match_rate": round(mood_pass / len(pos_list), 3) if pos_list else 0.0,
        "audio_visual_sync_rate": round(sync_pass / len(pos_list), 3) if pos_list else 0.0,
        "clean_audio_rate": round(clean_pass / len(pos_list), 3) if pos_list else 0.0,
        "overall_positive_alignment_rate": round(passed_pos_checks / total_pos_checks, 3) if total_pos_checks else 0.0,
        "negative_control_tnr": round(neg_correct / len(neg_list), 3) if neg_list else 0.0,
        "avg_latency_sec": round(
            sum(r["elapsed_sec"] for r in pos_list + neg_list) / max(1, len(pos_list) + len(neg_list)),
            2,
        ),
        "wall_clock_sec": round(time.perf_counter() - t_all, 2),
        "positive_items": pos_list,
        "negative_controls": neg_list,
    }
    out_path = config.ASSETS_DIR / "eval_audio_mood.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    report = asyncio.run(run_audio_mood_benchmark())
    print(json.dumps({k: v for k, v in report.items() if k not in ("positive_items", "negative_controls")}, indent=2))
