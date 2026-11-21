"""
Side-by-Side BE vs HE Demo Comparison Video Builder (Owned by P2 / worker-1, W1-T4B).

Feature & Use Cases:
- Stitches cached Scene 1..4 H.264 MP4s from `branch_v1` (BE) on the left and
  `branch_v2_cached` (HE) on the right into a single Chrome-playable H.264 + AAC
  comparison video at `backend/assets/finals/demo_comparison_be_vs_he.mp4`.
- Uses Unicode-safe ASCII temp-dir copies for OpenCV VideoCapture and `imageio_ffmpeg`
  libx264 (`yuv420p`, `+faststart`) encoding, adding clear English scene badges
  (`Scene 1 Shared`, `Branch 1 BE`, `Branch 2 HE`) and per-scene QA scores.
"""

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from backend import config
from backend.director.state_tree import get_branch
from backend.logger import log_function_call
from backend.media.video_renderer import _open_video_capture_safe, is_browser_h264

PANEL_W = 640
PANEL_H = 360
HEADER_H = 80
OUT_W = PANEL_W * 2
OUT_H = HEADER_H + PANEL_H
FPS = 24
FRAMES_PER_SCENE = FPS * 4

SCENE_SUBTITLES = {
    1: ("Rainy Encounter (Reused Prefix - 0 Rerender)", "Rainy Encounter (Reused Prefix - 0 Rerender)"),
    2: ("Thunder Startles Nyx · Vase Shatters", "Star Token Soothes Nyx · Vase Saved"),
    3: ("Broken Shards Persist · Nyx Hides", "Clean Floor · Nyx Guards Star Token"),
    4: ("Cold Rain Alone · Broken Vase (BE)", "Warm Starlight Snow · Shared Umbrella (HE)"),
}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a crisp TrueType font on Windows/Linux with safe PIL fallback."""
    candidates = (
        [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for font_path in candidates:
        if Path(font_path).is_file():
            try:
                return ImageFont.truetype(font_path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_header_bgr(scene_id: int, qa_be: float, qa_he: float) -> np.ndarray:
    """Render an 80px-tall dark banner with `Scene 1 Shared`, `Branch 1 BE`, and `Branch 2 HE` pills."""
    img = Image.new("RGB", (OUT_W, HEADER_H), (11, 17, 32))
    draw = ImageDraw.Draw(img)

    title_font = _load_font(17, bold=True)
    badge_font = _load_font(16, bold=True)
    sub_font = _load_font(15, bold=False)

    # Top bar caption
    draw.rectangle([(0, 0), (OUT_W, 28)], fill=(17, 25, 45))
    header_text = "StageState  |  Past-Rewrite Causal Branch Comparison  (Gemini 4 Omni Flash + Decisions API)"
    draw.text((20, 4), header_text, fill=(203, 213, 225), font=title_font)

    be_sub, he_sub = SCENE_SUBTITLES.get(scene_id, (f"Scene {scene_id} BE", f"Scene {scene_id} HE"))

    if scene_id == 1:
        # Shared prefix highlight (teal)
        for x0, qa_val in ((14, qa_be), (PANEL_W + 14, qa_he)):
            draw.rounded_rectangle([(x0, 36), (x0 + 146, 68)], radius=6, fill=(13, 148, 136))
            draw.text((x0 + 12, 42), "Scene 1 Shared", fill=(255, 255, 255), font=badge_font)
            draw.text((x0 + 158, 43), be_sub, fill=(226, 232, 240), font=sub_font)
        draw.line([(PANEL_W, 28), (PANEL_W, HEADER_H)], fill=(56, 189, 248), width=3)
    else:
        # Left: Branch 1 BE (crimson)
        draw.rounded_rectangle([(14, 36), (146, 68)], radius=6, fill=(190, 18, 60))
        draw.text((26, 42), "Branch 1 BE", fill=(255, 255, 255), font=badge_font)
        draw.text((158, 43), f"S{scene_id}: {be_sub}", fill=(254, 205, 211), font=sub_font)

        # Right: Branch 2 HE (amber gold)
        rx0 = PANEL_W + 14
        draw.rounded_rectangle([(rx0, 36), (rx0 + 132, 68)], radius=6, fill=(217, 119, 6))
        draw.text((rx0 + 12, 42), "Branch 2 HE", fill=(255, 255, 255), font=badge_font)
        draw.text((rx0 + 144, 43), f"S{scene_id}: {he_sub}", fill=(254, 243, 199), font=sub_font)

        draw.line([(PANEL_W, 28), (PANEL_W, HEADER_H)], fill=(245, 158, 11), width=3)

    # Subtle QA indicators at right edge of the second row (zero overlap with title bar)
    draw.text((PANEL_W - 76, 43), f"QA {qa_be:.2f}", fill=(148, 163, 184), font=sub_font)
    draw.text((OUT_W - 76, 43), f"QA {qa_he:.2f}", fill=(253, 224, 71), font=sub_font)

    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _read_normalized_frames(video_path: Path, target_count: int = FRAMES_PER_SCENE) -> list[np.ndarray]:
    """Read and resize all frames of `video_path` safely (Unicode-safe) to `(PANEL_W, PANEL_H)`."""
    cap, tmp_path = _open_video_capture_safe(video_path)
    frames: list[np.ndarray] = []
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video clip: {video_path}")
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if frame.shape[1] != PANEL_W or frame.shape[0] != PANEL_H:
                frame = cv2.resize(frame, (PANEL_W, PANEL_H), interpolation=cv2.INTER_AREA)
            frames.append(frame)
    finally:
        cap.release()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)

    if not frames:
        raise RuntimeError(f"No frames decoded from {video_path}")

    # Trim or hold final frame to hit exact `target_count` for clean A/V sync.
    if len(frames) >= target_count:
        return frames[:target_count]
    return frames + [frames[-1]] * (target_count - len(frames))


def _resolve_local_mp4(url: Optional[str]) -> Path:
    """Resolve `/assets/finals/...` URL to a local Path and verify existence."""
    if not url or not url.startswith("/assets/"):
        raise FileNotFoundError(f"Invalid asset URL: {url}")
    path = config.ASSETS_DIR / url.removeprefix("/assets/")
    if not path.is_file():
        raise FileNotFoundError(f"Cached video missing: {path}")
    return path


@log_function_call
def build_demo_comparison_video(
    output_path: Optional[Path] = None,
    cam_id: str = "cam_a",
) -> dict:
    """
    Build `backend/assets/finals/demo_comparison_be_vs_he.mp4` stitching Scenes 1..4 of
    `branch_v1` (BE) vs `branch_v2_cached` (HE) side-by-side with labels and H.264 encoding.
    """
    t0 = time.perf_counter()
    b1 = get_branch("branch_v1")
    b2 = get_branch("branch_v2_cached")
    if not b1 or not b2:
        raise RuntimeError("Both `branch_v1` and `branch_v2_cached` must exist to build demo reel.")

    out_path = output_path or (config.FINALS_DIR / "demo_comparison_be_vs_he.mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    scenes_b1 = {s.scene_id: s for s in b1.scenes}
    scenes_b2 = {s.scene_id: s for s in b2.scenes}
    url_field = f"{cam_id}_final_url"

    with tempfile.TemporaryDirectory(prefix="stagestate_reel_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        raw_video_path = tmp_dir / "side_by_side_video.mp4"
        final_tmp_path = tmp_dir / "demo_comparison_be_vs_he.mp4"

        # 1. Stream composited BGR frames into ffmpeg libx264 H.264 encoder
        enc_cmd = [
            ffmpeg_exe,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{OUT_W}x{OUT_H}",
            "-r", str(FPS),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(raw_video_path),
        ]
        proc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin is not None

        audio_sources: list[Path] = []
        total_frames = 0
        for sid in (1, 2, 3, 4):
            s1 = scenes_b1[sid]
            s2 = scenes_b2[sid]
            p1 = _resolve_local_mp4(getattr(s1.media, url_field))
            p2 = _resolve_local_mp4(getattr(s2.media, url_field))

            # Copy Branch 2 clip to ASCII temp path for clean audio concat
            audio_copy = tmp_dir / f"he_s{sid}.mp4"
            shutil.copy2(p2, audio_copy)
            audio_sources.append(audio_copy)

            frames_left = _read_normalized_frames(p1, FRAMES_PER_SCENE)
            frames_right = _read_normalized_frames(p2, FRAMES_PER_SCENE)
            header = _render_header_bgr(sid, s1.media.qa_score or 0.90, s2.media.qa_score or 0.90)
            divider_color = (248, 189, 56) if sid == 1 else (11, 158, 245)  # BGR teal vs amber

            for f_l, f_r in zip(frames_left, frames_right):
                canvas = np.empty((OUT_H, OUT_W, 3), dtype=np.uint8)
                canvas[:HEADER_H, :, :] = header
                canvas[HEADER_H:, :PANEL_W, :] = f_l
                canvas[HEADER_H:, PANEL_W:, :] = f_r
                canvas[HEADER_H:, PANEL_W - 2 : PANEL_W + 2, :] = divider_color
                proc.stdin.write(canvas.tobytes())
                total_frames += 1

        proc.stdin.close()
        stderr_out = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
        ret = proc.wait()
        if ret != 0 or not raw_video_path.is_file():
            raise RuntimeError(f"ffmpeg H.264 encode failed (code={ret}): {stderr_out[-600:]}")

        # 2. Mux concatenated Branch 2 HE audio trimmed cleanly to 4.0s per scene
        mux_cmd = [ffmpeg_exe, "-y", "-i", str(raw_video_path)]
        for a_src in audio_sources:
            mux_cmd.extend(["-i", str(a_src)])
        filter_parts = [f"[{idx}:a]atrim=0:4.0,asetpts=PTS-STARTPTS[a{idx}]" for idx in range(1, len(audio_sources) + 1)]
        concat_inputs = "".join(f"[a{idx}]" for idx in range(1, len(audio_sources) + 1))
        filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(audio_sources)}:v=0:a=1[aout]"
        mux_cmd.extend(
            [
                "-filter_complex", filter_complex,
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                str(final_tmp_path),
            ]
        )
        mux_res = subprocess.run(mux_cmd, capture_output=True, text=True)
        if mux_res.returncode == 0 and final_tmp_path.is_file():
            shutil.copy2(final_tmp_path, out_path)
            has_audio = True
        else:
            shutil.copy2(raw_video_path, out_path)
            has_audio = False

    h264_ok = is_browser_h264(out_path)
    elapsed = round(time.perf_counter() - t0, 2)
    return {
        "output_path": str(out_path),
        "asset_url": f"/assets/finals/{out_path.name}",
        "resolution": f"{OUT_W}x{OUT_H}",
        "fps": FPS,
        "total_frames": total_frames,
        "duration_sec": round(total_frames / FPS, 2),
        "file_size_bytes": out_path.stat().st_size,
        "file_size_mb": round(out_path.stat().st_size / (1024 * 1024), 2),
        "is_h264": h264_ok,
        "has_audio": has_audio,
        "elapsed_sec": elapsed,
    }


if __name__ == "__main__":
    import json

    summary = build_demo_comparison_video()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
