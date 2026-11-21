"""
W1-T7F: Export Consolidated `metrics_summary_for_submission.json` (Owned by P2).

Aggregates all empirically measured benchmarks across the StageState repository into
a single Devpost / Pitch / Eval-Modal ready JSON summary under
`backend/assets/metrics_summary_for_submission.json`.
"""

import json
import time
from pathlib import Path

from backend import config


def _load_if_exists(name: str) -> dict:
    p = config.ASSETS_DIR / name
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def build_metrics_summary() -> dict:
    """Aggregate all live evaluation artifacts into one consolidated submission summary."""
    dual_cam = _load_if_exists("eval_dual_cam_stats.json")
    calib = _load_if_exists("calibration_20_clips.json")
    prop_persist = _load_if_exists("eval_prop_persistence.json")
    props_audit = _load_if_exists("eval_props_audit.json")
    audio_codec = _load_if_exists("eval_audio_codec.json")
    bench_720 = _load_if_exists("eval_720p_vs_360p.json")
    cache_savings = _load_if_exists("eval_cache_savings.json")
    preflight = _load_if_exists("preflight_report.json")
    blueprint = _load_if_exists("eval_blueprint_anchoring.json")
    branch_matrix = _load_if_exists("eval_branch_matrix.json")
    audio_mood = _load_if_exists("eval_audio_mood.json")
    burst_stress = _load_if_exists("eval_burst_stress.json")
    burst_nested = burst_stress.get("concurrent_burst_stress") or {}
    failover = _load_if_exists("eval_failover_rehearsal.json")
    live_props = _load_if_exists("eval_live_props_sim.json")
    decisions_suite = _load_if_exists("eval_decisions_multimodal.json")
    fixed_room = _load_if_exists("eval_dual_cam_fixed_room.json")
    fast_live = _load_if_exists("eval_fast_live_latency.json")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": "StageState — Time-Travel Dual-Camera AI Stage Director",
        "models": {
            "director_planner": config.MODEL_DIRECTOR,
            "multimodal_judge": config.MODEL_JUDGE,
            "decisions_v1beta": config.MODEL_DECISION,
            "video_synthesis": config.MODEL_VIDEO,
            "prop_cutout": config.MODEL_IMAGE,
        },
        "dual_cam_consistency": {
            "cross_camera_consistency_rate": dual_cam.get("cross_camera_consistency_rate"),
            "passed_checks": dual_cam.get("passed_checks"),
            "total_scene_pairs": dual_cam.get("total_scene_pairs"),
            "avg_latency_sec": dual_cam.get("avg_latency_sec"),
        },
        "decisions_api_calibration": {
            "total_clips": calib.get("total_clips"),
            "accuracy": calib.get("accuracy"),
            "true_positive_rate": calib.get("true_positive_rate"),
            "true_negative_rate": calib.get("true_negative_rate"),
            "avg_latency_sec": calib.get("avg_latency_sec"),
        },
        "prop_persistence_and_cutout": {
            "total_props_audited": props_audit.get("total_props"),
            "all_rgba": props_audit.get("all_rgba"),
            "all_bottom_flush": props_audit.get("all_bottom_flush_ok"),
            "total_green_halo_pixels": props_audit.get("total_green_halo_pixels"),
            "branch_v3_star_token_persistence": (prop_persist.get("branch_v3_prop_persistence") or {}).get(
                "star_token_persistence_rate"
            ),
            "branch_v3_vase_persistence": (prop_persist.get("branch_v3_prop_persistence") or {}).get(
                "crystal_vase_intact_persistence_rate"
            ),
            "desk_props_avg_latency_sec": live_props.get("avg_latency_sec"),
        },
        "audio_and_codec_audit": {
            "unique_scene_mp4_count": audio_codec.get("unique_scene_mp4_count"),
            "all_h264": audio_codec.get("all_h264"),
            "all_640x360": audio_codec.get("all_640x360"),
            "all_24fps": audio_codec.get("all_24fps"),
            "all_have_audio_stream": audio_codec.get("all_have_audio_stream"),
            "submission_mp4s_audited": len(audio_codec.get("submission_videos") or []),
            "all_submission_videos_have_audio": all(
                v.get("has_audio_stream") for v in (audio_codec.get("submission_videos") or [])
            )
            if audio_codec.get("submission_videos")
            else False,
        },
        "resolution_latency_tradeoff": bench_720,
        "cache_and_cost_savings": {
            "branches_measured": cache_savings.get("branches_measured"),
            "clips_skipped": cache_savings.get("clips_skipped"),
            "total_usd_saved": cache_savings.get("total_usd_saved"),
            "overall_cost_reduction_pct": cache_savings.get("overall_cost_reduction_pct"),
            "localized_dependency_filter_savings_rate": cache_savings.get(
                "localized_dependency_filter_savings_rate"
            ),
        },
        "multimodal_and_robustness": {
            "branch_matrix_accuracy": branch_matrix.get("matrix_accuracy"),
            "audio_mood_accuracy": audio_mood.get("audio_mood_accuracy"),
            "burst_stress_wall_sec": (
                burst_stress.get("wall_latency_sec")
                if burst_stress.get("wall_latency_sec") is not None
                else burst_nested.get("elapsed_sec")
            ),
            "burst_stress_deadlock_free": (
                burst_stress.get("deadlock_free")
                if burst_stress.get("deadlock_free") is not None
                else burst_nested.get("zero_deadlocks_or_collisions")
            ),
            "failover_rehearsal_passed": failover.get("all_passed"),
            "cached_blueprints_total": blueprint.get("cached_blueprints_total"),
            "decisions_multimodal_suite_keys": list(decisions_suite.keys()) if decisions_suite else [],
            "fixed_room_5_criteria_rate_0_80": (fixed_room.get("fixed_room_5_criteria_eval") or {}).get(
                "consistency_rate_0_80"
            ),
            "fixed_room_qa_score": (fixed_room.get("render_result") or {}).get("qa_score"),
            "fast_live_scene5_wall_sec": fast_live.get("wall_clock_sec"),
            "fast_live_scene5_qa_score": fast_live.get("qa_score"),
        },
        "preflight": {
            "preflight_passed": preflight.get("preflight_passed"),
            "mp4_total": (preflight.get("mp4_summary") or {}).get("total_mp4s"),
            "mp4_all_h264": (preflight.get("mp4_summary") or {}).get("all_h264"),
            "mp4_with_audio": (preflight.get("mp4_summary") or {}).get("with_audio_count"),
            "blueprints_count": (preflight.get("blueprints_summary") or {}).get("count"),
        },
    }
    out_path = config.ASSETS_DIR / "metrics_summary_for_submission.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(build_metrics_summary(), ensure_ascii=False, indent=2))
