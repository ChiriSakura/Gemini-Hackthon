"""
W1-T3F + W1-T3G + W1-T3H + W1-T4C Live Media Benchmark, Pre-Cache & Eval Suite (Owned by P2 / worker-1).

Feature & Use Cases:
- W1-T3F: Runs `create_prop()` concurrently across 5 diverse stage props
  (`showcase_origami_crane`, `showcase_brass_pocket_watch`, `showcase_crystal_shield`,
  `showcase_glowing_quill`, `showcase_star_lantern`), cleans residual chroma-key
  edge fringe, and verifies RGBA mode, tight alpha bbox, 0 green spill, and latency.
- W1-T3G: Pre-caches a 3rd fallback branch (`branch_v3_starlight_cached.json`)
  introducing `star_token` in Scene 2 and carrying it through Scene 4 (reusing
  Scene 1 verbatim from `branch_v1`), saving H.264 MP4s (`branch_v3_scene_*`)
  and tail PNGs without clobbering canonical `branch_v1` files.
- W1-T3H: Benchmarks Omni Flash `720p` vs `360p` on a 4s Scene 4 clip (wall-clock
  latency, file size, resolution, H.264 codec, and $0.07/s vs $0.02/s cost tradeoff).
- W1-T4C: Evaluates multi-scene prop persistence across `branch_v1`,
  `branch_v2_cached`, and `branch_v3_starlight_cached` via `models/gemini-decision-1`
  and exports `backend/assets/eval_prop_persistence.json`.
"""

import argparse
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

import cv2
import numpy as np
from google import genai
from google.genai import types
from PIL import Image

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.director.hydrogen_judge import inspect_tail_frame
from backend.director.state_tree import get_branch, save_branch
from backend.logger import log_function_call, log_genai_call
from backend.media.audit_props import audit_single_prop, sanitize_prop_png
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import (
    _build_shot_prompt,
    _get_or_upload_uri,
    _open_video_capture_safe,
    _resolve_video_model,
    is_browser_h264,
    render_scene_media,
)
from backend.schemas import ActorState, BranchState, PropRegistryItem, Scene, StagePropState

SHOWCASE_PROPS: list[tuple[str, str, str]] = [
    (
        "showcase_origami_crane",
        "Origami Crane",
        "Delicate folded pearlescent white paper origami crane with gold starlight foil trim along its wings",
    ),
    (
        "showcase_brass_pocket_watch",
        "Brass Pocket Watch",
        "Antique ornate golden brass pocket watch with crescent moon engraving, glowing dial, and delicate brass chain",
    ),
    (
        "showcase_crystal_shield",
        "Crystal Shield",
        "Faceted translucent ice-blue crystal kite shield framed with silver filigree and a star crest",
    ),
    (
        "showcase_glowing_quill",
        "Glowing Quill",
        "Luminous silver-and-amber phoenix feather writing quill with a warm golden starlight nib",
    ),
    (
        "showcase_star_lantern",
        "Star Lantern",
        "Ornate dark wrought-iron handheld glass lantern glowing warmly with golden starlight candlelight inside",
    ),
]


def _resolve_lia_ref_for_v3() -> str:
    """Use umbrella-free `lia_clean_ref.png` if available locally, else `shared/assets/lia.png`."""
    candidates = [
        config.ACTORS_DIR / "lia_clean_ref.png",
        config.BASE_DIR.parent / "StageState" / "backend" / "assets" / "actors" / "lia_clean_ref.png",
        config.SHARED_ASSETS_DIR / "lia.png",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(config.SHARED_ASSETS_DIR / "lia.png")


def _probe_video_meta(video_path: Path) -> dict:
    """Return resolution, fps, frame count, file size, and H.264 check for a local MP4."""
    cap, tmp_path = _open_video_capture_safe(video_path)
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = round(float(cap.get(cv2.CAP_PROP_FPS) or 0.0), 2)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
    size_bytes = video_path.stat().st_size if video_path.is_file() else 0
    return {
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
        "fps": fps,
        "frame_count": frames,
        "duration_sec": round(frames / fps, 2) if fps > 0 else 0.0,
        "file_size_bytes": size_bytes,
        "file_size_mb": round(size_bytes / (1024 * 1024), 3),
        "is_h264": is_browser_h264(video_path),
    }


@log_function_call
async def run_w1_t3f_prop_showcase() -> dict:
    """W1-T3F: Generate 5 diverse stage props via `create_prop()` and verify RGBA/chroma-key metrics."""
    t_all = time.perf_counter()

    async def _gen_one(prop_id: str, name: str, desc: str) -> dict:
        t0 = time.perf_counter()
        resolved_id, item = await create_prop(name=name, description=desc, prop_id=prop_id)
        elapsed = round(time.perf_counter() - t0, 2)
        png_path = config.PROPS_DIR / f"{resolved_id}.png"

        # Check raw output before edge cleanup
        raw_audit = audit_single_prop(png_path)
        # Ensure clean bottom-flush & zero translucent green halo if fringe remained
        if not raw_audit["passed"]:
            final_audit = sanitize_prop_png(png_path)
        else:
            final_audit = raw_audit

        # Verify image is rich generated art (not the 256x256 solid gold placeholder fallback)
        with Image.open(png_path) as im:
            rgb_arr = np.array(im.convert("RGB"))
            unique_colors = int(len(np.unique(rgb_arr.reshape(-1, 3), axis=0)))

        return {
            "prop_id": resolved_id,
            "name": name,
            "image_url": item.image_url,
            "elapsed_sec": elapsed,
            "unique_rgb_colors": unique_colors,
            "is_real_generation": unique_colors > 50,
            "raw_opaque_green_spill_px": raw_audit["opaque_green_spill_px"],
            "raw_halo_green_pixels": raw_audit["halo_green_pixels"],
            **final_audit,
        }

    items = list(await asyncio.gather(*(_gen_one(pid, name, desc) for pid, name, desc in SHOWCASE_PROPS)))
    avg_latency = round(sum(x["elapsed_sec"] for x in items) / len(items), 2) if items else 0.0
    summary = {
        "task_id": "W1-T3F",
        "model": config.MODEL_IMAGE,
        "total_props": len(items),
        "wall_clock_sec": round(time.perf_counter() - t_all, 2),
        "avg_latency_sec": avg_latency,
        "all_real_generations": all(x["is_real_generation"] for x in items),
        "all_rgba": all(x["mode"] == "RGBA" and x["has_transparency"] for x in items),
        "all_zero_opaque_green_spill": all(x["opaque_green_spill_px"] == 0 for x in items),
        "all_zero_green_halo": all(x["halo_green_pixels"] == 0 for x in items),
        "all_bottom_flush_ok": all(x["bottom_flush_ok"] for x in items),
        "items": items,
    }
    out_path = config.ASSETS_DIR / "eval_prop_showcase.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _build_branch_v3_template(branch_v1: BranchState) -> BranchState:
    """Construct the 4-scene twist branch (`branch_v3_starlight_cached`) reusing Scene 1 from `branch_v1`."""
    b1_s1 = next(s for s in branch_v1.scenes if s.scene_id == 1)
    reused_s1 = b1_s1.model_copy(deep=True)
    reused_s1.title = "雨夜初遇（复用缓存）"
    reused_s1.needs_rerender = False
    reused_s1.reused_from_branch = "branch_v1"
    reused_s1.qa_questions = ["Prefix scene reused verbatim from branch_v1 (0 rerender cost)."]

    scene_2 = Scene(
        scene_id=2,
        title="星光落桌 · 稳住花瓶",
        duration_sec=4.0,
        narration=(
            "Nighttime rainy glass conservatory lit by warm amber wall lanterns (NOT daytime). "
            "Lia sets her single closed red umbrella flat on the left floor, leans calmly toward the center round table, "
            "and places the glowing golden star_token right beside the intact clear crystal vase holding a glowing blue rose. "
            "Small black cat Nyx stops jumping and gazes curiously at the glowing golden star_token on the table—the crystal vase stays 100% upright and unbroken!"
        ),
        vocal_emotion="gentle_whisper",
        gesture_intent="show_physical_prop",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="gentle_chimes_soft_rain_curious_cat_purr",
        environment_notes=(
            "nighttime_glass_conservatory_dark_rainy_sky_outside warm_amber_lantern_glow "
            "single_closed_red_umbrella_on_left_floor intact_crystal_vase_with_blue_rose_on_center_table "
            "glowing_golden_star_token_on_center_table clean_reflective_tiled_floor"
        ),
        needs_rerender=True,
        actors=[
            ActorState(
                id="lia",
                name="莉亚",
                asset_url="/assets/actors/lia.png",
                grid=[0.65, 1.05],
                facing="right",
                action="reach",
                holding_prop="star_token",
            ),
            ActorState(
                id="nyx",
                name="诺克斯",
                asset_url="/assets/actors/nyx.png",
                grid=[1.45, 1.05],
                facing="left",
                action="sit",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="red_umbrella", grid=[0.2, 1.2], on_floor=True, state="intact"),
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.08, 1.02], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Is the glowing golden star token clearly visible on or over the center round table?",
            "Is the crystal vase with a blue rose standing completely intact on the center round table?",
            "Is the scene set inside a nighttime glass conservatory rather than bright sunny daylight?",
            "Is the small black cat watching calmly instead of knocking over the vase?",
        ],
    )

    scene_3 = Scene(
        scene_id=3,
        title="星光共鸣 · 蓝玫映辉",
        duration_sec=4.0,
        narration=(
            "Nighttime glass conservatory with clean wet-free tiled floor and ZERO broken glass. "
            "The glowing golden star_token shines brightly on the center round table beside the intact blue-rose crystal vase, "
            "casting soft golden light across the tabletop. Small black cat Nyx trots gently beside Lia's starry cloak, "
            "nuzzling her hand near the glowing star_token as the first delicate snowflakes begin falling outside the dark glass windows."
        ),
        vocal_emotion="joyful",
        gesture_intent="beckon_soothe",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="warm_celesta_chimes_soft_purring_night_snow_breeze",
        environment_notes=(
            "nighttime_glass_conservatory_dark_starry_windows_first_snowflakes "
            "clean_floor_zero_broken_glass single_closed_red_umbrella_on_left_floor "
            "intact_crystal_vase_with_blue_rose_and_glowing_golden_star_token_together_on_center_table"
        ),
        needs_rerender=True,
        actors=[
            ActorState(
                id="lia",
                name="莉亚",
                asset_url="/assets/actors/lia.png",
                grid=[0.78, 1.12],
                facing="right",
                action="kneel",
                holding_prop=None,
            ),
            ActorState(
                id="nyx",
                name="诺克斯",
                asset_url="/assets/actors/nyx.png",
                grid=[1.22, 1.15],
                facing="left",
                action="nuzzle",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="red_umbrella", grid=[0.2, 1.2], on_floor=True, state="intact"),
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.08, 1.02], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Is the glowing golden star token persistently visible beside the intact blue-rose crystal vase on the round table?",
            "Is the conservatory floor completely clean with NO shattered glass shards?",
            "Are the silver-haired girl and small black cat warmly together inside the nighttime conservatory?",
        ],
    )

    scene_4 = Scene(
        scene_id=4,
        title="TWIST · 伞下星雪与双辉",
        duration_sec=4.0,
        narration=(
            "Warm golden starlight fills the nighttime glass conservatory as gentle luminous snow drifts outside the dark glass dome. "
            "Lia holds exactly ONE single open red umbrella over herself and small black cat Nyx beside the center round table, "
            "smiling warmly as the glowing golden star_token radiates beside the intact blue-rose crystal vase on the tabletop."
        ),
        vocal_emotion="joyful",
        gesture_intent="none",
        direction_hint="center",
        lighting="warm_gold_snow",
        camera_shake=False,
        audio_mood="magical_warm_harp_chimes_gentle_night_snowfall",
        environment_notes=(
            "nighttime_glass_conservatory_warm_golden_starlight_and_falling_snow_outside "
            "single_open_red_umbrella_held_by_lia_over_nyx "
            "intact_crystal_vase_with_blue_rose_and_glowing_golden_star_token_on_center_round_table"
        ),
        needs_rerender=True,
        actors=[
            ActorState(
                id="lia",
                name="莉亚",
                asset_url="/assets/actors/lia.png",
                grid=[0.88, 1.15],
                facing="right",
                action="shared_umbrella",
                holding_prop="red_umbrella",
            ),
            ActorState(
                id="nyx",
                name="诺克斯",
                asset_url="/assets/actors/nyx.png",
                grid=[1.22, 1.18],
                facing="left",
                action="nuzzle",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
            StagePropState(id="star_token", grid=[1.08, 1.02], on_floor=False, state="glowing"),
        ],
        qa_questions=[
            "Are the silver-haired girl and small black cat warmly sheltered together under a single open red umbrella?",
            "Is the glowing golden star token persistently visible alongside the intact blue-rose crystal vase on the round table?",
            "Is the atmosphere warm golden nighttime starlight with gently falling snow?",
        ],
    )

    return BranchState(
        branch_id="branch_v3_starlight_cached",
        branch_label="TWIST · 星光信物护瓶与夜雪共赏（第3缓存分支）",
        parent_branch_id="branch_v1",
        forked_from_scene=2,
        rewrite_summary=(
            "Judge holds up physical Starlight Token at Scene 2: Lia places the glowing golden star_token "
            "beside the intact blue-rose crystal_vase on the center table, soothing Nyx and persisting across Scenes 2..4."
        ),
        props_registry={
            "red_umbrella": PropRegistryItem(
                name="亮红复古长柄伞",
                image_url="/assets/actors/lia.png",
                state="held",
                origin="preset",
            ),
            "crystal_vase": PropRegistryItem(
                name="蓝玫瑰水晶花瓶",
                image_url="/assets/props/crystal_vase.png",
                state="intact",
                origin="preset",
            ),
            "star_token": PropRegistryItem(
                name="现场星光信物",
                image_url="/assets/props/star_token.png",
                state="glowing",
                origin="camera_physical",
            ),
        },
        scenes=[reused_s1, scene_2, scene_3, scene_4],
    )


@log_function_call
async def run_w1_t3g_cache_branch_v3() -> dict:
    """W1-T3G: Render Scenes 2..4 of `branch_v3_starlight_cached` without overwriting `branch_v1` files."""
    t0 = time.perf_counter()
    b1 = get_branch("branch_v1")
    assert b1 is not None, "branch_v1 must exist before caching branch_v3_starlight_cached"

    branch_v3 = _build_branch_v3_template(b1)
    refs_v3 = [
        _resolve_lia_ref_for_v3(),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        str(config.PROPS_DIR / "star_token.png"),
    ]

    async def _render_v3_scene(scene: Scene) -> dict:
        ts = time.perf_counter()
        media = await render_scene_media(
            scene,
            refs_v3,
            tail_inspector=inspect_tail_frame,
            overwrite_canonical=False,
        )
        # Move uniquely tagged outputs to clean canonical `branch_v3_scene_{id}_{cam}.mp4` names
        for cam in ("cam_a", "cam_b"):
            vid_url = getattr(media, f"{cam}_final_url")
            if vid_url and vid_url.startswith("/assets/"):
                src_mp4 = config.ASSETS_DIR / vid_url.removeprefix("/assets/")
                dst_mp4 = config.FINALS_DIR / f"branch_v3_scene_{scene.scene_id}_{cam}.mp4"
                if src_mp4.is_file():
                    shutil.move(str(src_mp4), str(dst_mp4))
                    setattr(media, f"{cam}_final_url", f"/assets/finals/{dst_mp4.name}")
            tail_url = getattr(media, f"{cam}_tail_frame_url")
            if tail_url and tail_url.startswith("/assets/"):
                src_tail = config.ASSETS_DIR / tail_url.removeprefix("/assets/")
                dst_tail = config.TAILS_DIR / f"branch_v3_scene_{scene.scene_id}_{cam}_tail.png"
                if src_tail.is_file():
                    shutil.move(str(src_tail), str(dst_tail))
                    setattr(media, f"{cam}_tail_frame_url", f"/assets/tails/{dst_tail.name}")

        scene.media = media
        scene.needs_rerender = False
        return {
            "scene_id": scene.scene_id,
            "elapsed_sec": round(time.perf_counter() - ts, 2),
            "qa_score": media.qa_score,
            "qa_retries": media.qa_retries,
            "cam_a_h264": is_browser_h264(config.FINALS_DIR / f"branch_v3_scene_{scene.scene_id}_cam_a.mp4"),
            "cam_b_h264": is_browser_h264(config.FINALS_DIR / f"branch_v3_scene_{scene.scene_id}_cam_b.mp4"),
        }

    scene_stats = list(await asyncio.gather(*(_render_v3_scene(s) for s in branch_v3.scenes if s.scene_id >= 2)))
    save_branch(branch_v3)

    return {
        "task_id": "W1-T3G",
        "branch_id": branch_v3.branch_id,
        "branch_json": f"backend/assets/branches/{branch_v3.branch_id}.json",
        "reused_prefix_scenes": [1],
        "rendered_scenes": [2, 3, 4],
        "new_mp4_count": 6,
        "all_h264": all(s["cam_a_h264"] and s["cam_b_h264"] for s in scene_stats),
        "avg_qa_score": round(sum(s["qa_score"] for s in scene_stats) / len(scene_stats), 3),
        "wall_clock_sec": round(time.perf_counter() - t0, 2),
        "scenes": scene_stats,
    }


def _run_single_resolution_omni_sync(
    model_id: str,
    input_parts: list[dict],
    resolution: str,
    out_path: Path,
    duration_sec: int = 4,
) -> dict:
    """Execute one timed Omni Flash generation + download call at `resolution` (`360p` or `720p`)."""
    client = genai.Client(http_options=types.HttpOptions(timeout=180_000))
    t0 = time.perf_counter()
    interaction = client.interactions.create(
        model=model_id,
        input=input_parts,
        response_format={
            "type": "video",
            "delivery": "uri",
            "aspect_ratio": config.VIDEO_ASPECT_RATIO,
            "duration": f"{duration_sec}s",
            "resolution": resolution,
        },
    )
    t_gen = round(time.perf_counter() - t0, 2)
    output_video = getattr(interaction, "output_video", None)
    if not output_video or not getattr(output_video, "uri", None):
        raise RuntimeError(f"Omni Flash ({resolution}) returned no output_video.")

    t_dl0 = time.perf_counter()
    video_bytes = client.files.download(file=output_video.uri)
    t_dl = round(time.perf_counter() - t_dl0, 2)
    t_total = round(time.perf_counter() - t0, 2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(video_bytes)
    meta = _probe_video_meta(out_path)
    log_genai_call(
        model_id,
        {"benchmark": "720p_vs_360p", "resolution": resolution, "duration": f"{duration_sec}s"},
        {"aspect_ratio": config.VIDEO_ASPECT_RATIO},
        {"interaction_id": getattr(interaction, "id", None), "gen_sec": t_gen, "download_sec": t_dl, **meta},
    )
    return {
        "resolution_requested": resolution,
        "interaction_id": getattr(interaction, "id", None),
        "generation_latency_sec": t_gen,
        "download_latency_sec": t_dl,
        "total_wall_clock_sec": t_total,
        **meta,
    }


@log_function_call
async def run_w1_t3h_720p_vs_360p_bench() -> dict:
    """W1-T3H: Benchmark 1 single 4s Scene 4 clip at `720p` vs `360p` (latency, size, $0.07/s vs $0.02/s)."""
    b1 = get_branch("branch_v1")
    assert b1 is not None
    scene_4 = next(s for s in b1.scenes if s.scene_id == 4)
    model_id = _resolve_video_model()

    ref_paths = [
        Path(config.SHARED_ASSETS_DIR / "lia.png"),
        Path(config.SHARED_ASSETS_DIR / "nyx.png"),
        Path(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
    ]
    uploaded = list(await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in ref_paths)))
    prompt = _build_shot_prompt(
        scene=scene_4,
        cam_id="cam_a",
        has_first_frame=False,
        has_preview_video=False,
    )
    input_parts: list[dict] = [{"type": "image", "uri": u, "mime_type": m} for u, m in uploaded]
    input_parts.append({"type": "text", "text": prompt})

    tmp_720 = config.FINALS_DIR / "_tmp_bench_scene4_720p.mp4"
    tmp_360 = config.FINALS_DIR / "_tmp_bench_scene4_360p.mp4"
    try:
        res_720, res_360 = await asyncio.gather(
            asyncio.to_thread(_run_single_resolution_omni_sync, model_id, input_parts, "720p", tmp_720, 4),
            asyncio.to_thread(_run_single_resolution_omni_sync, model_id, input_parts, "360p", tmp_360, 4),
        )
    finally:
        tmp_720.unlink(missing_ok=True)
        tmp_360.unlink(missing_ok=True)

    # Also record cached canonical Scene 4 360p baseline file size
    cached_s4_360 = _probe_video_meta(config.FINALS_DIR / "scene_4_cam_a.mp4")

    cost_360_per_s = 0.02
    cost_720_per_s = 0.07
    dur = 4.0
    size_ratio = (
        round(res_720["file_size_bytes"] / res_360["file_size_bytes"], 2)
        if res_360["file_size_bytes"] > 0
        else 0.0
    )
    latency_ratio = (
        round(res_720["total_wall_clock_sec"] / res_360["total_wall_clock_sec"], 2)
        if res_360["total_wall_clock_sec"] > 0
        else 0.0
    )

    summary = {
        "task_id": "W1-T3H",
        "model": model_id,
        "scene_tested": "branch_v1 Scene 4 (cam_a, 4s)",
        "clip_360p": {
            **res_360,
            "cost_per_sec_usd": cost_360_per_s,
            "clip_4s_cost_usd": round(cost_360_per_s * dur, 2),
            "branch_6_clips_cost_usd": round(cost_360_per_s * dur * 6, 2),
        },
        "clip_720p": {
            **res_720,
            "cost_per_sec_usd": cost_720_per_s,
            "clip_4s_cost_usd": round(cost_720_per_s * dur, 2),
            "branch_6_clips_cost_usd": round(cost_720_per_s * dur * 6, 2),
        },
        "cached_scene_4_360p_reference": cached_s4_360,
        "comparison": {
            "latency_ratio_720p_over_360p": latency_ratio,
            "latency_saved_sec_per_clip": round(res_720["total_wall_clock_sec"] - res_360["total_wall_clock_sec"], 2),
            "file_size_ratio_720p_over_360p": size_ratio,
            "cost_ratio_720p_over_360p": round(cost_720_per_s / cost_360_per_s, 2),
            "cost_savings_pct_using_360p": round((1.0 - cost_360_per_s / cost_720_per_s) * 100, 1),
            "pitch_takeaway": (
                f"360p cuts API cost by 71.4% ($0.02/s vs $0.07/s; $0.48 vs $1.68 per 3-scene dual-cam branch), "
                f"reduces clip payload by {size_ratio}x, and delivers {res_360['total_wall_clock_sec']}s vs "
                f"{res_720['total_wall_clock_sec']}s live turnaround for interactive hackathon directing."
            ),
        },
    }
    out_path = config.ASSETS_DIR / "eval_720p_vs_360p.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


@log_function_call
async def run_w1_t4c_prop_persistence() -> dict:
    """W1-T4C: Measure multi-scene object permanence & prop persistence via `models/gemini-decision-1`."""
    t_all = time.perf_counter()
    branch_ids = ["branch_v1", "branch_v2_cached", "branch_v3_starlight_cached"]
    branches = {bid: get_branch(bid) for bid in branch_ids}
    missing = [bid for bid, b in branches.items() if b is None]
    if missing:
        raise RuntimeError(f"Missing cached branches for W1-T4C: {missing}")

    # Build explicit per-(branch, scene, cam) object permanence questions directly against MP4 clips
    eval_jobs: list[dict] = []
    for bid in branch_ids:
        branch = branches[bid]
        assert branch is not None
        for scene in branch.scenes:
            vase_prop = next((p for p in scene.stage_props if p.id == "crystal_vase"), None)
            has_star = any(p.id == "star_token" for p in scene.stage_props) or any(
                a.holding_prop == "star_token" for a in scene.actors
            )
            vase_state = vase_prop.state if vase_prop else "intact"

            q_map: dict[str, dict] = {}
            if vase_state == "broken":
                q_map["vase_permanence"] = {
                    "type": "noul",
                    "instructions": (
                        "Does this video show the crystal vase shattered or broken glass shards / fallen blue rose "
                        "petals on the floor (consistent with irreversible breakage)?"
                    ),
                }
            else:
                q_map["vase_permanence"] = {
                    "type": "noul",
                    "instructions": (
                        "Is the crystal vase with a blue rose standing intact on the center round table "
                        "(NOT shattered on the floor)?"
                    ),
                }

            if has_star:
                q_map["star_token_persistence"] = {
                    "type": "noul",
                    "instructions": (
                        "Is a glowing golden star keepsake / star token visibly present in this scene "
                        "(either held by a character or resting on the table/floor)?"
                    ),
                }

            for cam_id in ("cam_a", "cam_b"):
                clip_url = getattr(scene.media, f"{cam_id}_final_url")
                if not clip_url:
                    continue
                eval_jobs.append(
                    {
                        "branch_id": bid,
                        "scene_id": scene.scene_id,
                        "cam_id": cam_id,
                        "clip_url": clip_url,
                        "expected_vase_state": vase_state,
                        "expects_star_token": has_star,
                        "questions": q_map,
                    }
                )

    sem = asyncio.Semaphore(5)

    async def _eval_job(job: dict) -> dict:
        async with sem:
            local_mp4 = config.ASSETS_DIR / job["clip_url"].removeprefix("/assets/")
            t0 = time.perf_counter()
            payload = build_decision_input(
                (
                    f"Evaluate object permanence for {job['branch_id']} Scene {job['scene_id']} "
                    f"({job['cam_id']}) strictly from the video evidence."
                ),
                [local_mp4],
            )
            res = await create_decision(payload, job["questions"])
            elapsed = round(time.perf_counter() - t0, 2)
            answers = getattr(res, "answers", None) or {}
            checks = {}
            for k in job["questions"]:
                ans = answers.get(k)
                p_true = round(float(getattr(ans, "noul", 0.5)), 3) if ans is not None else 0.5
                conf = round(float(getattr(ans, "confidence", max(p_true, 1.0 - p_true))), 3) if ans is not None else 0.5
                checks[k] = {"p_true": p_true, "confidence": conf, "passed": p_true >= 0.5}
            return {
                "branch_id": job["branch_id"],
                "scene_id": job["scene_id"],
                "cam_id": job["cam_id"],
                "clip_url": job["clip_url"],
                "expected_vase_state": job["expected_vase_state"],
                "expects_star_token": job["expects_star_token"],
                "elapsed_sec": elapsed,
                "checks": checks,
            }

    results = list(await asyncio.gather(*(_eval_job(j) for j in eval_jobs)))

    vase_checks = [r["checks"]["vase_permanence"] for r in results if "vase_permanence" in r["checks"]]
    star_checks = [r["checks"]["star_token_persistence"] for r in results if "star_token_persistence" in r["checks"]]
    all_checks = vase_checks + star_checks

    per_branch = {}
    for bid in branch_ids:
        b_rows = [r for r in results if r["branch_id"] == bid]
        b_checks = [c for r in b_rows for c in r["checks"].values()]
        b_vase = [r["checks"]["vase_permanence"] for r in b_rows if "vase_permanence" in r["checks"]]
        b_star = [r["checks"]["star_token_persistence"] for r in b_rows if "star_token_persistence" in r["checks"]]
        per_branch[bid] = {
            "clips_evaluated": len(b_rows),
            "passed_checks": sum(1 for c in b_checks if c["passed"]),
            "total_checks": len(b_checks),
            "persistence_rate": round(sum(1 for c in b_checks if c["passed"]) / len(b_checks), 3) if b_checks else 0.0,
            "vase_permanence_rate": round(sum(1 for c in b_vase if c["passed"]) / len(b_vase), 3) if b_vase else 0.0,
            "star_token_persistence_rate": (
                round(sum(1 for c in b_star if c["passed"]) / len(b_star), 3) if b_star else None
            ),
        }

    summary = {
        "task_id": "W1-T4C",
        "model": config.MODEL_DECISION,
        "branches_evaluated": branch_ids,
        "total_clips_evaluated": len(results),
        "total_checks": len(all_checks),
        "passed_checks": sum(1 for c in all_checks if c["passed"]),
        "overall_prop_persistence_rate": (
            round(sum(1 for c in all_checks if c["passed"]) / len(all_checks), 3) if all_checks else 0.0
        ),
        "vase_state_permanence_rate": (
            round(sum(1 for c in vase_checks if c["passed"]) / len(vase_checks), 3) if vase_checks else 0.0
        ),
        "star_token_persistence_rate": (
            round(sum(1 for c in star_checks if c["passed"]) / len(star_checks), 3) if star_checks else 0.0
        ),
        "avg_latency_sec": round(sum(r["elapsed_sec"] for r in results) / len(results), 2) if results else 0.0,
        "wall_clock_sec": round(time.perf_counter() - t_all, 2),
        "per_branch": per_branch,
        "items": results,
    }
    out_path = config.ASSETS_DIR / "eval_prop_persistence.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run P2 W1-T3F/T3G/T3H/T4C suite.")
    parser.add_argument("--t3f", action="store_true", help="Run W1-T3F 5-prop showcase.")
    parser.add_argument("--t3g", action="store_true", help="Run W1-T3G branch_v3_starlight_cached pre-cache.")
    parser.add_argument("--t3h", action="store_true", help="Run W1-T3H 720p vs 360p benchmark.")
    parser.add_argument("--t4c", action="store_true", help="Run W1-T4C multi-scene prop persistence eval.")
    parser.add_argument("--all", action="store_true", help="Run all 4 live API tasks.")
    args = parser.parse_args()

    run_all = args.all or not any((args.t3f, args.t3g, args.t3h, args.t4c))
    out: dict = {}
    t_start = time.perf_counter()

    # Phase 1: run independent live generation jobs concurrently
    phase1_tasks: dict[str, asyncio.Task] = {}
    if run_all or args.t3f:
        phase1_tasks["W1_T3F"] = asyncio.create_task(run_w1_t3f_prop_showcase())
    if run_all or args.t3g:
        phase1_tasks["W1_T3G"] = asyncio.create_task(run_w1_t3g_cache_branch_v3())
    if run_all or args.t3h:
        phase1_tasks["W1_T3H"] = asyncio.create_task(run_w1_t3h_720p_vs_360p_bench())

    if phase1_tasks:
        results = await asyncio.gather(*phase1_tasks.values())
        for key, res in zip(phase1_tasks.keys(), results):
            out[key] = res

    # Phase 2: evaluate multi-scene prop persistence once branch_v3_starlight_cached is ready
    if run_all or args.t4c:
        out["W1_T4C"] = await run_w1_t4c_prop_persistence()

    out["total_wall_clock_sec"] = round(time.perf_counter() - t_start, 2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
