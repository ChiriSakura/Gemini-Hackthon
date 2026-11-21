"""
W1-T3C + W1-T3D + W1-T3E Evaluation & Stress-Test Suite (Owned by P2 / worker-1).

Executes:
- W1-T3C: Cross-Camera Consistency Rate (`cam_a` vs `cam_b`) across `branch_v1`
  (Scenes 1..4) and `branch_v2_cached` (Scenes 2..4) via `models/gemini-decision-1`,
  saving results to `backend/assets/eval_dual_cam_stats.json`.
- W1-T3D: Multi-prop simultaneous stress test (`star_token` + `red_umbrella` on floor
  + `crystal_vase` intact on table) on live Omni Flash 360p 4s.
- W1-T3E: 20-clip Decisions API calibration dataset (14 real positive + 6 negative
  mismatch checks) saved to `backend/assets/calibration_20_clips.json`.
"""

import asyncio
import json
import time
from pathlib import Path

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.director.state_tree import get_branch
from backend.media.video_renderer import render_single_camera_shot
from backend.schemas import ActorState, Scene, StagePropState


async def run_w1_t3c_dual_cam() -> dict:
    """Measure cross-camera consistency across all 7 unique dual-cam scenes."""
    b1 = get_branch("branch_v1")
    b2 = get_branch("branch_v2_cached")
    assert b1 is not None and b2 is not None

    pairs: list[tuple[str, Scene]] = [(b1.branch_id, s) for s in b1.scenes] + [
        (b2.branch_id, s) for s in b2.scenes if s.scene_id >= 2
    ]

    sem = asyncio.Semaphore(4)

    async def _eval_pair(branch_id: str, scene: Scene) -> dict:
        async with sem:
            a_tail = config.ASSETS_DIR / (scene.media.cam_a_tail_frame_url or "").removeprefix("/assets/")
            b_tail = config.ASSETS_DIR / (scene.media.cam_b_tail_frame_url or "").removeprefix("/assets/")
            q_map = {
                "actor_presence": {
                    "type": "noul",
                    "instructions": "Do both Camera A (first image) and Camera B (second image) show the same characters (silver-haired girl and/or black cat) consistently?",
                },
                "spatial_layout": {
                    "type": "noul",
                    "instructions": "Are the characters' relative positions around the central round table spatially consistent between Camera A (wide) and Camera B (45-deg close-up)?",
                },
                "prop_state": {
                    "type": "noul",
                    "instructions": "Are the key prop states (red umbrella and crystal vase intact vs broken) consistent between both camera angles?",
                },
                "lighting_weather": {
                    "type": "noul",
                    "instructions": f"Do both camera angles share the same lighting and weather atmosphere ({scene.lighting})?",
                },
            }
            ctx = (
                f"Branch={branch_id}, Scene {scene.scene_id} ({scene.title}): {scene.narration}. "
                "Image 1 is Camera A front-wide; Image 2 is Camera B 45-degree close-up."
            )
            t0 = time.perf_counter()
            payload = build_decision_input(ctx, [a_tail, b_tail])
            res = await create_decision(payload, q_map)
            elapsed = round(time.perf_counter() - t0, 2)
            answers = getattr(res, "answers", None) or {}
            checks = {}
            passed_cnt = 0
            for k in q_map:
                ans = answers.get(k)
                p_true = round(float(getattr(ans, "noul", 0.5)), 3) if ans is not None else 0.5
                conf = round(float(getattr(ans, "confidence", max(p_true, 1.0 - p_true))), 3) if ans is not None else 0.5
                passed = p_true >= 0.5
                if passed:
                    passed_cnt += 1
                checks[k] = {"p_true": p_true, "confidence": conf, "passed": passed}
            rate = round(passed_cnt / len(q_map), 3)
            return {
                "branch_id": branch_id,
                "scene_id": scene.scene_id,
                "title": scene.title,
                "cam_a_tail": scene.media.cam_a_tail_frame_url,
                "cam_b_tail": scene.media.cam_b_tail_frame_url,
                "elapsed_sec": elapsed,
                "checks": checks,
                "scene_consistency_rate": rate,
                "all_consistent": passed_cnt == len(q_map),
            }

    results = list(await asyncio.gather(*(_eval_pair(bid, s) for bid, s in pairs)))
    total_checks = len(results) * 4
    total_passed = sum(sum(1 for c in r["checks"].values() if c["passed"]) for r in results)
    summary = {
        "model": config.MODEL_DECISION,
        "total_scene_pairs": len(results),
        "total_checks": total_checks,
        "passed_checks": total_passed,
        "cross_camera_consistency_rate": round(total_passed / total_checks, 3) if total_checks else 0.0,
        "all_consistent_scene_rate": round(sum(1 for r in results if r["all_consistent"]) / len(results), 3),
        "avg_latency_sec": round(sum(r["elapsed_sec"] for r in results) / len(results), 2),
        "scenes": results,
    }
    out_path = config.ASSETS_DIR / "eval_dual_cam_stats.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


async def run_w1_t3d_multi_prop_stress() -> dict:
    """Run 1 live Omni Flash 360p 4s shot with 3 simultaneous props."""
    stress_scene = Scene(
        scene_id=2,
        title="多道具压力测试（三道具同屏）",
        duration_sec=4.0,
        narration=(
            "暖金微光下，莉亚把亮红长柄伞轻放在左侧地板上，半蹲着双手捧起发光的金色星光信物递给小黑猫诺克斯；"
            "同时中央圆桌上稳稳立着完好无损的蓝玫瑰水晶花瓶。"
        ),
        vocal_emotion="gentle_whisper",
        gesture_intent="beckon_soothe",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="gentle_windchime_soft_purring_warm_ambience",
        environment_notes="glass_conservatory_clean_floor_three_simultaneous_props",
        needs_rerender=True,
        actors=[
            ActorState(
                id="lia",
                name="莉亚",
                asset_url="/assets/actors/lia.png",
                grid=[0.75, 1.15],
                facing="right",
                action="kneel",
                holding_prop="star_token",
            ),
            ActorState(
                id="nyx",
                name="诺克斯",
                asset_url="/assets/actors/nyx.png",
                grid=[1.35, 1.15],
                facing="left",
                action="sit",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="red_umbrella", grid=[0.2, 1.2], on_floor=True, state="intact"),
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.05, 1.15], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Is the red umbrella lying on the floor to the left?",
            "Is the crystal vase with blue rose standing intact on the center round table?",
            "Is a glowing golden star token visible between the girl and the black cat?",
            "Is the silver-haired girl kneeling calmly while the small black cat approaches?",
        ],
    )
    refs = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "star_token.png"),
    ]
    t0 = time.perf_counter()
    final_url, tail_url, qa_res, selected_idx, retries = await render_single_camera_shot(
        scene=stress_scene,
        cam_id="cam_a",
        prev_tail_path=None,
        reference_images=refs,
        preview_path=None,
        extra_constraint=(
            "Ensure all three props appear simultaneously and clearly: "
            "(1) closed red umbrella resting flat on floor left, "
            "(2) intact crystal vase with blue rose on center round table, "
            "(3) glowing golden star token held gently between Lia and Nyx."
        ),
        overwrite_canonical=False,
    )
    elapsed = round(time.perf_counter() - t0, 2)
    # Clean up the temporary stress mp4/tail so repo size stays lean after logging results.
    for url in (final_url, tail_url):
        if url and url.startswith("/assets/"):
            p = config.ASSETS_DIR / url.removeprefix("/assets/")
            p.unlink(missing_ok=True)
    return {
        "props_tested": ["star_token", "red_umbrella", "crystal_vase"],
        "elapsed_sec": elapsed,
        "qa_score": qa_res.overall_score,
        "all_passed": qa_res.all_passed,
        "selected_candidate_index": selected_idx,
        "retries": retries,
        "items": [it.model_dump() for it in qa_res.items],
    }


async def run_w1_t3e_calibration_20() -> dict:
    """Build 20-clip Decisions API calibration dataset (14 positive + 6 negative mismatch)."""
    cases = [
        # 14 real positive checks (8 branch_v1 + 6 branch_v2_cached)
        ("branch_v1", 1, "cam_a", "/assets/finals/scene_1_cam_a.mp4", "positive", "Is the silver-haired girl standing on the left holding a red umbrella in a glass conservatory?", True),
        ("branch_v1", 1, "cam_b", "/assets/finals/scene_1_cam_b.mp4", "positive", "Is a crystal vase with a blue rose standing intact on the round table?", True),
        ("branch_v1", 2, "cam_a", "/assets/finals/scene_2_cam_a.mp4", "positive", "Is the crystal vase broken on the floor under cold rainy blue night lighting?", True),
        ("branch_v1", 2, "cam_b", "/assets/finals/scene_2_cam_b.mp4", "positive", "Is the silver-haired girl holding her red umbrella while the black cat reacts near the table?", True),
        ("branch_v1", 3, "cam_a", "/assets/finals/scene_3_cam_a.mp4", "positive", "Are shattered crystal glass shards or blue petals visible on the rainy conservatory floor?", True),
        ("branch_v1", 3, "cam_b", "/assets/finals/scene_3_cam_b.mp4", "positive", "Is the lighting cold stormy blue rain at night?", True),
        ("branch_v1", 4, "cam_a", "/assets/finals/scene_4_cam_a.mp4", "positive", "Is the silver-haired girl standing under a red umbrella with broken vase remains on the floor?", True),
        ("branch_v1", 4, "cam_b", "/assets/finals/scene_4_cam_b.mp4", "positive", "Is the mood melancholic cold blue rain rather than warm sunny daylight?", True),
        ("branch_v2_cached", 2, "cam_a", "/assets/finals/branch_v2_scene_2_cam_a.mp4", "positive", "Is the crystal vase with blue rose intact on the round table (not broken)?", True),
        ("branch_v2_cached", 2, "cam_b", "/assets/finals/branch_v2_scene_2_cam_b.mp4", "positive", "Is the silver-haired girl interacting gently with the black cat near a glowing golden star?", True),
        ("branch_v2_cached", 3, "cam_a", "/assets/finals/branch_v2_scene_3_cam_a.mp4", "positive", "Is the conservatory floor clean without shattered crystal vase shards?", True),
        ("branch_v2_cached", 3, "cam_b", "/assets/finals/branch_v2_scene_3_cam_b.mp4", "positive", "Is the glowing star token visible with the girl and the small black cat?", True),
        ("branch_v2_cached", 4, "cam_a", "/assets/finals/branch_v2_scene_4_cam_a.mp4", "positive", "Is the atmosphere warm golden starlight with gentle falling snow?", True),
        ("branch_v2_cached", 4, "cam_b", "/assets/finals/branch_v2_scene_4_cam_b.mp4", "positive", "Are the silver-haired girl and small black cat warmly together under the open red umbrella with an intact blue-rose vase on the table?", True),
        # 6 negative / edge mismatched-prompt checks (ground truth = False)
        ("branch_v1", 4, "cam_a", "/assets/finals/scene_4_cam_a.mp4", "negative_mismatch", "Is the atmosphere warm golden starlight with gently falling snow and a joyful smile?", False),
        ("branch_v1", 3, "cam_a", "/assets/finals/scene_3_cam_a.mp4", "negative_mismatch", "Is the crystal vase standing completely intact and unbroken on top of the round table?", False),
        ("branch_v2_cached", 4, "cam_b", "/assets/finals/branch_v2_scene_4_cam_b.mp4", "negative_mismatch", "Is the crystal vase shattered into broken shards across a cold stormy blue rainy floor?", False),
        ("branch_v1", 1, "cam_a", "/assets/finals/scene_1_cam_a.mp4", "negative_mismatch", "Are there three or more human characters standing together on a sunny sandy beach?", False),
        ("branch_v2_cached", 2, "cam_a", "/assets/finals/branch_v2_scene_2_cam_a.mp4", "negative_mismatch", "Is a large red fire truck driving across a busy city highway?", False),
        ("branch_v1", 2, "cam_b", "/assets/finals/scene_2_cam_b.mp4", "negative_mismatch", "Is the silver-haired girl holding a glowing yellow surfboard in bright noon sunlight?", False),
    ]

    sem = asyncio.Semaphore(5)

    async def _run_case(idx: int, row: tuple) -> dict:
        branch_id, scene_id, cam_id, clip_url, check_type, question, human_label = row
        async with sem:
            local_mp4 = config.ASSETS_DIR / clip_url.removeprefix("/assets/")
            t0 = time.perf_counter()
            payload = build_decision_input(
                f"Evaluate video clip {clip_url} honestly against the yes/no question.",
                [local_mp4],
            )
            res = await create_decision(payload, {"q1": {"type": "noul", "instructions": question}})
            elapsed = round(time.perf_counter() - t0, 2)
            ans = (getattr(res, "answers", None) or {}).get("q1")
            p_true = round(float(getattr(ans, "noul", 0.5)), 3) if ans is not None else 0.5
            conf = round(float(getattr(ans, "confidence", max(p_true, 1.0 - p_true))), 3) if ans is not None else 0.5
            pred = p_true >= 0.5
            return {
                "id": idx + 1,
                "branch_id": branch_id,
                "scene_id": scene_id,
                "cam_id": cam_id,
                "clip_url": clip_url,
                "check_type": check_type,
                "question": question,
                "p_true": p_true,
                "confidence": conf,
                "predicted_label": pred,
                "human_label": human_label,
                "correct": pred == human_label,
                "elapsed_sec": elapsed,
            }

    items = list(await asyncio.gather(*(_run_case(i, row) for i, row in enumerate(cases))))
    pos_items = [x for x in items if x["human_label"]]
    neg_items = [x for x in items if not x["human_label"]]
    summary = {
        "model": config.MODEL_DECISION,
        "total_clips": len(items),
        "positive_count": len(pos_items),
        "negative_count": len(neg_items),
        "accuracy": round(sum(1 for x in items if x["correct"]) / len(items), 3),
        "true_positive_rate": round(sum(1 for x in pos_items if x["correct"]) / len(pos_items), 3),
        "true_negative_rate": round(sum(1 for x in neg_items if x["correct"]) / len(neg_items), 3),
        "avg_latency_sec": round(sum(x["elapsed_sec"] for x in items) / len(items), 2),
        "items": items,
    }
    out_path = config.ASSETS_DIR / "calibration_20_clips.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


async def main() -> None:
    t_all = time.perf_counter()
    dual_cam, multi_prop, calib = await asyncio.gather(
        run_w1_t3c_dual_cam(),
        run_w1_t3d_multi_prop_stress(),
        run_w1_t3e_calibration_20(),
    )
    out = {
        "total_wall_sec": round(time.perf_counter() - t_all, 2),
        "W1_T3C_dual_cam": {
            "cross_camera_consistency_rate": dual_cam["cross_camera_consistency_rate"],
            "all_consistent_scene_rate": dual_cam["all_consistent_scene_rate"],
            "avg_latency_sec": dual_cam["avg_latency_sec"],
            "passed_checks": f"{dual_cam['passed_checks']}/{dual_cam['total_checks']}",
        },
        "W1_T3D_multi_prop_stress": multi_prop,
        "W1_T3E_calibration_20": {
            "accuracy": calib["accuracy"],
            "true_positive_rate": calib["true_positive_rate"],
            "true_negative_rate": calib["true_negative_rate"],
            "avg_latency_sec": calib["avg_latency_sec"],
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
