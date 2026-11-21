"""
W1-V2-07 Regression Suite: 30s Throw-Peak Extraction, Live Fast Mode, Sculpture End-Freeze,
Unbounded Scene 5+ Extend, Dark-Border Crop, and R-13/R-14/R-18 Hardening (Owned by P2).
"""

import asyncio
import io
import json
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backend import config
from backend.director.state_tree import get_branch
from backend.main import _upsert_prop_appearance_note, app
from backend.media.prop_maker import (
    _compute_motion_fg_mask,
    _detect_throw_peak_frame_idx,
    _downscale_jpeg_sync,
    _extract_dominant_color_info,
    _extract_image_from_bytes,
    create_prop,
    estimate_prop_height_m,
)
from backend.media.video_renderer import (
    CROSS_CAM_THRESHOLD,
    _build_shot_prompt,
    _select_scene_ref_images,
    _trim_dark_borders_sync,
    render_scene_media,
)
from backend.schemas import ActorState, Scene, StagePropState


def test_30s_throw_peak_window_and_degraded_flag() -> None:
    """Verify 1200-frame decoding catches a throw peak after t=5s (frame > 150) & degraded flag on mock/fail."""
    frames: list[np.ndarray] = []
    # Simulate 240 frames (past the old 150-frame / 5s limit!):
    # Frames 0..175 are calm dark grey studio; frame 180 has high-contrast tossed golden object enter center ROI.
    for idx in range(240):
        frame = np.full((180, 240, 3), (38, 42, 48), dtype=np.uint8)
        if 178 <= idx <= 200:
            # Crisp concentric checkerboard-like golden coin at center (maximizes center ROI diff & Laplacian sharpness)
            cv2.circle(frame, (120, 90), 34, (30, 190, 250), -1)
            cv2.circle(frame, (120, 90), 18, (20, 30, 160), 3)
            cv2.line(frame, (98, 90), (142, 90), (255, 255, 255), 2)
        frames.append(frame)

    peak_idx = _detect_throw_peak_frame_idx(frames, est_fps=24.0)
    assert 178 <= peak_idx <= 200, f"Throw peak missed settled object window: idx={peak_idx}"

    # Verify MJPG-encoded multi-frame >180 clip extracts via _extract_image_from_bytes cleanly
    avi_tmp = Path(tempfile.gettempdir()) / "stagestate_v2_throw_240f.avi"
    try:
        wr = cv2.VideoWriter(str(avi_tmp), cv2.VideoWriter_fourcc(*"MJPG"), 24.0, (240, 180))
        for f in frames:
            wr.write(f)
        wr.release()
        extracted = _extract_image_from_bytes(avi_tmp.read_bytes(), timestamp_sec=None, detect_throw_peak=True)
        assert extracted is not None
        jpg_bytes, mime = extracted
        assert mime == "image/jpeg" and len(jpg_bytes) > 500
    finally:
        avi_tmp.unlink(missing_ok=True)

    # Verify everyday thrown-item SI heights & ASCII word-boundary matching (no 'can' in 'canopy', no 'pen' in 'open')
    assert estimate_prop_height_m("Soda Can Drink", "Cold sparkling drink") == 0.13
    assert estimate_prop_height_m("Iced Soda Can", "cold drink") == 0.13
    assert estimate_prop_height_m("Potato Chips Snack Bag") == 0.22
    assert estimate_prop_height_m("Silver Laptop", "compact notebook computer") == 0.30
    assert estimate_prop_height_m("Conservatory Wooden Chair") == 0.85
    assert estimate_prop_height_m("Closed Red Umbrella", "slim silhouette, no open canopy") == 0.86
    assert estimate_prop_height_m("Open Red Umbrella", "fully open dome canopy") == 0.86

    # Verify R-18 explicit fallback flag under DEMO_MODE=True
    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = True
    tmp_prop_id = "v2_throw_test_coin"
    try:
        pid, item = asyncio.run(create_prop(name="Throw Coin", description="Silver coin", prop_id=tmp_prop_id, detect_throw_peak=True))
        assert pid == tmp_prop_id
        assert item.state == "mock_placeholder"
        assert item.height_m == 0.08
    finally:
        config.DEMO_MODE = prev_demo
        (config.PROPS_DIR / f"{tmp_prop_id}.png").unlink(missing_ok=True)


def test_catch_from_camera_conditional_prompting() -> None:
    """Verify catch_from_camera / throw_arc triggers forward camera grab ONLY when explicit; defaults rest cleanly."""
    placed_scene = Scene(
        scene_id=6,
        title="Tabletop Snack Break",
        narration="A chilled drink can and a crisp snack bag rest on the table while Lia smiles.",
        lighting="warm_gold_snow",
        end_freeze_mode="sculpture",
        actors=[ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.6, 1.1], action="stand")],
        stage_props=[StagePropState(id="soda_drink_can", grid=[1.0, 1.0], state="intact", on_floor=False, entry_motion="place")],
    )
    p_place = _build_shot_prompt(placed_scene, "cam_a", has_first_frame=False, has_preview_video=False)
    assert "lean" not in p_place.lower() or "default prop placement" in p_place.lower()
    assert "catch the flying item out of mid-air" not in p_place
    assert "resting squarely on the center round table" in p_place

    catch_scene = Scene(
        scene_id=6,
        title="Catch the Drink",
        narration="Here, catch this cold soda can before setting it beside the vase!",
        lighting="warm_gold_snow",
        end_freeze_mode="sculpture",
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.6, 1.1], action="catch_from_camera"),
        ],
        stage_props=[
            StagePropState(id="soda_drink_can", grid=[1.0, 1.0], state="intact", on_floor=False, entry_motion="throw_arc"),
        ],
    )
    p_catch = _build_shot_prompt(catch_scene, "cam_a", has_first_frame=False, has_preview_video=False)
    assert "leans gracefully forward toward the camera lens to catch the flying item out of mid-air" in p_catch
    assert "incoming throw arc from the front camera" in p_catch


def test_fast_live_mode_sculpture_prompt_and_border_trim() -> None:
    """Verify fast_live_mode=True, sculpture end-freeze prompt, dark border trim, and room ref selection."""
    assert CROSS_CAM_THRESHOLD >= 0.80

    # Test border trimming (R-40)
    bordered = np.full((180, 320, 3), (40, 120, 200), dtype=np.uint8)
    bordered[:6, :] = 0
    bordered[-6:, :] = 0
    bordered[:, :8] = 0
    bordered[:, -8:] = 0
    tmp_png = config.PREVIEWS_DIR / "tmp_v2_border_test.png"
    cropped_path = None
    try:
        ok_enc, buf = cv2.imencode(".png", bordered)
        assert ok_enc
        tmp_png.write_bytes(buf.tobytes())
        out_p, trimmed = _trim_dark_borders_sync(tmp_png)
        assert trimmed is True
        cropped_path = out_p
        clean = cv2.imdecode(np.frombuffer(out_p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert clean is not None and float(np.mean(clean[1, :])) > 50.0
    finally:
        tmp_png.unlink(missing_ok=True)
        if cropped_path is not None and cropped_path != tmp_png:
            cropped_path.unlink(missing_ok=True)

    scene5 = Scene(
        scene_id=5,
        title="Sculpture Encore",
        narration="Lia catches the thrown brass key and holds it gently beside Nyx before both settle motionless.",
        lighting="warm_gold_snow",
        end_freeze_mode="sculpture",
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.6, 1.1], action="stand"),
            ActorState(id="nyx", name="Nyx", asset_url="/assets/actors/nyx.png", grid=[1.4, 1.1], action="sit"),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], state="intact", on_floor=False),
            StagePropState(id="brass_key", grid=[1.05, 1.0], state="intact", on_floor=False, entry_motion="throw_arc"),
        ],
    )
    prompt_a = _build_shot_prompt(scene5, "cam_a", has_first_frame=False, has_preview_video=False)
    assert "frozen-sculpture pose" in prompt_a
    assert "edge to edge from the very first frame" in prompt_a
    assert "Explicit toss/hand-off choreography" in prompt_a

    # Default non-throw scene must NOT trigger forward camera-grab beat
    calm_scene = Scene(
        scene_id=2,
        title="Calm Table",
        narration="Lia stands quietly beside the table with her closed umbrella and wooden chair.",
        lighting="warm_dim",
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.6, 1.1], action="stand", holding_prop="red_umbrella"),
            ActorState(id="nyx", name="Nyx", asset_url="/assets/actors/nyx.png", grid=[1.4, 1.1], action="sit"),
        ],
        stage_props=[
            StagePropState(id="crystal_vase", grid=[1.0, 1.0], state="intact", on_floor=False),
            StagePropState(id="wooden_chair", grid=[0.4, 1.1], state="intact", on_floor=True),
        ],
    )
    calm_prompt = _build_shot_prompt(calm_scene, "cam_a", has_first_frame=False, has_preview_video=False)
    assert "Explicit toss/hand-off choreography" not in calm_prompt
    assert "catch the flying item out of mid-air" not in calm_prompt

    # Even with 3 active props + max_refs=5, fixed empty room ref must NEVER be evicted
    refs_crowded = _select_scene_ref_images(
        calm_scene,
        [
            str(config.SHARED_ASSETS_DIR / "lia.png"),
            str(config.SHARED_ASSETS_DIR / "nyx.png"),
            str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
            str(config.PROPS_DIR / "red_umbrella_closed.png"),
            str(config.PROPS_DIR / "wooden_chair.png"),
        ],
        max_refs=5,
        cam_id="cam_a",
    )
    assert any(p.name == "conservatory_cam_a.png" for p in refs_crowded), f"Room ref evicted in {refs_crowded}"

    refs_a = _select_scene_ref_images(scene5, [str(config.SHARED_ASSETS_DIR / "lia.png"), str(config.SHARED_ASSETS_DIR / "nyx.png")], max_refs=5, cam_id="cam_a")
    assert any(p.name == "conservatory_cam_a.png" for p in refs_a), f"Room ref missing in {refs_a}"

    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = True
    try:
        media = asyncio.run(render_scene_media(scene5, [str(config.SHARED_ASSETS_DIR / "lia.png")], fast_live_mode=True))
        assert media.cam_a_final_url and media.cam_b_final_url
    finally:
        config.DEMO_MODE = prev_demo


def test_unbounded_extend_scene_5_plus() -> None:
    """Verify POST /api/director/extend appends Scene 5 cleanly beyond old 4-scene cap."""
    client = TestClient(app)
    b1_path = config.BRANCHES_DIR / "branch_v1.json"
    orig_raw = b1_path.read_bytes()
    b1 = get_branch("branch_v1")
    assert b1 is not None
    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = True
    try:
        r_ext = client.post(
            "/api/director/extend",
            data={
                "branch_id": "branch_v1",
                "from_scene_id": "4",
                "text": "Lia catches a golden bell from the starry sky and both freeze into moonlight sculptures.",
                "in_place": "false",
            },
        )
        assert r_ext.status_code == 200, r_ext.text
        body = r_ext.json()
        assert len(body["scenes"]) == 5, f"Expected 5 scenes after extend, got {len(body['scenes'])}"
        assert body["scenes"][-1]["scene_id"] == 5
        assert body["scenes"][-1]["end_freeze_mode"] == "sculpture"
        # Clean up newly created extend branch JSON
        new_id = body.get("branch_id")
        if new_id and new_id != "branch_v1":
            (config.BRANCHES_DIR / f"{new_id}.json").unlink(missing_ok=True)
    finally:
        config.DEMO_MODE = prev_demo
        b1_path.write_bytes(orig_raw)


def test_v3_captured_appearance_preservation() -> None:
    """W1-V3-03/04/05: Verify smartphone height, collision-free IDs, <0.3s template recolor & color fidelity, 384px downscale, & prop_descriptions."""
    assert estimate_prop_height_m("Black Smartphone", "Matte black mobile phone") == 0.15

    buf_yellow = io.BytesIO()
    Image.new("RGB", (640, 480), (250, 225, 25)).save(buf_yellow, format="PNG")
    buf_black = io.BytesIO()
    Image.new("RGB", (640, 480), (18, 18, 22)).save(buf_black, format="PNG")

    ds_bytes, ds_mime = _downscale_jpeg_sync((buf_yellow.getvalue(), "image/png"), max_side=384)
    assert ds_mime == "image/jpeg"
    with Image.open(io.BytesIO(ds_bytes)) as ds_im:
        assert max(ds_im.width, ds_im.height) <= 384

    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = False
    created_ids: list[str] = []
    try:
        t0 = time.perf_counter()
        pid_y, item_y = asyncio.run(
            create_prop(
                name="Captured Can",
                description="soda can",
                camera_frame_bytes=buf_yellow.getvalue(),
            )
        )
        dt_y = time.perf_counter() - t0
        created_ids.append(pid_y)

        t1 = time.perf_counter()
        pid_b, item_b = asyncio.run(
            create_prop(
                name="Black Smartphone",
                description="smartphone",
                camera_frame_bytes=buf_black.getvalue(),
            )
        )
        dt_b = time.perf_counter() - t1
        created_ids.append(pid_b)

        assert dt_y < 0.35 and dt_b < 0.35, f"Template fast-path too slow: yellow={dt_y:.3f}s, black={dt_b:.3f}s"
        assert pid_y != pid_b, f"Distinct camera captures collided on prop_id={pid_y}"
        assert item_y.state == "intact" and "yellow" in item_y.appearance_summary.lower(), item_y.appearance_summary
        assert item_b.state == "intact" and "black" in item_b.appearance_summary.lower(), item_b.appearance_summary

        with Image.open(config.PROPS_DIR / f"{pid_y}.png") as im_y:
            arr_y = np.array(im_y.convert("RGBA"))
            opaque_y = arr_y[arr_y[:, :, 3] > 200]
            assert float(np.mean(opaque_y[:, 0])) > 140 and float(np.mean(opaque_y[:, 1])) > 120
            assert float(np.mean(opaque_y[:, 0])) - float(np.mean(opaque_y[:, 2])) > 55
        with Image.open(config.PROPS_DIR / f"{pid_b}.png") as im_b:
            arr_b = np.array(im_b.convert("RGBA"))
            opaque_b = arr_b[arr_b[:, :, 3] > 200]
            assert float(np.mean(opaque_b[:, :3])) < 70
    finally:
        config.DEMO_MODE = prev_demo
        for pid in created_ids:
            (config.PROPS_DIR / f"{pid}.png").unlink(missing_ok=True)

    scene_v3 = Scene(
        scene_id=7,
        title="Yellow Can Encore",
        narration="Lia places the yellow aluminum can gently onto the center table.",
        lighting="warm_gold_snow",
        end_freeze_mode="sculpture",
        actors=[ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.6, 1.1], action="stand")],
        stage_props=[StagePropState(id="yellow_drink_can", grid=[1.0, 1.0], state="intact", on_floor=False)],
    )
    prompt_v3 = _build_shot_prompt(
        scene_v3,
        "cam_a",
        has_first_frame=False,
        has_preview_video=False,
        prop_descriptions={"yellow_drink_can": "bright yellow aluminum beverage can"},
    )
    assert "bright yellow aluminum beverage can" in prompt_v3


def test_round1_product_polish_regressions() -> None:
    """Round 1: Foreground-masked color resistance, parsed_directive_json /extend, environment_notes anti-bloat, & tail forwarding."""
    # 1. Grey background wall (68% center) + bright red soda can (32% center): both motion-mask and salience still pick red!
    bg = np.full((240, 320, 3), (145, 145, 148), dtype=np.uint8)
    fg = bg.copy()
    cv2.rectangle(fg, (128, 72), (192, 168), (38, 38, 222), -1)  # BGR crimson red rectangle
    fg_mask = _compute_motion_fg_mask(fg, bg)
    assert fg_mask is not None
    ok_jpg, buf_fg = cv2.imencode(".jpg", fg)
    assert ok_jpg
    still = (buf_fg.tobytes(), "image/jpeg")
    label_vid, _, bucket_vid = _extract_dominant_color_info("Soda Can", "soda can", still, fg_mask=fg_mask)
    label_img, _, bucket_img = _extract_dominant_color_info("Soda Can", "soda can", still, fg_mask=None)
    assert bucket_vid == "red" and "red" in label_vid, f"Video fg mask failed: {label_vid}/{bucket_vid}"
    assert bucket_img == "red" and "red" in label_img, f"Still salience filter failed: {label_img}/{bucket_img}"

    # 2. environment_notes anti-bloat deduplication + stale prop pruning
    notes = "rain_windows | old_removed_prop: silver mug | soda_can_1: silver can"
    cleaned = _upsert_prop_appearance_note(notes, "soda_can_1", "bright yellow soda can", {"soda_can_1", "crystal_vase"})
    assert "old_removed_prop" not in cleaned
    assert cleaned.count("soda_can_1:") == 1
    assert "soda_can_1: bright yellow soda can" in cleaned
    assert "rain_windows" in cleaned

    # 3. /api/director/extend honors parsed_directive_json + /api/media/render-scene accepts prev_tail_cam_b_url cleanly
    client = TestClient(app)
    b1_path = config.BRANCHES_DIR / "branch_v1.json"
    orig_raw = b1_path.read_bytes()
    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = True
    created_branch_id: str | None = None
    try:
        parsed_payload = json.dumps(
            {
                "instruction": "Lia places a bright crimson soda can gracefully on the conservatory table.",
                "target_scene_id": 5,
                "mode": "extend",
                "vocal_emotion": "joyful",
                "gesture_intent": "none",
                "direction_hint": "none",
                "has_physical_prop": False,
                "needs_prop_prompt": False,
            }
        )
        r_ext = client.post(
            "/api/director/extend",
            data={
                "branch_id": "branch_v1",
                "from_scene_id": "4",
                "parsed_directive_json": parsed_payload,
            },
        )
        assert r_ext.status_code == 200, r_ext.text
        ext_body = r_ext.json()
        created_branch_id = ext_body["branch_id"]
        assert "crimson soda can" in ext_body["scenes"][-1]["narration"]

        r_ren = client.post(
            "/api/media/render-scene",
            data={
                "branch_id": created_branch_id,
                "scene_id": "5",
                "prev_tail_frame_url": "/assets/tails/scene_1_cam_a_tail.png",
                "prev_tail_cam_b_url": "/assets/tails/scene_1_cam_b_tail.png",
                "fast_live_mode": "true",
            },
        )
        assert r_ren.status_code == 200, r_ren.text
        assert r_ren.json()["scene_id"] == 5
    finally:
        config.DEMO_MODE = prev_demo
        if created_branch_id and created_branch_id != "branch_v1":
            (config.BRANCHES_DIR / f"{created_branch_id}.json").unlink(missing_ok=True)
        b1_path.write_bytes(orig_raw)


if __name__ == "__main__":
    test_30s_throw_peak_window_and_degraded_flag()
    test_catch_from_camera_conditional_prompting()
    test_fast_live_mode_sculpture_prompt_and_border_trim()
    test_unbounded_extend_scene_5_plus()
    test_v3_captured_appearance_preservation()
    test_round1_product_polish_regressions()
    print("ALL W1-V2/V3 & ROUND-1 PRODUCT POLISH TESTS PASSED!")
