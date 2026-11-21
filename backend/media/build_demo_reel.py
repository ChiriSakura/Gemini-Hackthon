"""
Side-by-Side BE vs. HE Demo Comparison Reel Builder (Owned by P2 / worker-1).

Feature & Use Cases:
- W1-T4B: Stitches cached Scene 1..4 H.264 clips from `branch_v1` (BE) on the left
  and `branch_v2_cached` (HE) on the right into a single 16s side-by-side showcase
  video (`backend/assets/finals/demo_comparison_be_vs_he.mp4`), using an ASCII temp
  directory so OpenCV/ffmpeg work reliably with Unicode Windows paths.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from backend import config
from backend.director.state_tree import get_branch
from backend.logger import log_function_call


def _draw_header(width: int, header_h: int, top_title: str, left_label: str, right_label: str) -> np.ndarray:
    """Render a crisp dark comparison banner above the two side-by-side video panels."""
    bar = np.full((header_h, width, 3), (18, 22, 36), dtype=np.uint8)
    cv2.line(bar, (width // 2, 0), (width // 2, header_h), (65, 80, 110), 2)
    cv2.putText(bar, top_title, (20, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (250, 204, 21), 2, cv2.LINE_AA)
    cv2.putText(bar, left_label, (20, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (147, 197, 253), 1, cv2.LINE_AA)
    cv2.putText(bar, right_label, (width // 2 + 20, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (134, 239, 172), 1, cv2.LINE_AA)
    return bar


def _read_all_frames(mp4_path: Path, target_w: int, target_h: int) -> list[np.ndarray]:
    """Read and resize all frames from an MP4 file safely via an ASCII temp copy."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(mp4_path, tmp_path)
        cap = cv2.VideoCapture(str(tmp_path))
        frames: list[np.ndarray] = []
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if frame.shape[1] != target_w or frame.shape[0] != target_h:
                frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            frames.append(frame)
        cap.release()
        return frames
    finally:
        tmp_path.unlink(missing_ok=True)


@log_function_call
def build_be_vs_he_demo_reel() -> Path:
    """Stitch `branch_v1` vs `branch_v2_cached` Scenes 1..4 into `demo_comparison_be_vs_he.mp4`."""
    b1 = get_branch("branch_v1")
    b2 = get_branch("branch_v2_cached")
    assert b1 is not None and b2 is not None, "Cached branch_v1 and branch_v2_cached are required."

    b1_map = {s.scene_id: s for s in b1.scenes}
    b2_map = {s.scene_id: s for s in b2.scenes}

    panel_w, panel_h = 640, 360
    header_h = 60
    out_w, out_h = panel_w * 2, panel_h + header_h
    fps = 24.0

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_raw:
        raw_mp4 = Path(tmp_raw.name)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_h264:
        h264_mp4 = Path(tmp_h264.name)

    out_path = config.FINALS_DIR / "demo_comparison_be_vs_he.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        writer = cv2.VideoWriter(str(raw_mp4), cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_w, out_h))
        if not writer.isOpened():
            raise RuntimeError("Failed to open OpenCV VideoWriter for demo reel")

        for sid in (1, 2, 3, 4):
            s1 = b1_map[sid]
            s2 = b2_map[sid]
            p1 = config.ASSETS_DIR / (s1.media.cam_a_final_url or "").removeprefix("/assets/")
            p2 = config.ASSETS_DIR / (s2.media.cam_a_final_url or "").removeprefix("/assets/")
            frames_1 = _read_all_frames(p1, panel_w, panel_h)
            frames_2 = _read_all_frames(p2, panel_w, panel_h)
            n_frames = max(len(frames_1), len(frames_2))
            if n_frames == 0:
                continue

            if sid == 1:
                top_title = f"Scene {sid} Shared Prefix (0s Rerender — Reused Verbatim)"
                left_lbl = f"Branch 1 (BE): {s1.title}"
                right_lbl = f"Branch 2 (HE): {s2.title}"
            else:
                top_title = f"Scene {sid} Causal Divergence (Forked at Scene 2)"
                left_lbl = f"Branch 1 (BE): {s1.title}"
                right_lbl = f"Branch 2 (HE): {s2.title}"

            banner = _draw_header(out_w, header_h, top_title, left_lbl, right_lbl)
            for i in range(n_frames):
                f1 = frames_1[min(i, len(frames_1) - 1)]
                f2 = frames_2[min(i, len(frames_2) - 1)]
                side_by_side = np.hstack([f1, f2])
                cv2.line(side_by_side, (panel_w, 0), (panel_w, panel_h), (65, 80, 110), 2)
                composite = np.vstack([banner, side_by_side])
                writer.write(composite)

        writer.release()

        # Transcode to browser-playable H.264 if imageio-ffmpeg is available.
        transcoded = False
        try:
            import imageio_ffmpeg

            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            proc = subprocess.run(
                [
                    ffmpeg_exe,
                    "-y",
                    "-i",
                    str(raw_mp4),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(h264_mp4),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            if h264_mp4.is_file() and h264_mp4.stat().st_size > 0:
                shutil.copy2(h264_mp4, out_path)
                transcoded = True
        except Exception:
            pass

        if not transcoded:
            shutil.copy2(raw_mp4, out_path)
        return out_path
    finally:
        raw_mp4.unlink(missing_ok=True)
        h264_mp4.unlink(missing_ok=True)


if __name__ == "__main__":
    dest = build_be_vs_he_demo_reel()
    print(f"DEMO_REEL_SAVED: {dest} ({dest.stat().st_size} bytes)")
