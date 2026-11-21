"""
W1-T6F: Automated 3s `.webm` Physical-Prop Frame Extraction Regression Test (Owned by P2).

Generates a synthetic 3.0s `.webm` clip inside a Windows Unicode temporary directory:
- t = 0.0s .. 1.0s: dark slate blank studio frame
- t = 1.2s .. 1.9s: bright warm red/gold target emblem centered at t = 1.5s
- t = 2.0s .. 3.0s: cool dim teal frame

Verifies `_extract_image_from_bytes(webm_bytes, timestamp_sec=1.5)` extracts the
midpoint t=1.5s object frame rather than frame 0, and validates the output JPEG
plus `create_prop(..., frame_timestamp_sec=1.5)` under `DEMO_MODE=True`.
"""

import asyncio
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image

from backend import config
from backend.media.prop_maker import _extract_image_from_bytes, create_prop


def run_webm_prop_regression() -> dict:
    """Create a 3s `.webm` under a Unicode Windows path and assert exact `t=1.5s` extraction."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    unicode_dir = Path(tempfile.mkdtemp(prefix="StageState_Gemini·物理测试_"))
    avi_tmp = unicode_dir / "source_3s.avi"
    webm_unicode = unicode_dir / "prop_clip_物理.webm"

    try:
        fps = 24
        width, height = 320, 240
        writer = cv2.VideoWriter(str(avi_tmp), cv2.VideoWriter_fourcc(*"MJPG"), float(fps), (width, height))
        assert writer.isOpened(), "Failed to open temporary AVI writer"

        for idx in range(fps * 3):
            t = idx / float(fps)
            if 1.2 <= t <= 1.9:
                # Warm golden-red disk visible strictly around t=1.5s
                frame = np.full((height, width, 3), (30, 45, 70), dtype=np.uint8)
                cv2.circle(frame, (width // 2, height // 2), 68, (20, 195, 250), -1)
            elif t < 1.2:
                # Frame 0 dark blue
                frame = np.full((height, width, 3), (40, 15, 10), dtype=np.uint8)
            else:
                frame = np.full((height, width, 3), (80, 60, 20), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        # Encode to VP8 `.webm` via ASCII temp dir and copy into the Unicode Windows path.
        ascii_webm = Path(tempfile.gettempdir()) / "stagestate_t6f_src.webm"
        try:
            proc = subprocess.run(
                [
                    ffmpeg_exe,
                    "-y",
                    "-i",
                    str(avi_tmp),
                    "-c:v",
                    "libvpx",
                    "-b:v",
                    "400k",
                    "-an",
                    str(ascii_webm),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0 and ascii_webm.is_file(), f"ffmpeg webm encode failed: {proc.stderr}"
            shutil.copy2(ascii_webm, webm_unicode)
        finally:
            ascii_webm.unlink(missing_ok=True)

        webm_bytes = webm_unicode.read_bytes()
        extracted = _extract_image_from_bytes(webm_bytes, timestamp_sec=1.5)
        assert extracted is not None, "_extract_image_from_bytes returned None"
        jpeg_bytes, mime_type = extracted
        assert mime_type == "image/jpeg" and len(jpeg_bytes) > 500

        with Image.open(io.BytesIO(jpeg_bytes)) as im:
            assert im.format == "JPEG"
            rgb = np.array(im.convert("RGB"))

        center_px = rgb[height // 2, width // 2]
        # Must capture the golden-red emblem (R >= 180, G >= 150, B <= 90) at t=1.5s, NOT dark frame 0!
        assert int(center_px[0]) >= 180 and int(center_px[1]) >= 150 and int(center_px[2]) <= 90, (
            f"Extracted wrong timestamp frame! center RGB={center_px.tolist()}"
        )

        # Also verify create_prop handles frame_timestamp_sec end-to-end safely
        prev_demo = config.DEMO_MODE
        config.DEMO_MODE = True
        try:
            pid, item = asyncio.run(
                create_prop(
                    name="T6F Test Medallion",
                    description="Golden test emblem",
                    camera_frame_bytes=webm_bytes,
                    frame_timestamp_sec=1.5,
                )
            )
            assert pid.startswith("t6f_test_medallion_")
            assert item.origin == "camera_physical"
        finally:
            config.DEMO_MODE = prev_demo
            out_png = config.ASSETS_DIR / item.image_url.removeprefix("/assets/")
            out_png.unlink(missing_ok=True)

        return {
            "passed": True,
            "unicode_path_tested": str(webm_unicode),
            "extracted_jpeg_bytes": len(jpeg_bytes),
            "center_rgb_at_1_5s": [int(c) for c in center_px],
        }
    finally:
        shutil.rmtree(unicode_dir, ignore_errors=True)


if __name__ == "__main__":
    res = run_webm_prop_regression()
    print("W1-T6F WEBM PROP REGRESSION PASSED:", res)
