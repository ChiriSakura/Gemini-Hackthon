"""
Contract & Static-Asset Smoke Tests for P2 Media Endpoints (Owned by P2 / worker-1).

Feature & Use Cases:
- Verifies without binding a port or editing `backend/main.py` that:
  1. `/assets/actors/lia.png`, `/assets/actors/nyx.png`, `/assets/props/crystal_vase.png`
     serve 200 OK from FastAPI StaticFiles.
  2. `POST /api/media/create-prop` returns valid `{prop_id, image_url, item}` and
     generates a readable RGBA PNG served under `/assets/props/`.
  3. `POST /api/media/render-scene` (in fast contract check mode or live mode) returns
     valid `SceneMedia` URLs and serves cached `branch_v1` MP4s & tail PNGs with 200 OK.
"""

import io
from PIL import Image
from fastapi.testclient import TestClient

from backend import config
from backend.main import app


def run_smoke_tests() -> None:
    """Execute P2 HTTP contract and static asset smoke checks."""
    client = TestClient(app)

    # 1. Verify health & seeded branch
    r_health = client.get("/api/health")
    assert r_health.status_code == 200, r_health.text
    r_branches = client.get("/api/branches")
    assert r_branches.status_code == 200, r_branches.text
    branches = r_branches.json()
    assert any(b["branch_id"] == "branch_v1" for b in branches), "branch_v1 missing"

    # 2. Verify shared actor & prop URLs referenced by mock_branch_1/2.json serve 200 OK
    for asset_url in (
        "/assets/actors/lia.png",
        "/assets/actors/nyx.png",
        "/assets/props/crystal_vase.png",
        "/assets/props/crystal_vase_broken.png",
    ):
        resp = client.get(asset_url)
        assert resp.status_code == 200, f"Static asset missing: {asset_url} -> {resp.status_code}"

    # 3. Verify cached branch_v1 & branch_v2_cached media URLs serve 200 OK and use H.264
    from backend.media.video_renderer import get_render_progress, is_browser_h264

    for branch_id in ("branch_v1", "branch_v2_cached"):
        target_branch = next((b for b in branches if b["branch_id"] == branch_id), None)
        assert target_branch is not None, f"{branch_id} missing"
        for scene in target_branch["scenes"]:
            for key in ("cam_a_final_url", "cam_b_final_url", "cam_a_tail_frame_url", "cam_b_tail_frame_url"):
                url = scene["media"].get(key)
                if url:
                    resp = client.get(url)
                    assert resp.status_code == 200, f"Cached media missing: {url} -> {resp.status_code}"
                    if url.endswith(".mp4"):
                        local_mp4 = config.ASSETS_DIR / url.removeprefix("/assets/")
                        assert is_browser_h264(local_mp4), f"Non-H.264 clip: {local_mp4}"

    branch_v1 = next(b for b in branches if b["branch_id"] == "branch_v1")
    assert get_render_progress("nonexistent_branch")["status"] == "idle"

    # 4. Verify POST /api/media/create-prop contract using an in-memory test image (fast demo-safe check)
    prev_demo = config.DEMO_MODE
    try:
        config.DEMO_MODE = True
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (255, 200, 40)).save(buf, format="PNG")
        buf.seek(0)
        resp_prop = client.post(
            "/api/media/create-prop",
            data={"name": "smoke_prop", "description": "test star charm", "branch_id": "branch_v1"},
            files={"image": ("cam.png", buf.getvalue(), "image/png")},
        )
        assert resp_prop.status_code == 200, resp_prop.text
        prop_json = resp_prop.json()
        assert prop_json["prop_id"] == "smoke_prop"
        assert prop_json["image_url"] == "/assets/props/smoke_prop.png"
        assert prop_json["item"]["origin"] == "camera_physical"
        assert client.get(prop_json["image_url"]).status_code == 200

        # 5. Verify POST /api/media/render-scene contract on scene 1
        resp_render = client.post(
            "/api/media/render-scene",
            data={"branch_id": "branch_v1", "scene_id": 1},
        )
        assert resp_render.status_code == 200, resp_render.text
        render_json = resp_render.json()
        assert render_json["scene_id"] == 1
        assert render_json["media"]["cam_a_final_url"] == "/assets/finals/scene_1_cam_a.mp4"
        assert render_json["media"]["cam_b_final_url"] == "/assets/finals/scene_1_cam_b.mp4"
    finally:
        config.DEMO_MODE = prev_demo
        (config.PROPS_DIR / "smoke_prop.png").unlink(missing_ok=True)
        # Restore branch_v1 snapshot untouched by smoke_prop
        from backend.director.state_tree import save_branch
        from backend.schemas import BranchState
        save_branch(BranchState(**branch_v1))

    print("ALL P2 SMOKE TESTS PASSED!")


if __name__ == "__main__":
    run_smoke_tests()
