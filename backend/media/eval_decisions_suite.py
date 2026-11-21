"""
Decisions API `choice` (Intent Routing) & `score` (Video Clip Alignment Rubric) Benchmark
(Owned by P2 / worker-1, Zero-Conflict Optimization).

Feature & Use Cases:
- Complements `calibration_20_clips.json` (`noul` yes/no calibration) by empirically
  benchmarking the remaining two `v1beta` `models/gemini-decision-1` primitives wired
  into `backend/director/hydrogen_judge.py`:
  1. `choice` (`classify_rewrite_intent()`): 15 bilingual (EN + ZH) director directives
     across all 5 routing categories (`move_position`, `prop_change`, `new_prop`,
     `emotion_lighting`, `other`) -> accuracy & per-call latency.
  2. `score` (`score_clip_alignment()`): 12 multimodal `.mp4` clip-description pairs
     (6 matched vs 6 mismatched) -> mean matched score vs mismatched score, separation
     margin, pairwise ranking accuracy, and average latency.
- Exports full empirical report to `backend/assets/eval_decisions_multimodal.json`.
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
from backend.director.hydrogen_judge import classify_rewrite_intent, score_clip_alignment
from backend.logger import log_function_call

CHOICE_CASES: list[tuple[str, str]] = [
    # move_position (3)
    ("Move Lia to the far right corner beside the glass window while Nyx stays near the back.", "move_position"),
    ("Have the black cat walk from the right corner over to the center table.", "move_position"),
    ("让莉亚走到右侧玻璃窗前，诺克斯往左侧移动一步。", "move_position"),
    # prop_change (3)
    ("Lia puts her red umbrella down on the floor and catches the crystal vase before it breaks.", "prop_change"),
    ("Drop the red umbrella onto the floor so her hands are free to protect the vase.", "prop_change"),
    ("第2幕把红伞放到地上，扶住桌上的水晶花瓶不要摔碎。", "prop_change"),
    # new_prop (3)
    ("Introduce a glowing golden star lantern that the judge holds up to the camera.", "new_prop"),
    ("Add a brand new antique brass pocket watch onto the center table as a magical gift.", "new_prop"),
    ("我举起桌上的折纸千纸鹤，把它变成舞台里的全新道具放进第2幕。", "new_prop"),
    # emotion_lighting (3)
    ("Shift the stage lighting from cold blue rain to warm golden starlight with a joyful smile.", "emotion_lighting"),
    ("Make the atmosphere dramatic stormy blue with somber sad lighting and thunder.", "emotion_lighting"),
    ("把第4幕改成温暖金色星雪灯光，角色露出温柔开心的神情。", "emotion_lighting"),
    # other (3)
    ("Rewrite the entire ending into a time-loop where Scene 2 never happened and both characters vanish into space.", "other"),
    ("Change the genre to a comedy heist while rearranging props, lighting, positions, and backstory all at once.", "other"),
    ("全面重构第2到第4幕的剧情设定，同时改动道具、走位、情绪与结局走向。", "other"),
]

SCORE_PAIRS: list[tuple[str, str, bool]] = [
    # 6 matched video-description pairs
    ("/assets/finals/scene_1_cam_a.mp4", "Wide front shot of silver-haired girl on left holding red umbrella, intact blue-rose crystal vase on round table, small black cat on right in rainy night glass conservatory.", True),
    ("/assets/finals/scene_2_cam_a.mp4", "Cold stormy blue rainy night inside glass conservatory as crystal vase shatters on wet floor with scattered blue petals and startled black cat.", True),
    ("/assets/finals/scene_4_cam_a.mp4", "Melancholic cold blue rainy conservatory with silver-haired girl standing alone under red umbrella beside shattered crystal vase shards on floor.", True),
    ("/assets/finals/branch_v2_scene_2_cam_a.mp4", "Warm conservatory scene where silver-haired girl kneels gently offering a glowing golden star token to small black cat while blue-rose crystal vase stays intact on round table.", True),
    ("/assets/finals/branch_v2_scene_3_cam_a.mp4", "Clean conservatory floor with zero broken glass as silver-haired girl and black cat bond warmly near glowing star token and intact blue-rose vase on table.", True),
    ("/assets/finals/branch_v2_scene_4_cam_b.mp4", "Warm golden starlight and falling snow close-up of smiling silver-haired girl sheltering small black cat under open red umbrella with intact crystal vase on table.", True),
    # 6 mismatched video-description pairs
    ("/assets/finals/scene_4_cam_a.mp4", "Joyful warm golden starlight snowfall with girl smiling happily and intact unbroken vase sitting on center table.", False),
    ("/assets/finals/scene_2_cam_b.mp4", "Peaceful sunny beach picnic at noon with bright orange surfboard and palm trees.", False),
    ("/assets/finals/branch_v2_scene_4_cam_a.mp4", "Dark cold stormy blue rainstorm with shattered crystal vase shards across wet floor and girl crying alone.", False),
    ("/assets/finals/scene_3_cam_a.mp4", "Intact unbroken crystal vase standing neatly on dry floor under warm golden sunshine with glowing yellow star.", False),
    ("/assets/finals/branch_v2_scene_2_cam_b.mp4", "Red fire truck speeding across a busy downtown highway in daylight.", False),
    ("/assets/finals/scene_1_cam_b.mp4", "Two astronauts floating inside a white spaceship cockpit orbiting Mars.", False),
]


@log_function_call
async def run_decisions_choice_and_score_benchmark() -> dict:
    """Run live `choice` (15 directives) and `score` (12 MP4 pairs) Decisions API benchmarks."""
    t_all = time.perf_counter()
    sem = asyncio.Semaphore(5)

    async def _run_choice(idx: int, directive: str, expected: str) -> dict:
        async with sem:
            t0 = time.perf_counter()
            res = await classify_rewrite_intent(directive)
            elapsed = round(time.perf_counter() - t0, 2)
            pred = res.get("category", "other")
            conf = round(float(res.get("confidence", 0.5)), 3)
            return {
                "id": idx + 1,
                "directive": directive,
                "expected": expected,
                "predicted": pred,
                "confidence": conf,
                "correct": pred == expected,
                "elapsed_sec": elapsed,
            }

    async def _run_score(idx: int, clip_url: str, desc: str, is_match: bool) -> dict:
        async with sem:
            t0 = time.perf_counter()
            score = await score_clip_alignment(desc, media_paths=[clip_url])
            elapsed = round(time.perf_counter() - t0, 2)
            return {
                "id": idx + 1,
                "clip_url": clip_url,
                "target_description": desc,
                "is_match": is_match,
                "alignment_score": score,
                "elapsed_sec": elapsed,
            }

    choice_items, score_items = await asyncio.gather(
        asyncio.gather(*(_run_choice(i, d, e) for i, (d, e) in enumerate(CHOICE_CASES))),
        asyncio.gather(*(_run_score(i, url, desc, m) for i, (url, desc, m) in enumerate(SCORE_PAIRS))),
    )
    c_list = list(choice_items)
    s_list = list(score_items)

    matched_scores = [x["alignment_score"] for x in s_list if x["is_match"]]
    mismatched_scores = [x["alignment_score"] for x in s_list if not x["is_match"]]
    mean_matched = round(sum(matched_scores) / len(matched_scores), 3)
    mean_mismatched = round(sum(mismatched_scores) / len(mismatched_scores), 3)

    # Pairwise AUC / ranking accuracy across all (matched, mismatched) pairs
    wins = sum(1 for m in matched_scores for n in mismatched_scores if m > n)
    ties = sum(0.5 for m in matched_scores for n in mismatched_scores if m == n)
    auc = round((wins + ties) / (len(matched_scores) * len(mismatched_scores)), 3)

    summary = {
        "benchmark": "Decisions API choice + score Multimodal Calibration",
        "model": config.MODEL_DECISION,
        "choice_routing": {
            "total_cases": len(c_list),
            "correct": sum(1 for x in c_list if x["correct"]),
            "accuracy": round(sum(1 for x in c_list if x["correct"]) / len(c_list), 3),
            "avg_latency_sec": round(sum(x["elapsed_sec"] for x in c_list) / len(c_list), 2),
            "items": c_list,
        },
        "score_clip_alignment": {
            "total_clips": len(s_list),
            "matched_count": len(matched_scores),
            "mismatched_count": len(mismatched_scores),
            "mean_matched_score": mean_matched,
            "mean_mismatched_score": mean_mismatched,
            "score_separation_margin": round(mean_matched - mean_mismatched, 3),
            "pairwise_ranking_auc": auc,
            "avg_latency_sec": round(sum(x["elapsed_sec"] for x in s_list) / len(s_list), 2),
            "items": s_list,
        },
        "wall_clock_sec": round(time.perf_counter() - t_all, 2),
    }
    out_path = config.ASSETS_DIR / "eval_decisions_multimodal.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    report = asyncio.run(run_decisions_choice_and_score_benchmark())
    print(
        json.dumps(
            {
                "choice_accuracy": report["choice_routing"]["accuracy"],
                "choice_avg_latency_sec": report["choice_routing"]["avg_latency_sec"],
                "score_mean_matched": report["score_clip_alignment"]["mean_matched_score"],
                "score_mean_mismatched": report["score_clip_alignment"]["mean_mismatched_score"],
                "score_separation_margin": report["score_clip_alignment"]["score_separation_margin"],
                "score_pairwise_auc": report["score_clip_alignment"]["pairwise_ranking_auc"],
                "score_avg_latency_sec": report["score_clip_alignment"]["avg_latency_sec"],
                "wall_clock_sec": report["wall_clock_sec"],
            },
            indent=2,
        )
    )
