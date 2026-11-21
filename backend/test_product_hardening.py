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

import json

from fastapi.testclient import TestClient

from backend import config
from backend.director.state_tree import get_branch, save_branch
from backend.main import app
from backend.media.build_metrics_summary import build_metrics_summary
from backend.media.video_renderer import publish_scene_progress


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

    print("ALL PRODUCT HARDENING TESTS PASSED!", {
        "world_id": world["world_id"],
        "templates": len(world["fast_prop_templates"]),
        "export_bytes": exp["bytes"],
        "burst_stress_wall_sec": mm["burst_stress_wall_sec"],
        "fixed_room_rate": mm["fixed_room_5_criteria_rate_0_80"],
    })


if __name__ == "__main__":
    run_product_hardening_tests()
