"""
Contract & Static-Asset Smoke Tests for P2 Media Endpoints (Owned by P2 / worker-1, W1-T4A & W1-T4E).

Feature & Use Cases:
- Verifies without binding a port or editing `backend/main.py` that:
  1. `GET /api/branches` loads all 3 cached branches (`branch_v1`, `branch_v2_cached`,
     `branch_v3_starlight_cached`) with complete 4-scene manifests.
  2. Every `cam_a_final_url`, `cam_b_final_url`, `cam_a_tail_frame_url`, and
     `cam_b_tail_frame_url` across all 3 cached branches is non-null, returns HTTP 200 OK,
     and every `.mp4` passes Chrome H.264 (`is_browser_h264`) verification.
  3. All shared actor PNGs, prop PNGs (including `crystal_vase_broken.png`, `star_token.png`,
     and 5 `showcase_*.png` props), and `/assets/finals/demo_comparison_be_vs_he.mp4` return
     HTTP 200 OK.
  4. `POST /api/media/create-prop` and `POST /api/media/render-scene` contracts, non-clobbering
     run tags, and preview duration guards pass without touching canonical branch files.
"""

import io
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from PIL import Image
from fastapi.testclient import TestClient

from backend import config
from backend.main import app

EXPECTED_CACHED_BRANCHES = (
    "branch_v1",
    "branch_v2_cached",
    "branch_v3_starlight_cached",
)

EXPECTED_STATIC_ASSETS = (
    "/assets/actors/lia.png",
    "/assets/actors/nyx.png",
    "/assets/props/crystal_vase.png",
    "/assets/props/crystal_vase_broken.png",
    "/assets/props/star_token.png",
    "/assets/props/showcase_origami_crane.png",
    "/assets/props/showcase_brass_pocket_watch.png",
    "/assets/props/showcase_crystal_shield.png",
    "/assets/props/showcase_glowing_quill.png",
    "/assets/props/showcase_star_lantern.png",
    "/assets/finals/demo_comparison_be_vs_he.mp4",
)


def run_smoke_tests() -> None:
    """Execute P2 HTTP contract, all-branch cached asset HTTP 200, and H.264 smoke checks."""
    client = TestClient(app)

    # 1. Verify health & cached branch inventory
    r_health = client.get("/api/health")
    assert r_health.status_code == 200, r_health.text
    r_branches = client.get("/api/branches")
    assert r_branches.status_code == 200, r_branches.text
    branches = r_branches.json()
    branches_by_id = {b["branch_id"]: b for b in branches}

    for branch_id in EXPECTED_CACHED_BRANCHES:
        assert branch_id in branches_by_id, f"Cached branch missing from GET /api/branches: {branch_id}"

    # 2. Verify all actor PNGs, prop PNGs, showcase props, and BE-vs-HE demo reel serve 200 OK
    from backend.media.video_renderer import get_render_progress, is_browser_h264

    for asset_url in EXPECTED_STATIC_ASSETS:
        resp = client.get(asset_url)
        assert resp.status_code == 200, f"Static asset missing: {asset_url} -> {resp.status_code}"
        if asset_url.endswith(".mp4"):
            local_mp4 = config.ASSETS_DIR / asset_url.removeprefix("/assets/")
            assert is_browser_h264(local_mp4), f"Non-H.264 clip: {local_mp4}"

    # 3. Verify EVERY scene in ALL 3 cached branches has non-null 200 OK final MP4s & tail PNGs
    checked_mp4s: set[str] = set()
    checked_tails: set[str] = set()
    for branch_id in EXPECTED_CACHED_BRANCHES:
        target_branch = branches_by_id[branch_id]
        assert len(target_branch["scenes"]) == 4, f"{branch_id} expected 4 scenes, got {len(target_branch['scenes'])}"
        for scene in target_branch["scenes"]:
            for key in ("cam_a_final_url", "cam_b_final_url", "cam_a_tail_frame_url", "cam_b_tail_frame_url"):
                url = scene["media"].get(key)
                assert url, f"{branch_id} scene {scene['scene_id']} missing {key}"
                resp = client.get(url)
                assert resp.status_code == 200, f"Cached media missing: {url} -> {resp.status_code}"
                if url.endswith(".mp4"):
                    local_mp4 = config.ASSETS_DIR / url.removeprefix("/assets/")
                    assert is_browser_h264(local_mp4), f"Non-H.264 clip: {local_mp4}"
                    checked_mp4s.add(url)
                elif url.endswith(".png"):
                    checked_tails.add(url)

    branch_v1 = branches_by_id["branch_v1"]
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

        # 5. Verify POST /api/media/render-scene contract on scene 1 (unique non-clobbering output URL)
        resp_render = client.post(
            "/api/media/render-scene",
            data={"branch_id": "branch_v1", "scene_id": 1},
        )
        assert resp_render.status_code == 200, resp_render.text
        render_json = resp_render.json()
        assert render_json["scene_id"] == 1
        assert render_json["media"]["cam_a_final_url"].startswith("/assets/finals/scene_1_cam_a_")
        assert render_json["media"]["cam_a_final_url"].endswith(".mp4")
        assert render_json["media"]["cam_b_final_url"].startswith("/assets/finals/scene_1_cam_b_")
        assert render_json["media"]["cam_b_final_url"].endswith(".mp4")

        # 6. Verify preview duration guard extracts crisp start frame and avoids 400 mismatch
        from backend.media.video_renderer import _prepare_preview_media_sync

        start_img, raw_vid = _prepare_preview_media_sync(config.FINALS_DIR / "scene_1_cam_a.mp4", target_duration_sec=3)
        assert start_img is not None and start_img.is_file(), "Expected extracted start frame"
        assert raw_vid is None, "Expected mismatched duration preview video to be safely bypassed"
        start_img.unlink(missing_ok=True)
    finally:
        config.DEMO_MODE = prev_demo
        (config.PROPS_DIR / "smoke_prop.png").unlink(missing_ok=True)
        # Restore branch_v1 snapshot untouched by smoke_prop
        from backend.director.state_tree import save_branch
        from backend.schemas import BranchState

        save_branch(BranchState(**branch_v1))

    print(
        f"ALL P2 SMOKE TESTS PASSED! "
        f"(branches={len(EXPECTED_CACHED_BRANCHES)}, unique_mp4s={len(checked_mp4s)}, "
        f"unique_tails={len(checked_tails)}, static_assets={len(EXPECTED_STATIC_ASSETS)})"
    )


if __name__ == "__main__":
    run_smoke_tests()
