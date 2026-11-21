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
        "/assets/props/showcase_origami_crane.png",
        "/assets/props/showcase_brass_pocket_watch.png",
        "/assets/props/showcase_crystal_shield.png",
        "/assets/props/showcase_glowing_quill.png",
        "/assets/props/showcase_star_lantern.png",
        "/assets/finals/demo_comparison_be_vs_he.mp4",
    ):
        resp = client.get(asset_url)
        assert resp.status_code == 200, f"Static asset missing: {asset_url} -> {resp.status_code}"

    # 3. Verify cached branch_v1, branch_v2_cached & branch_v3_starlight_cached media URLs serve 200 OK and use H.264
    from backend.media.video_renderer import _merge_dual_cam_qa, get_render_progress, is_browser_h264

    for branch_id in ("branch_v1", "branch_v2_cached", "branch_v3_starlight_cached"):
        target_branch = next((b for b in branches if b["branch_id"] == branch_id), None)
        if target_branch is None:
            b_resp = client.get(f"/api/branches/{branch_id}")
            assert b_resp.status_code == 200, f"{branch_id} missing"
            target_branch = b_resp.json()
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
    from backend.schemas import JudgeItem, JudgeResult
    merged_qa = _merge_dual_cam_qa(
        JudgeResult(overall_score=0.9, all_passed=True, items=[JudgeItem(question="Cam A check", passed=True, confidence=0.9, reason="ok")]),
        JudgeResult(overall_score=0.8, all_passed=True, items=[JudgeItem(question="Cam B check", passed=True, confidence=0.8, reason="ok")]),
        cross_cam_item=JudgeItem(question="[Dual-Cam] cross", passed=True, confidence=0.85, reason="ok"),
    )
    assert len(merged_qa.items) == 3
    assert merged_qa.items[0].question.startswith("[CAM A]")
    assert merged_qa.items[1].question.startswith("[CAM B]")

    # 4. Verify POST /api/media/create-prop contract using an in-memory test image (fast demo-safe check)
    prev_demo = config.DEMO_MODE
    created_smoke_prop_id: str | None = None
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
        created_smoke_prop_id = prop_json["prop_id"]
        assert created_smoke_prop_id.startswith("smoke_prop")
        assert prop_json["image_url"] == f"/assets/props/{created_smoke_prop_id}.png"
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
        from backend.media.prop_maker import _chroma_key_green_to_rgba, estimate_prop_height_m
        from backend.media.video_renderer import (
            _build_shot_prompt,
            _prepare_preview_media_sync,
            _resolve_clean_actor_ref,
        )
        from backend.schemas import Scene as SceneModel

        start_img, raw_vid = _prepare_preview_media_sync(config.FINALS_DIR / "scene_1_cam_a.mp4", target_duration_sec=3)
        assert start_img is not None and start_img.is_file(), "Expected extracted start frame"
        assert raw_vid is None, "Expected mismatched duration preview video to be safely bypassed"
        start_img.unlink(missing_ok=True)

        # 7. Verify deep optimizations: border floodfill keeps green objects intact, no grid brackets,
        #    prop dedup, nighttime lock, umbrella-free Lia ref, and SI height estimation.
        green_test = Image.new("RGB", (120, 120), (0, 255, 0))
        # Draw a solid dark-outlined green leaf/bottle centered inside chroma-green bg
        for y in range(35, 85):
            for x in range(35, 85):
                is_outline = x in (35, 36, 83, 84) or y in (35, 36, 83, 84)
                green_test.putpixel((x, y), (20, 40, 20) if is_outline else (45, 165, 65))
        gbuf = io.BytesIO()
        green_test.save(gbuf, format="PNG")
        cutout = _chroma_key_green_to_rgba(gbuf.getvalue())
        center_alpha = cutout.getpixel((cutout.width // 2, cutout.height // 2))[3]
        assert center_alpha == 255, f"Border floodfill punched hole in interior green prop! alpha={center_alpha}"

        b2 = next(b for b in branches if b["branch_id"] == "branch_v2_cached")
        s4_prompt = _build_shot_prompt(SceneModel(**b2["scenes"][3]), "cam_a", False, False)
        assert "grid [" not in s4_prompt and "[0.95" not in s4_prompt, "Raw grid coordinates leaked into prompt"
        assert "NIGHTTIME" in s4_prompt, "Nighttime lock missing"
        stage_props_section = s4_prompt.split("Stage props:", 1)[1].split("Persistent", 1)[0]
        assert "star keepsake" not in stage_props_section.lower(), f"Duplicate star_token in Stage props: {stage_props_section}"
        assert _resolve_clean_actor_ref(config.SHARED_ASSETS_DIR / "lia.png").name == "lia_clean_ref.png"
        assert estimate_prop_height_m("star_token") == 0.10
        assert estimate_prop_height_m("crystal_vase") == 0.32
    finally:
        config.DEMO_MODE = prev_demo
        (config.PROPS_DIR / "smoke_prop.png").unlink(missing_ok=True)
        if created_smoke_prop_id:
            (config.PROPS_DIR / f"{created_smoke_prop_id}.png").unlink(missing_ok=True)
        # Restore branch_v1 snapshot untouched by smoke_prop
        from backend.director.state_tree import save_branch
        from backend.schemas import BranchState
        save_branch(BranchState(**branch_v1))

    print("ALL P2 SMOKE TESTS PASSED!")


if __name__ == "__main__":
    run_smoke_tests()
