"""
W1-T6H: Cache Reuse & Cost-Savings Quantifier (Owned by P2 / worker-1).

Feature & Use Cases:
- Analyzes all persisted branches under `backend/assets/branches/*.json` and quantifies
  exact Omni Flash API call savings, wall-clock latency savings, and USD cost savings of
  StageState's localized `reused_from_branch` + cached prefix architecture versus naive
  full-branch 4-scene regeneration (for both 1-take and 2-take dual-camera workflows).
- Exports `backend/assets/eval_cache_savings.json`.
"""

import json
import time
from pathlib import Path

from backend import config
from backend.director.state_tree import list_branches
from backend.logger import log_function_call

# Empirically measured benchmarks from W1-T3H & W1-T2D
CLIP_DURATION_SEC = 4
COST_PER_SEC_360P_USD = 0.02
COST_PER_CLIP_360P_USD = CLIP_DURATION_SEC * COST_PER_SEC_360P_USD  # $0.08
COST_PER_CLIP_720P_USD = CLIP_DURATION_SEC * 0.07  # $0.28
DUAL_TAKE_COUNT = config.DUAL_TAKE_COUNT  # 2
CAMS_PER_SCENE = 2
OMNI_LATENCY_PER_SCENE_PARALLEL_SEC = 25.47  # Measured dual-take parallel latency (W1-T2D)
OMNI_LATENCY_PER_CLIP_SEQ_SEC = 18.64


@log_function_call
def compute_cache_savings() -> dict:
    """Compute scene-level reuse, API call reduction, and USD/latency savings across all cached branches."""
    branches = list_branches()
    seen_clip_urls: set[str] = set()
    branch_rows: list[dict] = []

    total_scene_slots = 0
    total_reused_scenes = 0
    total_unique_rendered_scenes = 0

    for b in sorted(branches, key=lambda x: x.branch_id):
        n_scenes = len(b.scenes)
        reused = 0
        rendered = 0
        for s in b.scenes:
            urls = [u for u in (s.media.cam_a_final_url, s.media.cam_b_final_url) if u]
            is_reused = bool(s.reused_from_branch) or (bool(urls) and all(u in seen_clip_urls for u in urls))
            if is_reused:
                reused += 1
            else:
                rendered += 1
                seen_clip_urls.update(urls)

        total_scene_slots += n_scenes
        total_reused_scenes += reused
        total_unique_rendered_scenes += rendered

        naive_clips_2take = n_scenes * CAMS_PER_SCENE * DUAL_TAKE_COUNT
        actual_clips_2take = rendered * CAMS_PER_SCENE * DUAL_TAKE_COUNT
        naive_cost_360p = round(naive_clips_2take * COST_PER_CLIP_360P_USD, 2)
        actual_cost_360p = round(actual_clips_2take * COST_PER_CLIP_360P_USD, 2)

        branch_rows.append(
            {
                "branch_id": b.branch_id,
                "branch_label": b.branch_label,
                "parent_branch_id": b.parent_branch_id,
                "forked_from_scene": b.forked_from_scene,
                "total_scenes": n_scenes,
                "reused_scenes": reused,
                "rendered_scenes": rendered,
                "scene_reuse_rate": round(reused / n_scenes, 3) if n_scenes else 0.0,
                "naive_omni_calls_2take": naive_clips_2take,
                "actual_omni_calls_2take": actual_clips_2take,
                "saved_omni_calls_2take": naive_clips_2take - actual_clips_2take,
                "naive_cost_360p_usd": naive_cost_360p,
                "actual_cost_360p_usd": actual_cost_360p,
                "saved_cost_360p_usd": round(naive_cost_360p - actual_cost_360p, 2),
                "instant_cached_replay_sec": 0.0,
            }
        )

    # Also model localized single-scene & 2-scene fork scenarios (e.g., fork at Scene 3 or Scene 4)
    fork_scenarios = []
    for fork_sid in (2, 3, 4):
        scenes_rerendered = 4 - (fork_sid - 1)
        scenes_reused = fork_sid - 1
        naive_calls = 4 * CAMS_PER_SCENE * DUAL_TAKE_COUNT
        actual_calls = scenes_rerendered * CAMS_PER_SCENE * DUAL_TAKE_COUNT
        fork_scenarios.append(
            {
                "scenario": f"Fork at Scene {fork_sid} (Scenes 1..{scenes_reused} reused verbatim)",
                "reused_scenes": scenes_reused,
                "rerendered_scenes": scenes_rerendered,
                "compute_savings_pct": round((scenes_reused / 4) * 100.0, 1),
                "naive_cost_360p_usd": round(naive_calls * COST_PER_CLIP_360P_USD, 2),
                "localized_cost_360p_usd": round(actual_calls * COST_PER_CLIP_360P_USD, 2),
                "usd_saved_per_fork": round((naive_calls - actual_calls) * COST_PER_CLIP_360P_USD, 2),
                "naive_seq_latency_sec": round(4 * CAMS_PER_SCENE * OMNI_LATENCY_PER_CLIP_SEQ_SEC, 1),
                "localized_parallel_latency_sec": round(OMNI_LATENCY_PER_SCENE_PARALLEL_SEC, 1),
                "wall_clock_speedup_x": round((4 * CAMS_PER_SCENE * OMNI_LATENCY_PER_CLIP_SEQ_SEC) / OMNI_LATENCY_PER_SCENE_PARALLEL_SEC, 2),
            }
        )

    naive_total_calls = total_scene_slots * CAMS_PER_SCENE * DUAL_TAKE_COUNT
    actual_total_calls = total_unique_rendered_scenes * CAMS_PER_SCENE * DUAL_TAKE_COUNT
    saved_calls = naive_total_calls - actual_total_calls

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cost_model": {
            "clip_duration_sec": CLIP_DURATION_SEC,
            "360p_usd_per_clip": COST_PER_CLIP_360P_USD,
            "720p_usd_per_clip": COST_PER_CLIP_720P_USD,
            "360p_vs_720p_cost_reduction_pct": round((1.0 - COST_PER_CLIP_360P_USD / COST_PER_CLIP_720P_USD) * 100.0, 1),
            "dual_take_count": DUAL_TAKE_COUNT,
            "cams_per_scene": CAMS_PER_SCENE,
        },
        "portfolio_summary": {
            "cached_branches_count": len(branch_rows),
            "total_scene_slots": total_scene_slots,
            "reused_scene_slots": total_reused_scenes,
            "unique_rendered_scenes": total_unique_rendered_scenes,
            "unique_mp4_files_stored": len(seen_clip_urls),
            "prefix_reuse_savings_pct": round((total_reused_scenes / total_scene_slots) * 100.0, 1) if total_scene_slots else 0.0,
            "naive_total_omni_calls_2take": naive_total_calls,
            "actual_total_omni_calls_2take": actual_total_calls,
            "saved_omni_calls_2take": saved_calls,
            "naive_total_cost_360p_usd": round(naive_total_calls * COST_PER_CLIP_360P_USD, 2),
            "actual_total_cost_360p_usd": round(actual_total_calls * COST_PER_CLIP_360P_USD, 2),
            "saved_cost_360p_usd": round(saved_calls * COST_PER_CLIP_360P_USD, 2),
            "naive_total_cost_720p_usd": round(naive_total_calls * COST_PER_CLIP_720P_USD, 2),
            "combined_360p_plus_reuse_savings_vs_naive_720p_pct": round(
                (1.0 - (actual_total_calls * COST_PER_CLIP_360P_USD) / (naive_total_calls * COST_PER_CLIP_720P_USD)) * 100.0,
                1,
            )
            if naive_total_calls
            else 0.0,
        },
        "per_branch": branch_rows,
        "localized_fork_scenarios": fork_scenarios,
    }

    out_path = config.ASSETS_DIR / "eval_cache_savings.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    summary = compute_cache_savings()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
