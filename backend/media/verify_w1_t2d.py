"""
W1-T2B + W1-T2D + W1-T3A Live Verification & Benchmark Script (Owned by P2 / worker-1).

Verifies:
1. H.264 browser-playback codec across all cached MP4s (`backend/assets/finals/*.mp4`).
2. Photo-based `create_prop()` using realistic JPEG camera bytes -> RGBA transparent PNG.
3. Multimodal Decisions API (`models/gemini-decision-1` v1beta) benchmark comparing
   direct `.mp4` clip input vs `.png` tail-frame input (latencies & direct acceptance).
4. Exact 1-take vs 2-take pass-rate summary from `backend/assets/eval_media_stats.jsonl`.
"""

import asyncio
import io
import json
import time
from pathlib import Path

from PIL import Image

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import is_browser_h264


async def run_verification() -> dict:
    report: dict = {}

    # 1. H.264 Chrome playback check on all cached MP4s (W1-T2B)
    mp4s = sorted(config.FINALS_DIR.glob("*.mp4"))
    h264_results = {p.name: is_browser_h264(p) for p in mp4s}
    report["h264_check"] = {
        "total_mp4s": len(mp4s),
        "all_h264": all(h264_results.values()) and len(mp4s) > 0,
        "non_h264_files": [k for k, v in h264_results.items() if not v],
    }

    # 2. Photo-based create_prop() live test (W1-T2D)
    # Create a real JPEG photo input from an existing scene image (simulating webcam JPEG upload).
    sample_src = config.ASSETS_DIR / "props" / "crystal_vase.png"
    buf = io.BytesIO()
    img = Image.open(sample_src).convert("RGB")
    img.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()

    t0 = time.perf_counter()
    prop_id, prop_item = await create_prop(
        name="glass_lantern_photo_test",
        description="Warm glowing vintage brass and glass lantern from user photo",
        camera_frame_bytes=jpeg_bytes,
        prop_id="photo_prop_test",
    )
    photo_elapsed = round(time.perf_counter() - t0, 2)
    saved_prop = config.PROPS_DIR / f"{prop_id}.png"
    out_img = Image.open(saved_prop)
    alpha_extrema = out_img.getchannel("A").getextrema() if "A" in out_img.getbands() else None
    report["photo_create_prop"] = {
        "prop_id": prop_id,
        "origin": prop_item.origin,
        "image_url": prop_item.image_url,
        "elapsed_sec": photo_elapsed,
        "size": [out_img.width, out_img.height],
        "mode": out_img.mode,
        "has_transparency": bool(alpha_extrema and alpha_extrema[0] == 0 and alpha_extrema[1] > 200),
    }

    # 3. Multimodal Decisions API (.mp4 vs .png) benchmark (W1-T2D)
    sample_mp4 = config.FINALS_DIR / "scene_1_cam_a.mp4"
    sample_png = config.TAILS_DIR / "scene_1_cam_a_tail.png"
    q_map = {
        "q1": {
            "type": "noul",
            "instructions": "Is a silver-haired girl with a red umbrella visible in the glass conservatory?",
        },
        "q2": {
            "type": "noul",
            "instructions": "Is the black cat realistically small compared to the girl?",
        },
    }

    dec_report: dict = {}
    for label, media_path in [("mp4_direct", sample_mp4), ("png_tail", sample_png)]:
        t_start = time.perf_counter()
        try:
            payload = build_decision_input("Evaluate StageState Scene 1 Cam A visual frame.", [media_path])
            res = await create_decision(payload, q_map)
            elapsed = round(time.perf_counter() - t_start, 2)
            answers = getattr(res, "answers", None) or {}
            scores = {
                k: {
                    "noul": round(float(getattr(v, "noul", 0.5)), 3),
                    "confidence": round(float(getattr(v, "confidence", 0.5)), 3),
                }
                for k, v in answers.items()
            }
            dec_report[label] = {"accepted": True, "elapsed_sec": elapsed, "answers": scores}
        except Exception as exc:
            elapsed = round(time.perf_counter() - t_start, 2)
            dec_report[label] = {
                "accepted": False,
                "elapsed_sec": elapsed,
                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
            }
    report["decisions_multimodal_bench"] = dec_report

    # 4. Aggregate 1-take vs 2-take stats from eval_media_stats.jsonl (W1-T3A)
    stats_path = config.ASSETS_DIR / "eval_media_stats.jsonl"
    rows = [json.loads(line) for line in stats_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    t0_scores = [r["take_0_score"] for r in rows if r.get("take_0_score") is not None]
    best_scores = [r["selected_qa_score"] for r in rows if r.get("selected_qa_score") is not None]
    thr = config.QA_PASS_THRESHOLD
    take1_pass = sum(1 for s in t0_scores if s >= thr)
    take2_pass = sum(1 for s in best_scores if s >= thr)
    report["eval_media_stats_summary"] = {
        "total_shots": len(rows),
        "threshold": thr,
        "take_1_avg_score": round(sum(t0_scores) / len(t0_scores), 3) if t0_scores else 0.0,
        "take_2_best_avg_score": round(sum(best_scores) / len(best_scores), 3) if best_scores else 0.0,
        "take_1_pass_rate": round(take1_pass / len(t0_scores), 3) if t0_scores else 0.0,
        "take_2_pass_rate": round(take2_pass / len(best_scores), 3) if best_scores else 0.0,
        "take_1_won_count": sum(1 for r in rows if r.get("selected_idx") == 1),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    asyncio.run(run_verification())
