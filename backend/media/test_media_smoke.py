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
        "/assets/props/showcase_desk_coffee_mug.png",
        "/assets/props/showcase_desk_paper_heart.png",
        "/assets/props/showcase_desk_brass_key.png",
        "/assets/props/red_umbrella_closed.png",
        "/assets/props/red_umbrella_open.png",
        "/assets/props/wooden_chair.png",
        "/assets/props/showcase_everyday_soda_can.png",
        "/assets/props/showcase_everyday_yellow_soda_can.png",
        "/assets/props/showcase_everyday_black_smartphone.png",
        "/assets/props/showcase_everyday_snack_bag.png",
        "/assets/props/showcase_everyday_laptop.png",
        "/assets/stages/conservatory_cam_a.png",
        "/assets/stages/conservatory_cam_b.png",
        "/assets/showcase_storyboard_strip.png",
        "/assets/finals/demo_comparison_be_vs_he.mp4",
        "/assets/finals/pitch_demo_reel_1min.mp4",
        "/assets/submission/hook_10s_dualcam_twist.mp4",
        "/assets/submission/eval_dashboard_12s.mp4",
        "/assets/submission/eval_charts_board.png",
    ):
        resp = client.get(asset_url)
        assert resp.status_code == 200, f"Static asset missing: {asset_url} -> {resp.status_code}"

    # 3. Verify ALL branches in GET /api/branches pass HTTP 200 on cached media URLs & use H.264
    from backend.media.video_renderer import _merge_dual_cam_qa, get_render_progress, is_browser_h264

    branch_ids = {b["branch_id"] for b in branches}
    for req_id in (
        "branch_v1",
        "branch_v2_cached",
        "branch_v3_starlight_cached",
        "branch_v4_umbrella_shield_cached",
    ):
        assert req_id in branch_ids, f"Expected cached branch {req_id} in GET /api/branches"

    for target_branch in branches:
        for scene in target_branch["scenes"]:
            for key in ("cam_a_final_url", "cam_b_final_url", "cam_a_tail_frame_url", "cam_b_tail_frame_url"):
                url = scene["media"].get(key)
                if url:
                    resp = client.get(url)
                    assert resp.status_code == 200, f"Cached media missing ({target_branch['branch_id']}): {url} -> {resp.status_code}"
                    if url.endswith(".mp4"):
                        local_mp4 = config.ASSETS_DIR / url.removeprefix("/assets/")
                        assert is_browser_h264(local_mp4), f"Non-H.264 clip: {local_mp4}"

    # Verify zero CJK characters remain in persisted branch JSONs (W1-CR3 / W1-T5B)
    import re
    for bjson in sorted(config.BRANCHES_DIR.glob("*.json")):
        cjk_hits = re.findall(r"[\u4e00-\u9fff]+", bjson.read_text(encoding="utf-8"))
        assert not cjk_hits, f"CJK characters found in {bjson.name}: {cjk_hits[:5]}"

    # Strictly verify GET /api/media/progress/{branch_id} returns 200 OK (W1-T5B)
    r_prog = client.get("/api/media/progress/branch_v1")
    assert r_prog.status_code == 200, r_prog.text
    assert r_prog.json().get("branch_id") == "branch_v1"

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
    orig_b1_raw = (config.BRANCHES_DIR / "branch_v1.json").read_bytes()
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
        assert _resolve_clean_actor_ref(
            config.SHARED_ASSETS_DIR / "crystal_vase.png",
            scene=SceneModel(**branch_v1["scenes"][3]),
        ).name == "crystal_vase_broken.png"
        assert estimate_prop_height_m("star_token") == 0.10
        assert estimate_prop_height_m("crystal_vase") == 0.32

        # 8. Verify W1-T4C zero green-spill audit & W1-T5A audio/H.264 audit artifacts
        import json as _json
        ep_path = config.ASSETS_DIR / "eval_prop_persistence.json"
        if ep_path.is_file():
            ep = _json.loads(ep_path.read_text(encoding="utf-8"))
            assert ep.get("all_props_zero_green_spill") is True, f"Green spill remaining: {ep}"
            assert ep.get("all_props_bottom_flush") is True
        ac_path = config.ASSETS_DIR / "eval_audio_codec.json"
        if ac_path.is_file():
            ac = _json.loads(ac_path.read_text(encoding="utf-8"))
            assert ac.get("all_h264") is True
            assert ac.get("all_have_audio_stream") is True
            assert (ac.get("demo_reel") or {}).get("has_audio_stream") is True
            assert (ac.get("pitch_reel") or {}).get("is_browser_h264") is True
            assert (ac.get("pitch_reel") or {}).get("has_audio_stream") is True
        pa_path = config.ASSETS_DIR / "eval_props_audit.json"
        if pa_path.is_file():
            pa = _json.loads(pa_path.read_text(encoding="utf-8"))
            assert pa.get("all_passed") is True and pa.get("all_zero_green_halo") is True
        lp_path = config.ASSETS_DIR / "eval_live_props_sim.json"
        if lp_path.is_file():
            lp = _json.loads(lp_path.read_text(encoding="utf-8"))
            assert lp.get("all_rgba") is True and lp.get("all_zero_green_spill") is True
        cs_path = config.ASSETS_DIR / "eval_cache_savings.json"
        if cs_path.is_file():
            cs = _json.loads(cs_path.read_text(encoding="utf-8"))
            assert cs.get("branches_measured", 0) >= 4
    finally:
        config.DEMO_MODE = prev_demo
        (config.PROPS_DIR / "smoke_prop.png").unlink(missing_ok=True)
        if created_smoke_prop_id:
            (config.PROPS_DIR / f"{created_smoke_prop_id}.png").unlink(missing_ok=True)
        # Restore branch_v1 snapshot byte-for-byte untouched by smoke_prop
        (config.BRANCHES_DIR / "branch_v1.json").write_bytes(orig_b1_raw)

    print("ALL P2 SMOKE TESTS PASSED!")


if __name__ == "__main__":
    run_smoke_tests()
