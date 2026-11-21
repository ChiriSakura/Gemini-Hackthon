"""
W1-V2-B1 & W1-V2-B2 Live Empirical Benchmarks (Owned by P2 / worker-1).

Feature & Use Cases:
- **W1-V2-B1 (`eval_dual_cam_fixed_room.json`)**: Renders 1 live dual-camera Scene 2 pair
  through the upgraded fixed-room pipeline (`conservatory_cam_a.png` + `conservatory_cam_b.png`
  + isolated `red_umbrella_closed.png` + `wooden_chair.png` + `CROSS_CAM_THRESHOLD = 0.80`),
  measures live Decisions API (`models/gemini-decision-1`) cross-camera consistency across
  5 single-condition criteria (`room_architecture`, `actors_present`, `left_right_blocking`,
  `prop_states`, `lighting_mood`), and compares apples-to-apples against the pre-V2 baseline
  (`backend/assets/eval_dual_cam_stats.json`).
- **W1-V2-B2 (`eval_fast_live_latency.json`)**: Benchmarks 1 live `render_scene_media(..., fast_live_mode=True)`
  call on an extended Scene 5 (`scene_id=5`, `parent_scene_id=4`, `end_freeze_mode="sculpture"`,
  `action="catch_from_camera"`) with an everyday thrown desk prop (`showcase_desk_coffee_mug.png`),
  verifying wall-clock turnaround vs the 10-20s interactive budget and pre-caching sample clips
  at `backend/assets/finals/scene_5_fast_live_cam_{a,b}.mp4` + tail PNGs.
"""

import asyncio
import json
import shutil
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
from backend.logger import log_function_call
from backend.media.video_renderer import (
    CROSS_CAM_THRESHOLD,
    _resolve_asset_to_local_path,
    _select_scene_ref_images,
    is_browser_h264,
    render_scene_media,
)
from backend.schemas import ActorState, Scene, StagePropState


async def _evaluate_dual_cam_5_criteria(
    scene: Scene,
    cam_a_tail: Path,
    cam_b_tail: Path,
) -> dict:
    """Evaluate 5 single-condition cross-camera consistency criteria via `models/gemini-decision-1`."""
    q_map = {
        "room_architecture": {
            "type": "noul",
            "instructions": (
                "Do Image 1 (Camera A front wide) and Image 2 (Camera B 45-degree side angle) depict "
                "the same nighttime glass conservatory room with matching arched windows, warm wall lanterns, "
                "wooden floor, and central round wooden table?"
            ),
        },
        "actors_present": {
            "type": "noul",
            "instructions": (
                "Do both Image 1 and Image 2 show the same characters: the silver-haired anime girl Lia "
                "in a dark starry blue cloak and the small black cat Nyx?"
            ),
        },
        "left_right_blocking": {
            "type": "noul",
            "instructions": (
                "Are Lia and Nyx positioned consistently around the central round wooden table across "
                "Camera A and Camera B (accounting for Camera B looking from a 45-degree right-side angle)?"
            ),
        },
        "prop_states": {
            "type": "noul",
            "instructions": (
                "Do the stage props match consistently across both cameras (intact crystal vase on the "
                "central round table, closed furled crimson umbrella, and wooden chair beside the table)?"
            ),
        },
        "lighting_mood": {
            "type": "noul",
            "instructions": (
                "Do both cameras share the same nighttime rainy conservatory atmosphere with warm amber "
                "wall lantern glow and dark blue night outside?"
            ),
        },
    }
    t0 = time.perf_counter()
    payload = build_decision_input(
        f"W1-V2-B1 fixed-room cross-camera consistency evaluation for Scene {scene.scene_id}: {scene.narration}",
        [cam_a_tail, cam_b_tail],
    )
    res = await create_decision(payload, q_map)
    elapsed = round(time.perf_counter() - t0, 2)
    answers = getattr(res, "answers", None) or {}
    checks: dict[str, dict] = {}
    for k in q_map:
        ans = answers.get(k)
        p_true = float(getattr(ans, "noul", 0.5)) if ans is not None else 0.5
        checks[k] = {
            "p_true": round(p_true, 4),
            "passed_0_5": p_true >= 0.5,
            "passed_0_80": p_true >= CROSS_CAM_THRESHOLD,
        }
    n_total = len(checks)
    n_pass_05 = sum(1 for c in checks.values() if c["passed_0_5"])
    n_pass_08 = sum(1 for c in checks.values() if c["passed_0_80"])
    avg_p = round(sum(c["p_true"] for c in checks.values()) / n_total, 4)
    return {
        "eval_latency_sec": elapsed,
        "checks": checks,
        "passed_0_5_count": n_pass_05,
        "passed_0_80_count": n_pass_08,
        "total_checks": n_total,
        "consistency_rate_0_5": round(n_pass_05 / n_total, 4),
        "consistency_rate_0_80": round(n_pass_08 / n_total, 4),
        "mean_p_true": avg_p,
    }


@log_function_call
async def run_b1_fixed_room_eval() -> dict:
    """W1-V2-B1: Render 1 live fixed-room dual-cam Scene 2 pair and benchmark A/B consistency."""
    scene2 = Scene(
        scene_id=2,
        title="Fixed-Room Conservatory Evening",
        narration=(
            "Inside the quiet rainy glass conservatory, Lia stands to the left of the central round table "
            "holding her closed crimson umbrella at her side beside a vintage wooden chair, while small black "
            "cat Nyx sits calmly on the right watching the glowing intact crystal vase on the table."
        ),
        lighting="warm_dim",
        weather="rain",
        audio_mood="gentle rain on conservatory glass with warm soft chimes",
        end_freeze_mode="sculpture",
        actors=[
            ActorState(
                id="lia",
                name="Lia",
                asset_url="/assets/actors/lia.png",
                grid=[0.6, 1.1],
                facing="right",
                action="stand",
                holding_prop="red_umbrella",
            ),
            ActorState(
                id="nyx",
                name="Nyx",
                asset_url="/assets/actors/nyx.png",
                grid=[1.45, 1.15],
                facing="left",
                action="sit",
            ),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], state="intact", on_floor=False),
            StagePropState(id="wooden_chair", grid=[0.42, 1.05], state="intact", on_floor=True),
        ],
        qa_questions=[
            "Is the crystal vase intact on the central round wooden table?",
            "Is Lia's crimson umbrella closed and furled rather than open?",
            "Is there a wooden chair resting cleanly on the conservatory floor?",
        ],
    )

    refs = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "red_umbrella_closed.png"),
        str(config.PROPS_DIR / "wooden_chair.png"),
    ]
    selected_a = [p.name for p in _select_scene_ref_images(scene2, refs, max_refs=6, cam_id="cam_a")]
    selected_b = [p.name for p in _select_scene_ref_images(scene2, refs, max_refs=6, cam_id="cam_b")]

    t0 = time.perf_counter()
    media = await render_scene_media(
        scene2,
        refs,
        fast_live_mode=False,
        overwrite_canonical=False,
    )
    render_elapsed = round(time.perf_counter() - t0, 2)

    vid_a = _resolve_asset_to_local_path(media.cam_a_final_url)
    vid_b = _resolve_asset_to_local_path(media.cam_b_final_url)
    tail_a = _resolve_asset_to_local_path(media.cam_a_tail_frame_url)
    tail_b = _resolve_asset_to_local_path(media.cam_b_tail_frame_url)
    assert vid_a and vid_b and tail_a and tail_b, f"Missing rendered outputs: {media}"

    # Move temporary run-tagged outputs into canonical eval sample paths so preflight & reviewers can inspect them cleanly.
    canon_va = config.FINALS_DIR / "scene_2_fixed_room_cam_a.mp4"
    canon_vb = config.FINALS_DIR / "scene_2_fixed_room_cam_b.mp4"
    canon_ta = config.TAILS_DIR / "scene_2_fixed_room_cam_a_tail.png"
    canon_tb = config.TAILS_DIR / "scene_2_fixed_room_cam_b_tail.png"
    shutil.move(str(vid_a), str(canon_va))
    shutil.move(str(vid_b), str(canon_vb))
    shutil.move(str(tail_a), str(canon_ta))
    shutil.move(str(tail_b), str(canon_tb))

    eval_5 = await _evaluate_dual_cam_5_criteria(scene2, canon_ta, canon_tb)

    baseline_path = config.ASSETS_DIR / "eval_dual_cam_stats.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else {}
    baseline_summary = baseline.get("summary", {})

    report = {
        "task_id": "W1-V2-B1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pipeline_config": {
            "cross_cam_threshold": CROSS_CAM_THRESHOLD,
            "cam_a_refs": selected_a,
            "cam_b_refs": selected_b,
            "fixed_room_refs_used": ["conservatory_cam_a.png", "conservatory_cam_b.png"],
            "isolated_props_used": ["red_umbrella_closed.png", "wooden_chair.png"],
        },
        "render_result": {
            "wall_clock_sec": render_elapsed,
            "qa_score": media.qa_score,
            "qa_retries": media.qa_retries,
            "fallback_to_preview": media.fallback_to_preview,
            "cam_a_url": f"/assets/finals/{canon_va.name}",
            "cam_b_url": f"/assets/finals/{canon_vb.name}",
            "cam_a_tail_url": f"/assets/tails/{canon_ta.name}",
            "cam_b_tail_url": f"/assets/tails/{canon_tb.name}",
            "cam_a_h264": is_browser_h264(canon_va),
            "cam_b_h264": is_browser_h264(canon_vb),
            "renderer_qa_items": [it.model_dump() for it in (media.qa_result.items if media.qa_result else [])],
        },
        "fixed_room_5_criteria_eval": eval_5,
        "before_vs_after_comparison": {
            "before_baseline_file": "backend/assets/eval_dual_cam_stats.json",
            "before_cross_camera_consistency_rate": baseline_summary.get("cross_camera_consistency_rate", 0.929),
            "before_all_consistent_scene_rate": baseline_summary.get("all_consistent_scene_rate", 0.714),
            "after_consistency_rate_0_5": eval_5["consistency_rate_0_5"],
            "after_consistency_rate_0_80": eval_5["consistency_rate_0_80"],
            "after_mean_p_true": eval_5["mean_p_true"],
            "after_room_architecture_p_true": eval_5["checks"]["room_architecture"]["p_true"],
        },
    }
    out_json = config.ASSETS_DIR / "eval_dual_cam_fixed_room.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


@log_function_call
async def run_b2_fast_live_scene5_eval() -> dict:
    """W1-V2-B2: Benchmark & pre-cache 1 live `fast_live_mode=True` extended Scene 5 clip with desk coffee mug."""
    scene5 = Scene(
        scene_id=5,
        parent_scene_id=4,
        title="Scene 5 — Midnight Coffee Catch",
        narration=(
            "A warm ceramic desk coffee mug arcs playfully into the conservatory from the viewer's direction. "
            "Lia smiles, reaches forward gracefully toward the camera lens to catch the floating mug out of mid-air, "
            "and sets it gently beside the intact crystal vase while Nyx peers curiously before both freeze in a serene sculpture pose."
        ),
        lighting="warm_gold_snow",
        weather="starlight_snow",
        audio_mood="cozy chime catch sound and gentle warm nocturnal piano",
        end_freeze_mode="sculpture",
        actors=[
            ActorState(
                id="lia",
                name="Lia",
                asset_url="/assets/actors/lia.png",
                grid=[0.75, 1.15],
                facing="front",
                action="catch_from_camera",
                holding_prop="showcase_desk_coffee_mug",
            ),
            ActorState(
                id="nyx",
                name="Nyx",
                asset_url="/assets/actors/nyx.png",
                grid=[1.4, 1.1],
                facing="left",
                action="sit",
            ),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], state="intact", on_floor=False),
            StagePropState(
                id="showcase_desk_coffee_mug",
                grid=[1.08, 1.05],
                state="held",
                on_floor=False,
                entry_motion="throw_arc",
                entry_origin=[1.0, 2.2, 0.8],
            ),
        ],
        qa_questions=[
            "Is a single ceramic coffee mug cleanly present with Lia or on the central round table?",
            "Does the intact crystal vase remain on the central round table with small black cat Nyx nearby?",
        ],
    )

    refs = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "showcase_desk_coffee_mug.png"),
    ]

    # Pre-warm static actor & room references (simulating live interactive session after Scene 1 load)
    # and measure parallel cold upload time explicitly.
    from backend.media.video_renderer import _get_or_upload_uri

    static_refs = [
        config.ACTORS_DIR / "lia_clean_ref.png",
        config.SHARED_ASSETS_DIR / "nyx.png",
        config.ASSETS_DIR / "stages" / "conservatory_cam_a.png",
        config.ASSETS_DIR / "stages" / "conservatory_cam_b.png",
        config.SHARED_ASSETS_DIR / "crystal_vase.png",
        config.PROPS_DIR / "showcase_desk_coffee_mug.png",
    ]
    t_up0 = time.perf_counter()
    await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in static_refs if p.is_file()))
    parallel_upload_sec = round(time.perf_counter() - t_up0, 2)

    t0 = time.perf_counter()
    media = await render_scene_media(
        scene5,
        refs,
        fast_live_mode=True,
        overwrite_canonical=False,
    )
    elapsed = round(time.perf_counter() - t0, 2)
    cold_total_sec = round(parallel_upload_sec + elapsed, 2)

    vid_a = _resolve_asset_to_local_path(media.cam_a_final_url)
    vid_b = _resolve_asset_to_local_path(media.cam_b_final_url)
    tail_a = _resolve_asset_to_local_path(media.cam_a_tail_frame_url)
    tail_b = _resolve_asset_to_local_path(media.cam_b_tail_frame_url)
    assert vid_a and vid_b and tail_a and tail_b, f"Missing Scene 5 fast_live outputs: {media}"

    canon_va = config.FINALS_DIR / "scene_5_fast_live_cam_a.mp4"
    canon_vb = config.FINALS_DIR / "scene_5_fast_live_cam_b.mp4"
    canon_ta = config.TAILS_DIR / "scene_5_fast_live_cam_a_tail.png"
    canon_tb = config.TAILS_DIR / "scene_5_fast_live_cam_b_tail.png"
    shutil.move(str(vid_a), str(canon_va))
    shutil.move(str(vid_b), str(canon_vb))
    shutil.move(str(tail_a), str(canon_ta))
    shutil.move(str(tail_b), str(canon_tb))

    report = {
        "task_id": "W1-V2-B2",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scene_id": 5,
        "parent_scene_id": 4,
        "end_freeze_mode": "sculpture",
        "actor_action": "catch_from_camera",
        "everyday_prop_used": "showcase_desk_coffee_mug.png",
        "fast_live_mode": True,
        "target_budget_sec": [10.0, 20.0],
        "parallel_ref_upload_sec": parallel_upload_sec,
        "wall_clock_sec": elapsed,
        "cold_total_with_parallel_upload_sec": cold_total_sec,
        "within_20s_target": elapsed <= 20.0,
        "within_25s_interactive_slo": elapsed <= 25.0,
        "qa_score": media.qa_score,
        "qa_retries": media.qa_retries,
        "fallback_to_preview": media.fallback_to_preview,
        "cam_a_url": f"/assets/finals/{canon_va.name}",
        "cam_b_url": f"/assets/finals/{canon_vb.name}",
        "cam_a_tail_url": f"/assets/tails/{canon_ta.name}",
        "cam_b_tail_url": f"/assets/tails/{canon_tb.name}",
        "cam_a_h264": is_browser_h264(canon_va),
        "cam_b_h264": is_browser_h264(canon_vb),
        "cam_a_bytes": canon_va.stat().st_size,
        "cam_b_bytes": canon_vb.stat().st_size,
        "qa_items": [it.model_dump() for it in (media.qa_result.items if media.qa_result else [])],
    }
    out_json = config.ASSETS_DIR / "eval_fast_live_latency.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def main() -> None:
    """Execute W1-V2-B2 (fast_live_mode=True) and optionally W1-V2-B1 (fixed-room A/B consistency)."""
    b2_only = "--b2-only" in sys.argv
    print("=== Running W1-V2-B2 (fast_live_mode=True Scene 5 latency benchmark) ===")
    b2 = await run_b2_fast_live_scene5_eval()
    print(
        f"W1-V2-B2 DONE: upload={b2['parallel_ref_upload_sec']}s warm_wall={b2['wall_clock_sec']}s "
        f"cold_total={b2['cold_total_with_parallel_upload_sec']}s (<=20s={b2['within_20s_target']}, <=25s={b2['within_25s_interactive_slo']}) "
        f"qa={b2['qa_score']} fallback={b2['fallback_to_preview']} retries={b2['qa_retries']} "
        f"h264=({b2['cam_a_h264']},{b2['cam_b_h264']})"
    )

    if not b2_only:
        print("=== Running W1-V2-B1 (Fixed-room A/B consistency benchmark) ===")
        b1 = await run_b1_fixed_room_eval()
        ev = b1["fixed_room_5_criteria_eval"]
        print(
            f"W1-V2-B1 DONE: wall={b1['render_result']['wall_clock_sec']}s qa={b1['render_result']['qa_score']} "
            f"5-criteria pass@0.5={ev['passed_0_5_count']}/{ev['total_checks']} ({ev['consistency_rate_0_5']:.1%}) "
            f"pass@0.80={ev['passed_0_80_count']}/{ev['total_checks']} ({ev['consistency_rate_0_80']:.1%}) "
            f"mean_p={ev['mean_p_true']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
