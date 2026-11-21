"""
W1-T6F: Automated 3s `.webm` Physical-Prop Frame Extraction Regression Test (Owned by P2).

Feature & Use Cases:
- Synthesizes a realistic 3.0s `.webm` video clip (matching Chrome MediaRecorder webcam
  captures) using `imageio_ffmpeg` with visually distinct content at `t=0.0s` (dark blue),
  `t=1.4s` (heavily blurred red card), `t=1.5s` (razor-sharp bright crimson-and-gold star
  prop with crisp high-frequency edges), and `t=2.6s` (teal background).
- Stores the clip inside a temporary Windows Unicode directory (`测试_Gemini路`), loads raw
  bytes, invokes `prop_maker._extract_image_from_bytes(..., timestamp_sec=1.5)`, and
  asserts:
  1. Valid JPEG output bytes (`image/jpeg`, readable via PIL).
  2. Non-frame-0 extraction (red channel clearly dominates blue channel at `t=1.5s`).
  3. Laplacian sharpness selection picks the crisp star frame over blurred neighbors.
  4. Midpoint fallback (`timestamp_sec=None`) also succeeds cleanly.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image

from backend.media.prop_maker import _extract_image_from_bytes, _frame_sharpness_score


def _make_synthetic_3s_webm(out_webm: Path) -> None:
    """Create a 3-second 24fps VP8 `.webm` clip with a sharp red-gold prop peak at t=1.5s."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    width, height, fps = 320, 240, 24
    total_frames = fps * 3  # 72 frames (0.0s .. 3.0s)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_mp4:
        raw_mp4 = Path(tmp_mp4.name)
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_webm:
        ascii_webm = Path(tmp_webm.name)

    try:
        writer = cv2.VideoWriter(str(raw_mp4), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (width, height))
        assert writer.isOpened(), "Failed to create synthetic OpenCV video"
        for i in range(total_frames):
            t = i / fps
            if t < 1.0:
                # 0.0s..1.0s: dark blue frame (must NOT be selected for t=1.5s)
                frame = np.full((height, width, 3), (180, 40, 20), dtype=np.uint8)
            elif t < 2.0:
                # 1.0s..2.0s: crimson-red prop card centered on warm dark background
                frame = np.full((height, width, 3), (35, 35, 195), dtype=np.uint8)
                cv2.rectangle(frame, (80, 50), (240, 190), (40, 210, 245), 6)
                cv2.circle(frame, (160, 120), 42, (255, 255, 255), -1)
                cv2.putText(frame, "STAR", (112, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 180), 3, cv2.LINE_AA)
                # Blur frames away from exact t=1.5s so sharpness picker must select ~1.5s
                if abs(t - 1.5) > 0.08:
                    frame = cv2.GaussianBlur(frame, (21, 21), 0)
            else:
                # 2.0s..3.0s: muted purple/cyan end segment
                frame = np.full((height, width, 3), (170, 160, 30), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-i",
                str(raw_mp4),
                "-c:v",
                "libvpx",
                "-b:v",
                "400k",
                "-an",
                str(ascii_webm),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        out_webm.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ascii_webm, out_webm)
    finally:
        raw_mp4.unlink(missing_ok=True)
        ascii_webm.unlink(missing_ok=True)


def run_webm_prop_regression() -> dict:
    """Run 3s `.webm` frame extraction regression test under a Windows Unicode directory."""
    unicode_root = Path(tempfile.mkdtemp(prefix="stagestate_")) / "测试_Gemini路"
    unicode_root.mkdir(parents=True, exist_ok=True)
    webm_path = unicode_root / "手势道具_3s.webm"

    try:
        _make_synthetic_3s_webm(webm_path)
        assert webm_path.is_file() and webm_path.stat().st_size > 1000
        raw_bytes = webm_path.read_bytes()

        # 1. Targeted timestamp extraction (t=1.5s)
        res_ts = _extract_image_from_bytes(raw_bytes, timestamp_sec=1.5)
        assert res_ts is not None, "Expected non-None JPEG from 3s .webm at t=1.5s"
        jpg_bytes, mime = res_ts
        assert mime == "image/jpeg"
        with Image.open(io.BytesIO(jpg_bytes)) as im:
            rgb = np.array(im.convert("RGB"), dtype=np.uint8)
        mean_r, mean_g, mean_b = float(rgb[:, :, 0].mean()), float(rgb[:, :, 1].mean()), float(rgb[:, :, 2].mean())
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        sharpness_t15 = _frame_sharpness_score(bgr)

        # Verify NOT frame 0 (frame 0 is blue: B~180, R~20; t=1.5s is crimson-red: R>120 > B)
        assert mean_r > mean_b + 40.0, f"Extracted frame looks like frame-0 instead of t=1.5s! RGB=({mean_r:.1f},{mean_g:.1f},{mean_b:.1f})"
        assert sharpness_t15 > 120.0, f"Expected crisp non-blurred frame at t=1.5s, got Laplacian var={sharpness_t15:.1f}"

        # 2. Midpoint fallback extraction (timestamp_sec=None)
        res_mid = _extract_image_from_bytes(raw_bytes, timestamp_sec=None)
        assert res_mid is not None and res_mid[1] == "image/jpeg"
        with Image.open(io.BytesIO(res_mid[0])) as im_mid:
            mid_rgb = np.array(im_mid.convert("RGB"), dtype=np.uint8)
        assert float(mid_rgb[:, :, 0].mean()) > float(mid_rgb[:, :, 2].mean()) + 30.0

        # 3. Early timestamp check (t=0.2s must extract blue segment)
        res_early = _extract_image_from_bytes(raw_bytes, timestamp_sec=0.2)
        assert res_early is not None
        with Image.open(io.BytesIO(res_early[0])) as im_e:
            early_rgb = np.array(im_e.convert("RGB"), dtype=np.uint8)
        assert float(early_rgb[:, :, 2].mean()) > float(early_rgb[:, :, 0].mean()) + 40.0, "t=0.2s did not seek to blue segment"

        report = {
            "passed": True,
            "unicode_path_tested": str(webm_path),
            "webm_bytes": len(raw_bytes),
            "t15_jpeg_bytes": len(jpg_bytes),
            "t15_mean_rgb": [round(mean_r, 1), round(mean_g, 1), round(mean_b, 1)],
            "t15_laplacian_sharpness": round(sharpness_t15, 1),
            "midpoint_fallback_passed": True,
            "early_seek_passed": True,
        }
        print("W1-T6F WEBM PROP REGRESSION PASSED:", report)
        return report
    finally:
        shutil.rmtree(unicode_root.parent, ignore_errors=True)


if __name__ == "__main__":
    run_webm_prop_regression()
