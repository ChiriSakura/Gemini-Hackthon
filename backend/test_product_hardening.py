"""
Product-Grade Hardening & Feature Verification Suite.

Verifies:
1. `GET /api/world/manifest` returns structured WorldManifest with room refs, actors & fast templates.
2. Upload size guardrail rejects oversized uploads with HTTP 413 Payload Too Large.
3. `DELETE /api/branches/{branch_id}` blocks deletion of protected presets (`branch_v1`, `*_cached` -> 403)
   while cleanly deleting temporary live branches (`branch_v999` -> 200).
4. `GET /api/media/progress/{branch_id}/stream?once=true` streams valid SSE `text/event-stream` JSON.
5. `POST /api/media/export-branch` stitches rendered scenes of `branch_v1` into a valid H.264+AAC MP4
   and serves it with HTTP 200 (cleaning up the temporary export afterward).
6. `backend/assets/metrics_summary_for_submission.json` has non-null burst stress & V2 benchmark fields.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from backend import config
from backend.director.state_tree import get_branch, save_branch
from backend.main import app
from backend.media.build_metrics_summary import build_metrics_summary
from backend.media.video_renderer import (
    CAM_B_SAME_VIEW_THRESHOLD,
    _action_phrase,
    _build_size_chart,
    _cam_b_blocking,
    _compose_split_frame_sync,
    _crop_split_panel_sync,
    _same_view_score,
    is_browser_h264,
    publish_scene_progress,
    render_scene_media,
)
from backend.schemas import ActorState, Scene


def run_product_hardening_tests() -> None:
    client = TestClient(app)

    # 1. World Pack manifest contract
    r_world = client.get("/api/world/manifest")
    assert r_world.status_code == 200, r_world.text
    world = r_world.json()
    assert world["world_id"] == "glass_conservatory_v1", world
    assert "cam_a" in world["room_refs"] and "cam_b" in world["room_refs"], world
    assert len(world["actors"]) >= 2, world
    assert "soda_can" in world["fast_prop_templates"], world
    assert "branch_v1" in world["protected_branches"], world

    # 2. Upload size ceiling -> HTTP 413
    prev_limit = config.MAX_IMAGE_UPLOAD_BYTES
    config.MAX_IMAGE_UPLOAD_BYTES = 64
    try:
        r_big = client.post(
            "/api/media/create-prop",
            data={"name": "TooBig", "description": "oversized", "branch_id": "branch_v1"},
            files={"image": ("big.png", b"\x89PNG" + (b"\x00" * 128), "image/png")},
        )
        assert r_big.status_code == 413, f"Expected 413, got {r_big.status_code}: {r_big.text}"
    finally:
        config.MAX_IMAGE_UPLOAD_BYTES = prev_limit

    # 3. Protected branch delete guard (403) + temporary branch cleanup (200)
    assert client.delete("/api/branches/branch_v1").status_code == 403
    assert client.delete("/api/branches/branch_v2_cached").status_code == 403
    base = get_branch("branch_v1")
    assert base is not None
    tmp_branch = base.model_copy(deep=True)
    tmp_branch.branch_id = "branch_v999"
    save_branch(tmp_branch)
    try:
        r_del = client.delete("/api/branches/branch_v999")
        assert r_del.status_code == 200, r_del.text
        assert r_del.json()["deleted"] is True
        assert client.get("/api/branches/branch_v999").status_code == 404
    finally:
        (config.BRANCHES_DIR / "branch_v999.json").unlink(missing_ok=True)

    # 3b. Interactive 3D drag move-actor + table clearance guard
    tmp_move = base.model_copy(deep=True)
    tmp_move.branch_id = "branch_v998"
    save_branch(tmp_move)
    try:
        r_move = client.post(
            "/api/director/move-actor",
            data={"branch_id": "branch_v998", "scene_id": "1", "actor_id": "lia", "grid_x": "1.0", "grid_y": "1.0"},
        )
        assert r_move.status_code == 200, r_move.text
        moved_grid = r_move.json()["grid"]
        assert moved_grid != [1.0, 1.0], f"Table clearance failed: {moved_grid}"
    finally:
        (config.BRANCHES_DIR / "branch_v998.json").unlink(missing_ok=True)

    # 4. Real-time SSE progress stream (`text/event-stream`)
    publish_scene_progress("branch_v1", 2, "rendering", branch_status="running")
    r_sse = client.get("/api/media/progress/branch_v1/stream?once=true")
    assert r_sse.status_code == 200, r_sse.text
    assert "text/event-stream" in (r_sse.headers.get("content-type") or "")
    data_lines = [ln for ln in r_sse.text.splitlines() if ln.startswith("data: ")]
    assert data_lines, f"No SSE data frame in: {r_sse.text!r}"
    sse_payload = json.loads(data_lines[0].removeprefix("data: "))
    assert sse_payload["branch_id"] == "branch_v1"
    assert sse_payload["scenes"]["2"]["status"] == "rendering"
    publish_scene_progress("branch_v1", 2, "done", qa_score=0.91, fallback=False, branch_status="completed")

    # 5. Branch stitched film export (`cam_a` H.264+AAC MP4)
    export_path = config.FINALS_DIR / "export_branch_v1_cam_a.mp4"
    try:
        r_exp = client.post("/api/media/export-branch", data={"branch_id": "branch_v1", "mode": "cam_a"})
        assert r_exp.status_code == 200, r_exp.text
        exp = r_exp.json()
        assert exp["scene_count"] == 4, exp
        assert exp["is_h264"] is True, exp
        assert exp["bytes"] > 100_000, exp
        r_vid = client.get(exp["video_url"])
        assert r_vid.status_code == 200, f"Exported video not served: {exp['video_url']}"
    finally:
        export_path.unlink(missing_ok=True)

    # 6. Regenerate and assert zero-null submission summary metrics
    summary = build_metrics_summary()
    mm = summary["multimodal_and_robustness"]
    assert mm["burst_stress_wall_sec"] is not None, mm
    assert mm["burst_stress_deadlock_free"] is True, mm
    assert mm["fixed_room_5_criteria_rate_0_80"] == 1.0, mm
    assert mm["fast_live_scene5_qa_score"] is not None, mm

    # 7. Round-3 Cam-B-from-A geometry, Unicode-safe same-view detector, drink/eat/hold_up actions & granular SSE phases
    drink_scene = Scene(
        scene_id=7,
        title="Tea Sip",
        narration="Lia lifts the yellow soda can and takes a sip while Nyx watches.",
        lighting="warm_dim",
        actors=[
            ActorState(id="lia", name="Lia", asset_url="/assets/actors/lia.png", grid=(0.0, 1.0), facing="right", action="drink", holding_prop="yellow_soda_can"),
            ActorState(id="nyx", name="Nyx", asset_url="/assets/actors/nyx.png", grid=(2.0, 1.0), facing="left", action="sit"),
        ],
    )
    assert "drinking" in _action_phrase("drink")
    assert "eating" in _action_phrase("eat")
    assert "holding" in _action_phrase("hold_up")
    chart = _build_size_chart(drink_scene)
    assert "Lia is 1.62 m tall" in chart and "yellow soda can" in chart, chart
    blocking = _cam_b_blocking(drink_scene)
    assert "Lia" in blocking and "Nyx" in blocking, blocking

    with tempfile.TemporaryDirectory(prefix="StageState_视图测试_") as tmpdir:
        p_a = Path(tmpdir) / "cam_a_画面.png"
        p_same = Path(tmpdir) / "cam_b_same.png"
        p_diff = Path(tmpdir) / "cam_b_orbit.png"
        real_a = config.TAILS_DIR / "scene_1_cam_a_f0a5fb_tail.png"
        real_b = config.TAILS_DIR / "scene_1_cam_b_270cea_tail.png"
        p_a.write_bytes(real_a.read_bytes())
        p_same.write_bytes(real_a.read_bytes())
        p_diff.write_bytes(real_b.read_bytes())

        same_score = _same_view_score(p_a, p_same)
        diff_score = _same_view_score(p_a, p_diff)
        assert same_score >= CAM_B_SAME_VIEW_THRESHOLD, f"Expected same>=0.6, got {same_score}"
        assert diff_score < CAM_B_SAME_VIEW_THRESHOLD, f"Expected orbit<0.6, got {diff_score}"

        # 8. Split-screen 1-pass dual-cam composition & bundled-ffmpeg panel crop (Unicode path safe)
        open_a = config.ASSETS_DIR / "stages" / "opening_cam_a.png"
        open_b = config.ASSETS_DIR / "stages" / "opening_cam_b.png"
        room_a = config.ASSETS_DIR / "stages" / "conservatory_cam_a.png"
        room_b = config.ASSETS_DIR / "stages" / "conservatory_cam_b.png"
        assert open_a.is_file() and open_b.is_file(), "Missing opening_cam_a/b.png"
        assert room_a.is_file() and room_b.is_file(), "Missing conservatory_cam_a/b.png"
        open_score = _same_view_score(open_a, open_b)
        room_score = _same_view_score(room_a, room_b)
        assert open_score < CAM_B_SAME_VIEW_THRESHOLD, f"opening A/B too similar: {open_score}"
        assert room_score < CAM_B_SAME_VIEW_THRESHOLD, f"conservatory A/B too similar: {room_score}"
        split_png = _compose_split_frame_sync(open_a, open_b, Path(tmpdir) / "split_双机位.png")
        assert split_png.is_file() and split_png.stat().st_size > 50_000
        sample_mp4 = config.FINALS_DIR / "scene_1_cam_a_f0a5fb.mp4"
        cropped_left = _crop_split_panel_sync(sample_mp4, False, Path(tmpdir) / "panel_a.mp4")
        cropped_right = _crop_split_panel_sync(sample_mp4, True, Path(tmpdir) / "panel_b.mp4")
        assert is_browser_h264(cropped_left) and is_browser_h264(cropped_right)

        # 9. Camera-directive narration sanitizer prevents divergent left/right panel actions
        from backend.media.video_renderer import _strip_camera_directives

        dirty_nar = (
            "Heavy rain pours down as Nyx flees right; Camera B closes in at 45 degrees on "
            "Lia standing alone beside broken glass in Camera B."
        )
        cleaned_nar = _strip_camera_directives(dirty_nar)
        assert "camera b" not in cleaned_nar.lower() and "camera a" not in cleaned_nar.lower(), cleaned_nar
        assert "Lia standing alone beside broken glass" in cleaned_nar, cleaned_nar

        # 10. 3D-first dual-camera epipolar projection guide & 1280x720 paired split layout
        from backend.media.stage_blueprint import compute_dual_cam_epipolar_guide, render_dual_cam_split_guide

        epipolar = compute_dual_cam_epipolar_guide(drink_scene)
        assert len(epipolar["actors"]) == 2 and "Exact 3D dual-camera geometric projection" in epipolar["prompt_summary"]
        nyx_row = next(a for a in epipolar["actors"] if a["id"] == "nyx")
        lia_row = next(a for a in epipolar["actors"] if a["id"] == "lia")
        assert nyx_row["cam_b"]["depth_m"] < lia_row["cam_b"]["depth_m"], epipolar
        guide_png = render_dual_cam_split_guide(drink_scene, Path(tmpdir) / "guide_3d_split.png")
        assert guide_png.is_file()
        from backend.media.video_renderer import _read_bgr_safe

        guide_img = _read_bgr_safe(guide_png)
        assert guide_img is not None and guide_img.shape == (720, 1280, 3), getattr(guide_img, "shape", None)

    phases: list[str] = []
    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = True
    try:
        asyncio.run(
            render_scene_media(
                drink_scene,
                [str(config.SHARED_ASSETS_DIR / "lia.png")],
                fast_live_mode=True,
                progress_cb=phases.append,
            )
        )
        assert "rendering_cam_a" in phases and "rendering_cam_b" in phases, phases
    finally:
        config.DEMO_MODE = prev_demo

    print("ALL PRODUCT HARDENING TESTS PASSED!", {
        "world_id": world["world_id"],
        "templates": len(world["fast_prop_templates"]),
        "export_bytes": exp["bytes"],
        "burst_stress_wall_sec": mm["burst_stress_wall_sec"],
        "fixed_room_rate": mm["fixed_room_5_criteria_rate_0_80"],
        "sse_phases": phases,
    })


if __name__ == "__main__":
    run_product_hardening_tests()
