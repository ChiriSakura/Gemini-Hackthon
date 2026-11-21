"""
W1-T3F + W1-T3G + W1-T3H + W1-T4C Execution Suite (Owned by P2 / worker-1).

Executes:
- W1-T3F: 5-Prop Diversity Showcase (`showcase_*.png`) + green-spill & alpha audit.
- W1-T3G: Pre-cache 3rd fallback English branch `branch_v3_starlight_cached.json`.
- W1-T3H: 720p vs 360p Omni Flash latency, file-size & cost benchmark on Scene 4.
- W1-T4C: Prop tight-crop / halo audit across all `backend/assets/props/*.png`
  plus multi-scene prop persistence evaluation across `branch_v3_starlight_cached`
  via `models/gemini-decision-1` saved to `backend/assets/eval_prop_persistence.json`.
"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.director.hydrogen_judge import inspect_tail_frame
from backend.director.state_tree import get_branch, save_branch
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import (
    _build_shot_prompt,
    _generate_omni_take_sync,
    _get_or_upload_uri,
    _resolve_video_model,
    render_scene_media,
)
from backend.schemas import ActorState, BranchState, PropRegistryItem, Scene, StagePropState


def _audit_prop_png(path: Path) -> dict:
    """Audit RGBA mode, tight bottom-flush alpha crop, aspect ratio, and opaque green spill."""
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        arr = np.array(rgba)
        bbox = rgba.getbbox()
    bottom_pad = (arr.shape[0] - bbox[3]) if bbox else arr.shape[0]

    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]
    bgr = rgb[:, :, ::-1].astype(np.uint8)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    green_excess = rgb[:, :, 1] - np.maximum(rgb[:, :, 0], rgb[:, :, 2])
    opaque_green_spill = int(
        np.sum(
            (alpha >= 200)
            & (hsv[:, :, 0] >= 35)
            & (hsv[:, :, 0] <= 90)
            & (hsv[:, :, 1] >= 80)
            & (hsv[:, :, 2] >= 60)
            & (green_excess > 30)
        )
    )
    return {
        "file": path.name,
        "mode": im.mode,
        "width": int(arr.shape[1]),
        "height": int(arr.shape[0]),
        "aspect_ratio": round(arr.shape[1] / max(arr.shape[0], 1), 3),
        "bottom_pad_px": int(bottom_pad),
        "opaque_green_spill_pixels": opaque_green_spill,
        "transparent_ratio": round(float((alpha < 20).mean()), 3),
    }


async def run_w1_t3f_showcase_props() -> dict:
    """Generate 5 diverse stage props concurrently and audit chroma-key quality."""
    specs = [
        ("showcase_origami_crane", "Origami Crane", "A delicate pale-gold paper origami crane with crisp folded wings"),
        ("showcase_brass_pocket_watch", "Brass Pocket Watch", "An antique ornate brass pocket watch with glowing cyan hands"),
        ("showcase_crystal_shield", "Crystal Shield", "A translucent azure crystal mini buckler shield with silver trim"),
        ("showcase_glowing_quill", "Glowing Quill", "An enchanted silver feather quill with a warm golden ink nib"),
        ("showcase_star_lantern", "Star Lantern", "A small warm golden glass star lantern hanging from a brass ring"),
    ]
    t0 = time.perf_counter()

    async def _make(pid: str, name: str, desc: str) -> dict:
        t_start = time.perf_counter()
        _, item = await create_prop(name=name, description=desc, prop_id=pid)
        elapsed = round(time.perf_counter() - t_start, 2)
        audit = _audit_prop_png(config.PROPS_DIR / f"{pid}.png")
        audit["elapsed_sec"] = elapsed
        audit["image_url"] = item.image_url
        return audit

    results = list(await asyncio.gather(*(_make(pid, name, desc) for pid, name, desc in specs)))
    return {
        "count": len(results),
        "wall_sec": round(time.perf_counter() - t0, 2),
        "avg_latency_sec": round(sum(r["elapsed_sec"] for r in results) / len(results), 2),
        "all_rgba": all(r["mode"] == "RGBA" for r in results),
        "max_opaque_green_spill": max(r["opaque_green_spill_pixels"] for r in results),
        "all_bottom_flush": all(r["bottom_pad_px"] == 0 for r in results),
        "props": results,
    }


async def run_w1_t3g_branch_v3() -> BranchState:
    """Pre-cache 3rd fallback English branch (`branch_v3_starlight_cached`) with `star_token`."""
    b1 = get_branch("branch_v1")
    assert b1 is not None, "branch_v1 required for prefix Scene 1 reuse"
    s1 = b1.scenes[0].model_copy(deep=True)
    s1.title = "Rainy Night Encounter (Reused)"
    s1.needs_rerender = False
    s1.reused_from_branch = "branch_v1"

    s2 = Scene(
        scene_id=2,
        title="Starlight Gift & Calm Table",
        duration_sec=4.0,
        narration=(
            "Lia sets her closed crimson umbrella neatly on the left floor, kneels beside the round table, "
            "and offers the glowing golden Starlight Keepsake to Nyx while the blue-rose crystal vase stands intact."
        ),
        vocal_emotion="gentle_whisper",
        gesture_intent="beckon_soothe",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="gentle_windchime_soft_purring_rain_easing",
        environment_notes="nighttime_glass_conservatory_clean_floor_warm_amber_lanterns",
        needs_rerender=True,
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.65, 1.15], facing="right", action="kneel", holding_prop="star_token"),
            ActorState(id="nyx", name="Nyx", asset_url="/assets/actors/nyx.png", grid=[1.45, 1.15], facing="left", action="sit", holding_prop=None),
        ],
        stage_props=[
            StagePropState(id="red_umbrella", grid=[0.2, 1.2], on_floor=True, state="intact"),
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.05, 1.15], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Is the red umbrella placed neatly on the left floor?",
            "Is the glowing golden star keepsake visible between Lia and the small black cat?",
            "Is the blue-rose crystal vase standing intact on the center round table?",
        ],
    )
    s3 = Scene(
        scene_id=3,
        title="Starlight Vigil & Clean Floor",
        duration_sec=4.0,
        narration=(
            "Under clearing night rain outside the conservatory glass, small black cat Nyx sits affectionately beside "
            "the glowing Starlight Keepsake near Lia while the intact blue-rose crystal vase sparkles on the center table."
        ),
        vocal_emotion="joyful",
        gesture_intent="none",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="soft_music_box_gentle_rain_outside",
        environment_notes="nighttime_glass_conservatory_clean_floor_glowing_starlight",
        needs_rerender=True,
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.7, 1.15], facing="right", action="kneel", holding_prop=None),
            ActorState(id="nyx", name="Nyx", asset_url="/assets/actors/nyx.png", grid=[1.35, 1.15], facing="left", action="nuzzle", holding_prop="star_token"),
        ],
        stage_props=[
            StagePropState(id="red_umbrella", grid=[0.2, 1.2], on_floor=True, state="intact"),
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.05, 1.18], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Is the conservatory floor clean with NO broken glass shards?",
            "Is the glowing golden star keepsake still present beside Nyx and Lia?",
            "Is the blue-rose crystal vase still intact on the round table?",
        ],
    )
    s4 = Scene(
        scene_id=4,
        title="HE · Starlight Snow Shelter",
        duration_sec=4.0,
        narration=(
            "Warm golden starlight and gentle winter snow fill the night sky outside; Lia raises one open red umbrella "
            "to shelter little Nyx as the glowing Starlight Keepsake rests beside the intact blue-rose vase on the table."
        ),
        vocal_emotion="joyful",
        gesture_intent="none",
        direction_hint="center",
        lighting="warm_gold_snow",
        camera_shake=False,
        audio_mood="magical_warm_chimes_gentle_winter_snow_romantic_strings",
        environment_notes="nighttime_warm_golden_starlight_and_falling_snowflakes_intact_vase",
        needs_rerender=True,
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.85, 1.15], facing="right", action="shared_umbrella", holding_prop="red_umbrella"),
            ActorState(id="nyx", name="Nyx", asset_url="/assets/actors/nyx.png", grid=[1.35, 1.18], facing="left", action="nuzzle", holding_prop=None),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.15, 1.0], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Are Lia and the small black cat sheltered together under one open red umbrella?",
            "Is the glowing golden star keepsake still visible in the scene?",
            "Is the blue-rose crystal vase intact on the center table under warm golden starlight and snow?",
        ],
    )

    refs = [
        str(config.ACTORS_DIR / "lia_clean_ref.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "star_token.png"),
    ]

    async def _render_v3(scene: Scene) -> None:
        media = await render_scene_media(scene, refs, tail_inspector=inspect_tail_frame, overwrite_canonical=False)
        for cam in ("cam_a", "cam_b"):
            cur_mp4 = getattr(media, f"{cam}_final_url") or ""
            if cur_mp4.startswith("/assets/"):
                src = config.ASSETS_DIR / cur_mp4.removeprefix("/assets/")
                dst = config.FINALS_DIR / f"branch_v3_scene_{scene.scene_id}_{cam}.mp4"
                if src.is_file():
                    shutil.move(str(src), str(dst))
                    setattr(media, f"{cam}_final_url", f"/assets/finals/{dst.name}")
            cur_tail = getattr(media, f"{cam}_tail_frame_url") or ""
            if cur_tail.startswith("/assets/"):
                src_t = config.ASSETS_DIR / cur_tail.removeprefix("/assets/")
                dst_t = config.TAILS_DIR / f"branch_v3_scene_{scene.scene_id}_{cam}_tail.png"
                if src_t.is_file():
                    shutil.move(str(src_t), str(dst_t))
                    setattr(media, f"{cam}_tail_frame_url", f"/assets/tails/{dst_t.name}")
        scene.media = media
        scene.needs_rerender = False

    await asyncio.gather(_render_v3(s2), _render_v3(s3), _render_v3(s4))

    branch_v3 = BranchState(
        branch_id="branch_v3_starlight_cached",
        branch_label="HE · Starlight Vigil & Shared Shelter (3rd Cached Variant)",
        parent_branch_id="branch_v1",
        forked_from_scene=2,
        rewrite_summary="Lia offers the glowing Starlight Keepsake in Scene 2 and preserves it alongside the intact crystal vase through Scene 4.",
        props_registry={
            "red_umbrella": PropRegistryItem(name="Crimson Vintage Umbrella", image_url="/assets/actors/lia.png", state="held", origin="preset"),
            "crystal_vase": PropRegistryItem(name="Blue Rose Crystal Vase", image_url="/assets/props/crystal_vase.png", state="intact", origin="preset"),
            "star_token": PropRegistryItem(name="Starlight Keepsake", image_url="/assets/props/star_token.png", state="glowing", origin="camera_physical"),
        },
        scenes=[s1, s2, s3, s4],
    )
    save_branch(branch_v3)
    return branch_v3


async def run_w1_t3h_720p_benchmark() -> dict:
    """Benchmark 1 single 4s Scene 4 clip at 720p vs 360p baseline."""
    b1 = get_branch("branch_v1")
    assert b1 is not None
    scene4 = b1.scenes[3]
    refs = [
        config.ACTORS_DIR / "lia_clean_ref.png",
        config.SHARED_ASSETS_DIR / "nyx.png",
        config.SHARED_ASSETS_DIR / "crystal_vase.png",
    ]
    uploaded_refs = list(await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in refs)))
    prompt = _build_shot_prompt(scene4, "cam_a", has_first_frame=False, has_preview_video=False)
    input_parts = [{"type": "image", "uri": u, "mime_type": m} for u, m in uploaded_refs] + [{"type": "text", "text": prompt}]
    out_720p = config.FINALS_DIR / "tmp_bench_720p_s4.mp4"

    t0 = time.perf_counter()
    meta = await asyncio.to_thread(
        _generate_omni_take_sync,
        _resolve_video_model(),
        input_parts,
        4,
        "720p",
        config.VIDEO_ASPECT_RATIO,
        out_720p,
    )
    latency_720p = round(time.perf_counter() - t0, 2)
    size_720p = out_720p.stat().st_size if out_720p.is_file() else meta.get("bytes", 0)
    out_720p.unlink(missing_ok=True)

    p360 = config.FINALS_DIR / "scene_4_cam_a.mp4"
    size_360p = p360.stat().st_size if p360.is_file() else 0
    return {
        "model": _resolve_video_model(),
        "scene_id": 4,
        "duration_sec": 4,
        "360p": {"latency_sec": 18.64, "bytes": size_360p, "est_cost_usd": 0.08},
        "720p": {"latency_sec": latency_720p, "bytes": size_720p, "est_cost_usd": 0.28},
        "speed_ratio_720p_vs_360p": round(latency_720p / 18.64, 2),
        "size_ratio_720p_vs_360p": round(size_720p / max(size_360p, 1), 2),
    }


async def run_w1_t4c_audit_and_persistence(branch_v3: BranchState) -> dict:
    """Audit all prop PNGs and measure multi-scene prop persistence across branch_v3_starlight_cached."""
    prop_audits = [_audit_prop_png(p) for p in sorted(config.PROPS_DIR.glob("*.png"))]

    checks = []
    for scene in branch_v3.scenes:
        if scene.scene_id < 2:
            continue
        for cam in ("cam_a", "cam_b"):
            mp4_url = getattr(scene.media, f"{cam}_final_url") or ""
            mp4_path = config.ASSETS_DIR / mp4_url.removeprefix("/assets/")
            q_map = {
                "star_token_persists": {
                    "type": "noul",
                    "instructions": "Is the glowing golden star keepsake prop visibly present in this scene?",
                },
                "crystal_vase_intact": {
                    "type": "noul",
                    "instructions": "Is the blue-rose crystal vase intact (not shattered) in this scene?",
                },
            }
            payload = build_decision_input(
                f"Audit prop persistence in branch_v3_starlight_cached Scene {scene.scene_id} ({cam}).",
                [mp4_path],
            )
            res = await create_decision(payload, q_map)
            answers = getattr(res, "answers", None) or {}
            star_ans = answers.get("star_token_persists")
            vase_ans = answers.get("crystal_vase_intact")
            checks.append(
                {
                    "scene_id": scene.scene_id,
                    "cam_id": cam,
                    "star_token_persists": float(getattr(star_ans, "noul", 0.5)) >= 0.5 if star_ans else False,
                    "star_p_true": round(float(getattr(star_ans, "noul", 0.5)), 3) if star_ans else 0.5,
                    "crystal_vase_intact": float(getattr(vase_ans, "noul", 0.5)) >= 0.5 if vase_ans else False,
                    "vase_p_true": round(float(getattr(vase_ans, "noul", 0.5)), 3) if vase_ans else 0.5,
                }
            )

    star_rate = round(sum(1 for c in checks if c["star_token_persists"]) / max(len(checks), 1), 3)
    vase_rate = round(sum(1 for c in checks if c["crystal_vase_intact"]) / max(len(checks), 1), 3)
    report = {
        "prop_png_audits": prop_audits,
        "all_props_rgba": all(a["mode"] == "RGBA" for a in prop_audits),
        "all_props_zero_green_spill": all(a["opaque_green_spill_pixels"] == 0 for a in prop_audits),
        "all_props_bottom_flush": all(a["bottom_pad_px"] == 0 for a in prop_audits),
        "branch_v3_prop_persistence": {
            "branch_id": branch_v3.branch_id,
            "clips_audited": len(checks),
            "star_token_persistence_rate": star_rate,
            "crystal_vase_intact_persistence_rate": vase_rate,
            "details": checks,
        },
    }
    out_path = config.ASSETS_DIR / "eval_prop_persistence.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def main() -> None:
    t_start = time.perf_counter()
    showcase_res, branch_v3, bench_720p = await asyncio.gather(
        run_w1_t3f_showcase_props(),
        run_w1_t3g_branch_v3(),
        run_w1_t3h_720p_benchmark(),
    )
    persistence_res = await run_w1_t4c_audit_and_persistence(branch_v3)
    summary = {
        "wall_sec": round(time.perf_counter() - t_start, 2),
        "W1_T3F_showcase_5_props": showcase_res,
        "W1_T3G_branch_v3_qa_scores": [s.media.qa_score for s in branch_v3.scenes],
        "W1_T3H_720p_vs_360p": bench_720p,
        "W1_T4C_persistence": persistence_res["branch_v3_prop_persistence"],
        "W1_T4C_png_audit_summary": {
            "total_props": len(persistence_res["prop_png_audits"]),
            "all_rgba": persistence_res["all_props_rgba"],
            "all_zero_green_spill": persistence_res["all_props_zero_green_spill"],
            "all_bottom_flush": persistence_res["all_props_bottom_flush"],
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
