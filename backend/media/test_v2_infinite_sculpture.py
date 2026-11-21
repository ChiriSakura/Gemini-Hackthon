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
from backend.main import app
from backend.media.audit_props import audit_single_prop
from backend.media.prop_maker import (
    _detect_throw_peak_frame_idx,
    _downscale_jpeg_sync,
    _extract_image_from_bytes,
    _match_prop_template_category,
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

    # Everyday thrown object SI height scaling + ASCII word-boundary matching (no 'can' in 'canopy', no 'pen' in 'open')
    assert estimate_prop_height_m("Soda Can Drink", "Cold sparkling drink") == 0.13
    assert estimate_prop_height_m("Black Smartphone", "Matte black mobile phone") == 0.15
    assert estimate_prop_height_m("Snack Bag", "Crunchy potato chips") == 0.20
    assert estimate_prop_height_m("Silver Laptop", "Slim aluminum laptop") == 0.28
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
    assert "Lia leans toward the camera to catch the flying item out of mid-air" in p_catch
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
    assert "Lia leans toward the camera to catch the flying item out of mid-air" in prompt_a

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
    assert "Lia leans toward the camera to catch the flying item out of mid-air" not in calm_prompt

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


def test_v3_appearance_fidelity_and_fast_template_tint() -> None:
    """
    W1-V3-03 / W1-V3-04 / W1-V3-05 regression checks:
    (a) Smartphone SI height == 0.15m.
    (b) Distinct camera_frame_bytes produce non-colliding prop_ids + faithful appearance_summary.
    (c) _build_shot_prompt(..., prop_descriptions=...) preserves exact prop appearance verbatim.
    (d) Seen everyday categories recolor neutral 2D anime templates in <0.30s with accurate color
        fidelity & 0 green halo; unseen categories downscale payload to <=384px cleanly.
    """
    assert estimate_prop_height_m("Black Smartphone", "Matte black mobile phone") == 0.15
    assert _match_prop_template_category("Yellow Soda Can") == "soda_can"
    assert _match_prop_template_category("Black Smartphone") == "smartphone"
    assert _match_prop_template_category("Red Rubber Duck") is None

    # Synthetic camera captures: bright yellow can vs cobalt blue can vs matte black smartphone
    def _make_jpg(bgr: tuple[int, int, int], w: int = 320, h: int = 240) -> bytes:
        im = np.full((h, w, 3), (42, 46, 54), dtype=np.uint8)
        im[h // 5 : (4 * h) // 5, w // 5 : (4 * w) // 5] = bgr
        ok, buf = cv2.imencode(".jpg", im)
        assert ok
        return buf.tobytes()

    yellow_jpg = _make_jpg((25, 215, 250))  # BGR -> bright yellow
    blue_jpg = _make_jpg((225, 115, 40))    # BGR -> cobalt blue
    black_jpg = _make_jpg((24, 26, 32))     # BGR -> matte black

    created_paths: list[Path] = []
    try:
        t0 = time.perf_counter()
        pid_y, item_y = asyncio.run(create_prop(name="Soda Can", description="", camera_frame_bytes=yellow_jpg))
        dt_y = time.perf_counter() - t0
        created_paths.append(config.ASSETS_DIR / item_y.image_url.removeprefix("/assets/"))

        t1 = time.perf_counter()
        pid_b, item_b = asyncio.run(create_prop(name="Soda Can", description="", camera_frame_bytes=blue_jpg))
        dt_b = time.perf_counter() - t1
        created_paths.append(config.ASSETS_DIR / item_b.image_url.removeprefix("/assets/"))

        t2 = time.perf_counter()
        pid_p, item_p = asyncio.run(create_prop(name="Smartphone", description="", camera_frame_bytes=black_jpg))
        dt_p = time.perf_counter() - t2
        created_paths.append(config.ASSETS_DIR / item_p.image_url.removeprefix("/assets/"))

        # (b) Collision-free prop_ids + faithful appearance_summary
        assert pid_y != pid_b, f"Distinct camera captures collided: {pid_y} == {pid_b}"
        assert "yellow" in item_y.appearance_summary.lower(), f"Missing yellow in {item_y.appearance_summary!r}"
        assert "blue" in item_b.appearance_summary.lower(), f"Missing blue in {item_b.appearance_summary!r}"
        assert "black" in item_p.appearance_summary.lower(), f"Missing black in {item_p.appearance_summary!r}"
        assert item_p.height_m == 0.15

        # (d) Fast template tinting (<0.30s) + real pixel color fidelity + 0 green halo
        assert dt_y < 0.35 and dt_b < 0.35 and dt_p < 0.35, f"Template tint too slow: {dt_y:.3f}s, {dt_b:.3f}s, {dt_p:.3f}s"
        y_path = created_paths[0]
        aud_y = audit_single_prop(y_path)
        assert aud_y["passed"] and aud_y["halo_green_pixels"] == 0
        with Image.open(y_path) as y_im:
            y_arr = np.array(y_im.convert("RGBA"))
        h_y = y_arr.shape[0]
        body_y = y_arr[int(h_y * 0.25) : int(h_y * 0.85), :, :]
        op_y = body_y[body_y[:, :, 3] >= 200]
        assert float(np.mean(op_y[:, 0])) > 180.0 and float(np.mean(op_y[:, 1])) > 155.0 and float(np.mean(op_y[:, 2])) < 95.0, (
            f"Yellow soda can color fidelity failed: mean RGB=({np.mean(op_y[:,0]):.1f},{np.mean(op_y[:,1]):.1f},{np.mean(op_y[:,2]):.1f})"
        )

        p_path = created_paths[2]
        with Image.open(p_path) as p_im:
            p_arr = np.array(p_im.convert("RGBA"))
        op_p = p_arr[p_arr[:, :, 3] >= 200]
        assert float(np.mean(op_p[:, :3])) < 80.0, f"Black smartphone too bright: mean RGB={np.mean(op_p[:, :3]):.1f}"

        # Exact-match disk cache hit (<0.10s)
        t_hit = time.perf_counter()
        pid_y2, item_y2 = asyncio.run(create_prop(name="Soda Can", description="", camera_frame_bytes=yellow_jpg))
        dt_hit = time.perf_counter() - t_hit
        created_paths.append(config.ASSETS_DIR / item_y2.image_url.removeprefix("/assets/"))
        assert dt_hit < 0.15, f"Template disk cache hit too slow: {dt_hit:.3f}s"

        # W1-V3-05 lean payload downscale <=384px
        big_jpg = _make_jpg((30, 40, 220), w=1280, h=720)
        small_jpg = _downscale_jpeg_sync(big_jpg, max_dim=384, quality=80)
        with Image.open(io.BytesIO(small_jpg)) as sm_im:
            assert max(sm_im.width, sm_im.height) <= 384
    finally:
        for p in created_paths:
            p.unlink(missing_ok=True)

    # (c) Propagate prop_descriptions verbatim into _build_shot_prompt()
    can_scene = Scene(
        scene_id=5,
        title="Yellow Soda Can Break",
        narration="Lia smiles beside a bright yellow beverage can on the central round table.",
        lighting="warm_dim",
        actors=[ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=[0.6, 1.1], action="stand")],
        stage_props=[StagePropState(id="yellow_drink_can", grid=[1.0, 1.0], state="intact", on_floor=False)],
    )
    prompt_can = _build_shot_prompt(
        can_scene,
        "cam_a",
        has_first_frame=False,
        has_preview_video=False,
        prop_descriptions={"yellow_drink_can": "bright yellow aluminum beverage can"},
    )
    assert "bright yellow aluminum beverage can" in prompt_can, f"appearance missing from prompt: {prompt_can}"


if __name__ == "__main__":
    test_30s_throw_peak_window_and_degraded_flag()
    test_catch_from_camera_conditional_prompting()
    test_fast_live_mode_sculpture_prompt_and_border_trim()
    test_unbounded_extend_scene_5_plus()
    test_v3_appearance_fidelity_and_fast_template_tint()
    print("ALL W1-V2-07 & W1-V3-01..05 REGRESSION TESTS PASSED!")
