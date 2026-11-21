"""
W1-T6 Batch Runner (Owned by P2 / worker-1).

Executes batched T6 tasks cleanly and concurrently:
- W1-T6B: 3 judge-desk physical props (`showcase_desk_*.png`) -> `eval_live_props_sim.json`
- W1-T6C: Pre-cache 4th preset branch (`branch_v4_umbrella_shield_cached.json`)
- W1-T6G: Storyboard strip image (`showcase_storyboard_strip.png`)
"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from backend import config
from backend.director.hydrogen_judge import inspect_tail_frame
from backend.director.state_tree import get_branch, save_branch
from backend.media.prop_maker import create_prop
from backend.media.run_w1_t4_batch import _audit_prop_png
from backend.media.video_renderer import render_scene_media
from backend.schemas import ActorState, BranchState, PropRegistryItem, Scene, StagePropState


async def run_w1_t6b_desk_props() -> dict:
    """Benchmark `create_prop()` on 3 everyday physical desk objects concurrently."""
    specs = [
        (
            "showcase_desk_coffee_mug",
            "Ceramic Coffee Mug",
            "A cozy cream ceramic coffee mug filled with warm latte art resting flat",
        ),
        (
            "showcase_desk_paper_heart",
            "Folded Red Paper Heart",
            "A handmade origami crimson red folded paper heart resting upright",
        ),
        (
            "showcase_desk_brass_key",
            "Brass Apartment Key",
            "A vintage polished brass apartment key with an ornate clover bow",
        ),
    ]
    t0 = time.perf_counter()

    async def _one(pid: str, name: str, desc: str) -> dict:
        ts = time.perf_counter()
        _, item = await create_prop(name=name, description=desc, prop_id=pid)
        elapsed = round(time.perf_counter() - ts, 2)
        audit = _audit_prop_png(config.PROPS_DIR / f"{pid}.png")
        audit["elapsed_sec"] = elapsed
        audit["image_url"] = item.image_url
        audit["name"] = name
        return audit

    results = list(await asyncio.gather(*(_one(p, n, d) for p, n, d in specs)))
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(results),
        "wall_sec": round(time.perf_counter() - t0, 2),
        "avg_latency_sec": round(sum(r["elapsed_sec"] for r in results) / len(results), 2),
        "all_rgba": all(r["mode"] == "RGBA" for r in results),
        "all_zero_green_spill": all(r["opaque_green_spill_pixels"] == 0 for r in results),
        "all_bottom_flush": all(r["bottom_pad_px"] == 0 for r in results),
        "props": results,
    }
    out_path = config.ASSETS_DIR / "eval_live_props_sim.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def run_w1_t6c_umbrella_shield_branch() -> dict:
    """
    Pre-cache 4th preset branch (`branch_v4_umbrella_shield_cached`):
    Matches Demo Preset 'Shield · Umbrella Guard' — Lia blocks the table with her
    crimson umbrella to protect the blue-rose crystal vase so it stays intact.
    Reuses Scene 1 from `branch_v1`; renders Scenes 2..4 dual-cam concurrently.
    """
    b1 = get_branch("branch_v1")
    assert b1 is not None, "branch_v1 must exist before creating branch_v4"

    s1 = b1.scenes[0].model_copy(deep=True)
    s1.reused_from_branch = "branch_v1"
    s1.needs_rerender = False

    s2 = Scene(
        scene_id=2,
        title="Shield · Umbrella Guard Intervention",
        duration_sec=4.0,
        narration=(
            "As sudden thunder cracks outside, Lia swiftly extends her crimson umbrella across the edge "
            "of the center table, shielding the blue-rose crystal vase so startled Nyx lands safely without knocking it over."
        ),
        vocal_emotion="urgent_emotional",
        gesture_intent="point_center",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="muffled_thunder_cloth_whoosh_warm_chime",
        environment_notes=(
            "crimson_umbrella_shielding_table intact_blue_rose_crystal_vase "
            "warm_lantern_conservatory rain_outside_windows clean_dry_table"
        ),
        needs_rerender=False,
        reused_from_branch=None,
        actors=[
            ActorState(
                id="lia",
                name="Lia",
                asset_url="/assets/actors/lia.png",
                grid=[0.6, 1.05],
                facing="right",
                action="reach",
                holding_prop="red_umbrella",
            ),
            ActorState(
                id="nyx",
                name="Nyx",
                asset_url="/assets/actors/nyx.png",
                grid=[1.7, 1.05],
                facing="left",
                action="jump",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
        ],
        qa_questions=[
            "Is the silver-haired girl using her red umbrella to shield the center table?",
            "Is the blue-rose crystal vase still standing intact on the center table (unbroken)?",
            "Is the small black cat safely halted near the right side of the table?",
            "Do Camera A and Camera B stay spatially consistent?",
        ],
    )

    s3 = Scene(
        scene_id=3,
        title="Quiet Relief Beside the Intact Vase",
        duration_sec=4.0,
        narration=(
            "With the vase saved intact on the table, Lia lowers her crimson umbrella to kneel beside Nyx, "
            "softly comforting the calm black cat as warm golden lantern glow fills the glass conservatory."
        ),
        vocal_emotion="gentle_whisper",
        gesture_intent="beckon_soothe",
        direction_hint="center",
        lighting="warm_transition",
        camera_shake=False,
        audio_mood="gentle_rain_soft_purring_warm_piano",
        environment_notes=(
            "intact_blue_rose_crystal_vase warm_golden_lantern_light "
            "open_red_umbrella_resting_beside_lia rain_streaked_glass_windows"
        ),
        needs_rerender=False,
        reused_from_branch=None,
        actors=[
            ActorState(
                id="lia",
                name="Lia",
                asset_url="/assets/actors/lia.png",
                grid=[0.65, 1.15],
                facing="right",
                action="kneel",
                holding_prop="red_umbrella",
            ),
            ActorState(
                id="nyx",
                name="Nyx",
                asset_url="/assets/actors/nyx.png",
                grid=[1.5, 1.15],
                facing="left",
                action="sit",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
        ],
        qa_questions=[
            "Is Lia kneeling near the small black cat with warm golden lighting?",
            "Is the crystal vase with its blue rose still intact on the center round table?",
            "Do Camera A and Camera B match character placement cleanly?",
        ],
    )

    s4 = Scene(
        scene_id=4,
        title="Safe Under the Crimson Canopy",
        duration_sec=4.0,
        narration=(
            "Under warm golden light, Lia and Nyx rest close together beneath the crimson umbrella "
            "while the intact blue-rose crystal vase glows peacefully on the center table."
        ),
        vocal_emotion="joyful",
        gesture_intent="none",
        direction_hint="center",
        lighting="warm_gold_snow",
        camera_shake=False,
        audio_mood="peaceful_night_rain_warm_strings",
        environment_notes=(
            "intact_blue_rose_crystal_vase warm_golden_conservatory "
            "single_open_red_umbrella_sheltering_girl_and_cat starry_glass_roof"
        ),
        needs_rerender=False,
        reused_from_branch=None,
        actors=[
            ActorState(
                id="lia",
                name="Lia",
                asset_url="/assets/actors/lia.png",
                grid=[0.85, 1.15],
                facing="right",
                action="shared_umbrella",
                holding_prop="red_umbrella",
            ),
            ActorState(
                id="nyx",
                name="Nyx",
                asset_url="/assets/actors/nyx.png",
                grid=[1.4, 1.15],
                facing="left",
                action="nuzzle",
                holding_prop=None,
            ),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], on_floor=False, state="intact"),
        ],
        qa_questions=[
            "Are Lia and the small black cat resting warmly together under one crimson umbrella?",
            "Is the crystal vase with the blue rose still standing intact on the center table?",
            "Do Camera A and Camera B stay consistent without duplicate umbrellas?",
        ],
    )

    ref_urls = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.PROPS_DIR / "crystal_vase.png"),
    ]

    async def _render_one(sc: Scene) -> Scene:
        media = await render_scene_media(
            sc,
            ref_urls,
            tail_inspector=inspect_tail_frame,
        )
        # Rename generated files to clean canonical `branch_v4_scene_{id}_cam_{a,b}*` names.
        for cam in ("cam_a", "cam_b"):
            url_attr = f"{cam}_final_url"
            tail_attr = f"{cam}_tail_frame_url"
            old_v = getattr(media, url_attr)
            if old_v:
                src_v = config.ASSETS_DIR / old_v.removeprefix("/assets/")
                dst_v = config.FINALS_DIR / f"branch_v4_scene_{sc.scene_id}_{cam}.mp4"
                if src_v.is_file():
                    shutil.move(str(src_v), str(dst_v))
                setattr(media, url_attr, f"/assets/finals/{dst_v.name}")
            old_t = getattr(media, tail_attr)
            if old_t:
                src_t = config.ASSETS_DIR / old_t.removeprefix("/assets/")
                dst_t = config.TAILS_DIR / f"branch_v4_scene_{sc.scene_id}_{cam}_tail.png"
                if src_t.is_file():
                    shutil.move(str(src_t), str(dst_t))
                setattr(media, tail_attr, f"/assets/tails/{dst_t.name}")
        sc.media = media
        return sc

    rendered = list(await asyncio.gather(*(_render_one(sc) for sc in (s2, s3, s4))))
    branch = BranchState(
        branch_id="branch_v4_umbrella_shield_cached",
        branch_label="Shield · Umbrella Guard (Intact Vase Preset)",
        parent_branch_id="branch_v1",
        forked_from_scene=2,
        rewrite_summary="In Scene 2, Lia blocks the table with her red umbrella to protect the crystal vase from falling.",
        props_registry={
            "red_umbrella": PropRegistryItem(
                name="Crimson Vintage Umbrella",
                image_url="/assets/actors/lia.png",
                state="held",
                origin="preset",
            ),
            "crystal_vase": PropRegistryItem(
                name="Blue Rose Crystal Vase",
                image_url="/assets/props/crystal_vase.png",
                state="intact",
                origin="preset",
            ),
        },
        scenes=[s1, *rendered],
    )
    save_branch(branch)
    return {
        "branch_id": branch.branch_id,
        "qa_scores": [round(sc.media.qa_score, 3) for sc in branch.scenes],
    }


def run_w1_t6g_storyboard_strip() -> Path:
    """Create a 2x4 BE vs HE high-resolution storyboard contact sheet PNG."""
    b1 = get_branch("branch_v1")
    b2 = get_branch("branch_v2_cached")
    assert b1 is not None and b2 is not None, "branch_v1 and branch_v2_cached required"

    cell_w, cell_h = 460, 260
    pad_x, pad_y = 24, 34
    header_h = 84
    footer_h = 54
    cols, rows = 4, 2
    canvas_w = pad_x + cols * (cell_w + pad_x)
    canvas_h = header_h + rows * (cell_h + pad_y) + footer_h

    canvas = Image.new("RGB", (canvas_w, canvas_h), (14, 18, 30))
    draw = ImageDraw.Draw(canvas)
    ttf_bold = Path(r"C:\Windows\Fonts\segoeuib.ttf")
    ttf_reg = Path(r"C:\Windows\Fonts\segoeui.ttf")
    font_title = ImageFont.truetype(str(ttf_bold if ttf_bold.is_file() else ttf_reg), 24) if ttf_reg.is_file() else ImageFont.load_default(size=24)
    font_sub = ImageFont.truetype(str(ttf_reg), 15) if ttf_reg.is_file() else ImageFont.load_default(size=15)
    font_cap = ImageFont.truetype(str(ttf_bold if ttf_bold.is_file() else ttf_reg), 14) if ttf_reg.is_file() else ImageFont.load_default(size=14)

    draw.text(
        (pad_x, 18),
        "StageState -- Dual-Branch Time-Travel Storyboard (BE Shattered Vase vs. HE Starlight Gift)",
        fill=(250, 204, 21),
        font=font_title,
    )
    draw.text(
        (pad_x, 50),
        "Top Row: Branch 1 (BE Baseline)   |   Bottom Row: Branch 2 (HE Rewritten at Scene 2 -- Scene 1 Shared Prefix Reused Verbatim)",
        fill=(148, 163, 184),
        font=font_sub,
    )

    branches = [
        ("BE", (248, 113, 113), {s.scene_id: s for s in b1.scenes}),
        ("HE", (74, 222, 128), {s.scene_id: s for s in b2.scenes}),
    ]
    for r_idx, (tag, accent, sc_map) in enumerate(branches):
        for c_idx, sid in enumerate((1, 2, 3, 4)):
            sc = sc_map[sid]
            tail_rel = (sc.media.cam_a_tail_frame_url or "").removeprefix("/assets/")
            tail_path = config.ASSETS_DIR / tail_rel
            x0 = pad_x + c_idx * (cell_w + pad_x)
            y0 = header_h + r_idx * (cell_h + pad_y)
            if tail_path.is_file():
                with Image.open(tail_path) as im:
                    thumb = im.convert("RGB").resize((cell_w, cell_h), Image.Resampling.LANCZOS)
                canvas.paste(thumb, (x0, y0))
            draw.rectangle([x0 - 1, y0 - 1, x0 + cell_w + 1, y0 + cell_h + 1], outline=accent, width=2)
            clean_title = sc.title.replace("·", "-").replace("—", "--")
            lbl = f"{tag} | S{sid}: {clean_title} (QA {sc.media.qa_score:.2f})"
            draw.text((x0 + 4, y0 + cell_h + 7), lbl, fill=(226, 232, 240), font=font_cap)

    draw.text(
        (pad_x, canvas_h - 34),
        "Models: Gemini 4 Neon (Plan) | Gemini 4 Hydrogen + Decisions v1beta (QA) | Omni Flash 360p (Dual-Cam) | Nano Banana 2.1 (Props)",
        fill=(125, 211, 252),
        font=font_sub,
    )
    out_path = config.ASSETS_DIR / "showcase_storyboard_strip.png"
    canvas.save(out_path, format="PNG")
    return out_path


async def run_batch_1() -> None:
    """Execute W1-T6B, W1-T6C, and W1-T6G concurrently where possible."""
    t6b_res, t6c_res = await asyncio.gather(
        run_w1_t6b_desk_props(),
        run_w1_t6c_umbrella_shield_branch(),
    )
    strip_path = run_w1_t6g_storyboard_strip()
    print("W1-T6B:", json.dumps({k: v for k, v in t6b_res.items() if k != "props"}))
    print("W1-T6C:", json.dumps(t6c_res))
    print("W1-T6G:", str(strip_path), strip_path.stat().st_size)


if __name__ == "__main__":
    asyncio.run(run_batch_1())
