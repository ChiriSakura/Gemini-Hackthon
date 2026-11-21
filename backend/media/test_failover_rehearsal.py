"""
W1-T7B: Live Failover Dry-Run (`corrupt .webm` / PNG Fallback Regression) (Owned by P2).

Verifies that passing an empty or corrupt `.webm` preview alongside valid
`cam_a_start_png` / `cam_b_start_png` never crashes `_prepare_preview_media_sync()`,
`create_prop()`, or `POST /api/media/render-scene`, cleanly falling back to PNG/demo
rendering and saving the rehearsal report to `backend/assets/eval_failover_rehearsal.json`.
"""

import io
import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from backend import config
from backend.director.state_tree import get_branch, save_branch
from backend.main import app
from backend.media.prop_maker import _extract_image_from_bytes
from backend.media.video_renderer import _prepare_preview_media_sync
from backend.schemas import BranchState


def run_failover_rehearsal() -> dict:
    """Exercise empty/corrupt .webm inputs with PNG fallbacks end-to-end."""
    t0 = time.perf_counter()
    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="stagestate_failover_") as tmp_dir:
        tmp = Path(tmp_dir)
        empty_webm = tmp / "empty.webm"
        empty_webm.write_bytes(b"")
        corrupt_webm = tmp / "corrupt.webm"
        corrupt_webm.write_bytes(b"NOT_A_VALID_WEBM_HEADER\x00\xff\xfe\x12\x34")

        # 1. Direct _prepare_preview_media_sync resilience on empty & corrupt .webm
        s_img1, r_vid1 = _prepare_preview_media_sync(empty_webm, 4)
        assert s_img1 is None and r_vid1 is None
        results.append({"case": "empty_webm_prepare_preview", "passed": True, "graceful_none": True})

        s_img2, r_vid2 = _prepare_preview_media_sync(corrupt_webm, 4)
        assert s_img2 is None and r_vid2 is None
        results.append({"case": "corrupt_webm_prepare_preview", "passed": True, "graceful_none": True})

        # 2. _extract_image_from_bytes resilience on corrupt .webm bytes
        extracted_bad = _extract_image_from_bytes(corrupt_webm.read_bytes(), timestamp_sec=1.5)
        assert extracted_bad is None
        results.append({"case": "corrupt_webm_prop_extract", "passed": True, "graceful_none": True})

        # 3. Full HTTP /api/media/render-scene failover: corrupt .webm UploadFile + valid PNG fallback
        client = TestClient(app)
        b1 = get_branch("branch_v1")
        assert b1 is not None
        snapshot = b1.model_copy(deep=True)
        prev_demo = config.DEMO_MODE
        config.DEMO_MODE = True
        try:
            png_buf = io.BytesIO()
            Image.new("RGB", (640, 360), (24, 34, 58)).save(png_buf, format="PNG")
            import base64

            png_b64 = "data:image/png;base64," + base64.b64encode(png_buf.getvalue()).decode("ascii")
            resp = client.post(
                "/api/media/render-scene",
                data={
                    "branch_id": "branch_v1",
                    "scene_id": "2",
                    "cam_a_start_png": png_b64,
                    "cam_b_start_png": png_b64,
                },
                files={
                    "cam_a_preview": ("bad_a.webm", corrupt_webm.read_bytes(), "video/webm"),
                    "cam_b_preview": ("empty_b.webm", b"", "video/webm"),
                },
            )
            assert resp.status_code == 200, f"HTTP failover crashed: {resp.status_code} {resp.text}"
            payload = resp.json()
            assert payload.get("scene_id") == 2 and "media" in payload
            results.append(
                {
                    "case": "http_render_scene_corrupt_webm_with_png_fallback",
                    "passed": True,
                    "http_status": resp.status_code,
                    "scene_id": payload["scene_id"],
                }
            )
        finally:
            config.DEMO_MODE = prev_demo
            save_branch(BranchState(**snapshot.model_dump()))
            # Remove temporary preview files created during dry run
            for fname in (
                "branch_v1_s2_cam_a.webm",
                "branch_v1_s2_cam_b.webm",
            ):
                (config.PREVIEWS_DIR / fname).unlink(missing_ok=True)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "all_passed": all(r["passed"] for r in results),
        "cases_tested": len(results),
        "elapsed_sec": round(time.perf_counter() - t0, 2),
        "cases": results,
    }
    out_path = config.ASSETS_DIR / "eval_failover_rehearsal.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    res = run_failover_rehearsal()
    print("W1-T7B FAILOVER REHEARSAL PASSED:", json.dumps(res, ensure_ascii=False))
