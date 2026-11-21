"""
W1-T7B: Live Failover Dry-Run (`corrupt .webm` / PNG Fallback Regression) (Owned by P2).

Feature & Use Cases:
- Verifies that when a browser sends an empty or corrupt `.webm` blob alongside valid
  `cam_a_start_png` / `cam_b_start_png` blueprints (or raw corrupt bytes to `create_prop()`):
  1. `_extract_image_from_bytes(b"")` and `_extract_image_from_bytes(b"NOT_A_VALID_WEBM...")`
     return `None` cleanly without raising unhandled exceptions.
  2. `_prepare_preview_media_sync(corrupt_webm, 4)` returns `(None, None)` cleanly without crashing.
  3. `render_scene_media()` with corrupt preview paths or blueprint PNG start-frames completes
     cleanly with valid `SceneMedia` URLs and zero unhandled exceptions.
  4. `create_prop()` given corrupt camera bytes still returns a valid RGBA PNG safely.
- Exports pass log to `backend/assets/eval_failover_rehearsal.json`.
"""

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import config
from backend.director.state_tree import get_branch
from backend.media.prop_maker import _extract_image_from_bytes, create_prop
from backend.media.video_renderer import (
    _prepare_preview_media_sync,
    render_scene_media,
    render_single_camera_shot,
)
from backend.schemas import JudgeItem, JudgeResult


async def run_failover_rehearsal() -> dict:
    """Execute end-to-end live failover rehearsal tests and export JSON report."""
    t0 = time.perf_counter()
    tmp_dir = Path(tempfile.mkdtemp(prefix="stagestate_failover_"))
    created_files: list[Path] = []
    prev_demo = config.DEMO_MODE
    prev_key = config.GEMINI_API_KEY

    try:
        # 1. Empty & corrupt .webm bytes in prop_maker._extract_image_from_bytes
        empty_res = _extract_image_from_bytes(b"", timestamp_sec=1.5)
        corrupt_res = _extract_image_from_bytes(b"\x1a\x45\xdf\xa3CORRUPT_WEBM_GARBAGE_012345", timestamp_sec=1.5)
        assert empty_res is None, f"Expected None on empty bytes, got {empty_res}"
        assert corrupt_res is None, f"Expected None on corrupt webm bytes, got {corrupt_res}"

        # 2. Corrupt & zero-byte .webm files in video_renderer._prepare_preview_media_sync
        empty_webm = tmp_dir / "empty.webm"
        corrupt_webm = tmp_dir / "corrupt_测试.webm"
        empty_webm.write_bytes(b"")
        corrupt_webm.write_bytes(b"RIFF_BROKEN_WEBM_STREAM")
        p_empty = _prepare_preview_media_sync(empty_webm, target_duration_sec=4)
        p_corrupt = _prepare_preview_media_sync(corrupt_webm, target_duration_sec=4)
        assert p_empty == (None, None), f"Expected (None, None) for empty webm, got {p_empty}"
        assert p_corrupt == (None, None), f"Expected (None, None) for corrupt webm, got {p_corrupt}"

        # 3. Valid PNG start-frame fallback when .webm fails: verify render_scene_media succeeds
        b1 = get_branch("branch_v1")
        assert b1 is not None
        scene2 = b1.scenes[1].model_copy(deep=True)
        bp_a = config.PREVIEWS_DIR / "blueprint_branch_v1_s2_cam_a.png"
        bp_b = config.PREVIEWS_DIR / "blueprint_branch_v1_s2_cam_b.png"
        assert bp_a.is_file() and bp_b.is_file(), "Blueprint PNG start frames missing"

        fake_mp4 = tmp_dir / "fallback_ok.mp4"
        fake_tail = tmp_dir / "fallback_tail.png"
        fake_mp4.write_bytes(b"MOCK_H264_OK")
        shutil.copy2(bp_a, fake_tail)
        qa_ok = JudgeResult(
            overall_score=0.86,
            all_passed=True,
            items=[JudgeItem(question="Fallback check", passed=True, confidence=0.86, reason="ok")],
        )

        uploaded_seen: list[str] = []

        async def mock_upload(path: Path, use_cache: bool = True):
            uploaded_seen.append(path.name)
            return (f"uri://{path.name}", "image/png")

        async def mock_take(**kwargs):
            # Verify PNG start frame was uploaded cleanly (no corrupt video part)
            assert kwargs["uploaded_preview"] is None
            # Each real candidate take writes its own unique job_uid temp artifact
            uid = f"{kwargs['cam_id']}_{kwargs['take_idx']}_{kwargs.get('job_uid', 't')}"
            per_mp4 = tmp_dir / f"ok_{uid}.mp4"
            per_tail = tmp_dir / f"ok_{uid}_tail.png"
            per_mp4.write_bytes(b"MOCK_H264_OK")
            shutil.copy2(bp_a, per_tail)
            return (per_mp4, per_tail, qa_ok, 0.88)

        config.DEMO_MODE = False
        config.GEMINI_API_KEY = "failover-test-key"
        with (
            patch("backend.media.video_renderer._get_or_upload_uri", side_effect=mock_upload),
            patch("backend.media.video_renderer._run_candidate_take", side_effect=mock_take),
            patch("backend.media.video_renderer._check_cross_camera_consistency", new=AsyncMock(return_value=None)),
            patch("backend.media.video_renderer._append_eval_media_stat"),
        ):
            # 3a. Corrupt .webm preview gracefully degrades without crashing
            media_corrupt = await render_scene_media(
                scene=scene2,
                reference_images=[str(config.SHARED_ASSETS_DIR / "lia.png")],
                cam_a_preview_path=str(corrupt_webm),
                cam_b_preview_path=str(corrupt_webm),
                overwrite_canonical=False,
            )
            assert media_corrupt.cam_a_final_url and media_corrupt.cam_b_final_url
            for u in (
                media_corrupt.cam_a_final_url,
                media_corrupt.cam_b_final_url,
                media_corrupt.cam_a_tail_frame_url,
                media_corrupt.cam_b_tail_frame_url,
            ):
                if u and u.startswith("/assets/"):
                    created_files.append(config.ASSETS_DIR / u.removeprefix("/assets/"))

            # 3b. Valid PNG start-frame fallback uploads start-frame PNG cleanly
            uploaded_seen.clear()
            media_png = await render_scene_media(
                scene=scene2,
                reference_images=[str(config.SHARED_ASSETS_DIR / "lia.png")],
                cam_a_preview_path=str(bp_a),
                cam_b_preview_path=str(bp_b),
                overwrite_canonical=False,
            )
            assert bp_a.name in uploaded_seen and bp_b.name in uploaded_seen, f"PNG start frames not anchored: {uploaded_seen}"
            for u in (
                media_png.cam_a_final_url,
                media_png.cam_b_final_url,
                media_png.cam_a_tail_frame_url,
                media_png.cam_b_tail_frame_url,
            ):
                if u and u.startswith("/assets/"):
                    created_files.append(config.ASSETS_DIR / u.removeprefix("/assets/"))

        # 4. create_prop() resilience under corrupt bytes (DEMO_MODE fallback)
        config.DEMO_MODE = True
        pid, item = await create_prop(
            name="failover_test_charm",
            description="corrupt webm resilience check",
            camera_frame_bytes=b"INVALID_CORRUPT_WEBM_BYTES",
            frame_timestamp_sec=1.5,
        )
        prop_file = config.PROPS_DIR / f"{pid}.png"
        assert prop_file.is_file()
        created_files.append(prop_file)

        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "all_passed": True,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "checks": {
                "empty_and_corrupt_webm_extract_safe": True,
                "corrupt_unicode_webm_preview_guard_safe": True,
                "render_scene_media_corrupt_webm_no_crash": True,
                "render_scene_media_png_start_frame_fallback_anchored": True,
                "anchored_start_frames": [bp_a.name, bp_b.name],
                "create_prop_corrupt_bytes_resilient": True,
            },
        }
        out_path = config.ASSETS_DIR / "eval_failover_rehearsal.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("W1-T7B FAILOVER REHEARSAL PASSED:", json.dumps(report, indent=2))
        return report
    finally:
        config.DEMO_MODE = prev_demo
        config.GEMINI_API_KEY = prev_key
        for p in created_files:
            p.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_failover_rehearsal())
