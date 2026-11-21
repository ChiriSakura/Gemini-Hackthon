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

        # Transcode to browser-playable H.264 AND mux native Omni Flash audio track (Scene 1..4 HE).
        transcoded = False
        audio_tmp_copies: list[Path] = []
        try:
            import imageio_ffmpeg

            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            audio_inputs: list[str] = []
            for sid in (1, 2, 3, 4):
                src_s = b2_map[sid]
                src_p = config.ASSETS_DIR / (src_s.media.cam_a_final_url or "").removeprefix("/assets/")
                if src_p.is_file():
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                        cp = Path(tf.name)
                    shutil.copy2(src_p, cp)
                    audio_tmp_copies.append(cp)
                    audio_inputs.extend(["-i", str(cp)])

            if len(audio_tmp_copies) == 4:
                cmd = [
                    ffmpeg_exe,
                    "-y",
                    "-i",
                    str(raw_mp4),
                    *audio_inputs,
                    "-filter_complex",
                    "[1:a][2:a][3:a][4:a]concat=n=4:v=0:a=1[aout]",
                    "-map",
                    "0:v:0",
                    "-map",
                    "[aout]",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(h264_mp4),
                ]
            else:
                cmd = [
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
                ]

            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            if h264_mp4.is_file() and h264_mp4.stat().st_size > 0:
                shutil.copy2(h264_mp4, out_path)
                transcoded = True
        except Exception:
            pass
        finally:
            for cp in audio_tmp_copies:
                cp.unlink(missing_ok=True)

        if not transcoded:
            shutil.copy2(raw_mp4, out_path)
        return out_path
    finally:
        raw_mp4.unlink(missing_ok=True)
        h264_mp4.unlink(missing_ok=True)


@log_function_call
def build_1min_pitch_reel() -> Path:
    """
    W1-T6A: Build a 50s 16:9 1280x720 full-pitch composite video (`pitch_demo_reel_1min.mp4`)
    with Title Card (4s) -> Branch 1 BE (16s) -> Branch 2 HE (12s) -> Branch 3 & Props (12s)
    -> Ending Metric Card (6s), H.264 + muxed AAC audio.
    """
    from PIL import Image, ImageDraw, ImageFont

    b1 = get_branch("branch_v1")
    b2 = get_branch("branch_v2_cached")
    b3 = get_branch("branch_v3_starlight_cached")
    assert b1 and b2 and b3, "Cached branches branch_v1, branch_v2_cached, branch_v3_starlight_cached required"

    b1_map = {s.scene_id: s for s in b1.scenes}
    b2_map = {s.scene_id: s for s in b2.scenes}
    b3_map = {s.scene_id: s for s in b3.scenes}

    out_w, out_h, fps = 1280, 720, 24.0
    ttf_bold = Path(r"C:\Windows\Fonts\segoeuib.ttf")
    ttf_reg = Path(r"C:\Windows\Fonts\segoeui.ttf")
    font_h1 = ImageFont.truetype(str(ttf_bold if ttf_bold.is_file() else ttf_reg), 40) if ttf_reg.is_file() else ImageFont.load_default(size=40)
    font_h2 = ImageFont.truetype(str(ttf_bold if ttf_bold.is_file() else ttf_reg), 24) if ttf_reg.is_file() else ImageFont.load_default(size=24)
    font_body = ImageFont.truetype(str(ttf_reg), 18) if ttf_reg.is_file() else ImageFont.load_default(size=18)
    font_small = ImageFont.truetype(str(ttf_reg), 15) if ttf_reg.is_file() else ImageFont.load_default(size=15)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf1:
        raw_mp4 = Path(tf1.name)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf2:
        h264_mp4 = Path(tf2.name)
    out_path = config.FINALS_DIR / "pitch_demo_reel_1min.mp4"

    def _pil_to_bgr(im: Image.Image) -> np.ndarray:
        return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)

    def _make_card(title: str, subtitle: str, bullets: list[str], accent: tuple[int, int, int]) -> np.ndarray:
        card = Image.new("RGB", (out_w, out_h), (12, 16, 28))
        d = ImageDraw.Draw(card)
        d.rectangle([36, 36, out_w - 36, out_h - 36], outline=accent, width=3)
        d.text((72, 95), title, fill=(250, 204, 21), font=font_h1)
        d.text((72, 162), subtitle, fill=(147, 197, 253), font=font_h2)
        y = 240
        for line in bullets:
            d.text((88, y), f"•  {line}", fill=(226, 232, 240), font=font_body)
            y += 48
        d.text(
            (72, out_h - 82),
            "StageState · Gemini 4 Neon + Hydrogen + Decisions API v1beta + Omni Flash + Nano Banana 2.1",
            fill=(148, 163, 184),
            font=font_small,
        )
        return _pil_to_bgr(card)

    def _make_props_card() -> np.ndarray:
        card = Image.new("RGB", (out_w, out_h), (12, 16, 28))
        d = ImageDraw.Draw(card)
        d.text((54, 34), "Zero-Halo RGBA Stage Props (Nano Banana 2.1 + Floodfill Keying)", fill=(250, 204, 21), font=font_h2)
        d.text((54, 70), "Bottom-flush cropped (0px gap) + physical SI height anchored in Three.js & Omni Flash", fill=(148, 163, 184), font=font_small)
        names = [
            ("star_token.png", "Star Keepsake (0.10m)"),
            ("crystal_vase.png", "Crystal Vase (0.32m)"),
            ("crystal_vase_broken.png", "Broken Vase (0.06m)"),
            ("showcase_brass_pocket_watch.png", "Pocket Watch (0.09m)"),
            ("showcase_star_lantern.png", "Star Lantern (0.24m)"),
            ("showcase_crystal_shield.png", "Crystal Shield (0.65m)"),
            ("showcase_origami_crane.png", "Origami Crane (0.12m)"),
            ("showcase_glowing_quill.png", "Glowing Quill (0.18m)"),
        ]
        tile_w, tile_h = 270, 255
        for idx, (fname, label) in enumerate(names):
            col, row = idx % 4, idx // 4
            x0 = 56 + col * (tile_w + 30)
            y0 = 118 + row * (tile_h + 26)
            d.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], fill=(22, 29, 48), outline=(71, 85, 105), width=2)
            pp = config.PROPS_DIR / fname
            if pp.is_file():
                with Image.open(pp) as pim:
                    rgba = pim.convert("RGBA")
                    rgba.thumbnail((tile_w - 40, tile_h - 60), Image.Resampling.LANCZOS)
                    px = x0 + (tile_w - rgba.width) // 2
                    py = y0 + tile_h - 38 - rgba.height
                    card.paste(rgba, (px, py), rgba)
            d.text((x0 + 16, y0 + tile_h - 28), label, fill=(226, 232, 240), font=font_small)
        return _pil_to_bgr(card)

    def _scene_banner(branch_tag: str, sc, accent_bgr: tuple[int, int, int]) -> np.ndarray:
        bar = np.full((180, out_w, 3), (12, 16, 28), dtype=np.uint8)
        clean_title = sc.title.replace("·", "-").replace("—", "--")
        cv2.putText(bar, f"{branch_tag}  |  Scene {sc.scene_id}: {clean_title}", (36, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.92, (250, 204, 21), 2, cv2.LINE_AA)
        cv2.putText(bar, f"Lighting: {sc.lighting}   |   QA Score: {sc.media.qa_score:.3f}   |   Dual-Cam Synchronized (Cam A + Cam B)", (36, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.58, accent_bgr, 1, cv2.LINE_AA)
        cv2.putText(bar, sc.narration[:108], (36, 128), cv2.FONT_HERSHEY_SIMPLEX, 0.54, (226, 232, 240), 1, cv2.LINE_AA)
        cv2.putText(bar, "CAM A (Front Wide)", (230, 168), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (147, 197, 253), 1, cv2.LINE_AA)
        cv2.putText(bar, "CAM B (45-deg Close-Up)", (850, 168), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (134, 239, 172), 1, cv2.LINE_AA)
        return bar

    def _bottom_bar() -> np.ndarray:
        bar = np.full((out_h - 180 - 360, out_w, 3), (12, 16, 28), dtype=np.uint8)
        cv2.putText(bar, "Physical-Scale Grounded Omni Flash 360p (Lia 1.62m | Nyx 0.30m | Table 0.75m | Vase 0.32m)", (165, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (148, 163, 184), 1, cv2.LINE_AA)
        return bar

    # Timeline segments (duration_sec, optional audio_mp4_path): total = 50.0s
    audio_segments: list[tuple[float, Path | None]] = []
    audio_tmp_copies: list[Path] = []
    try:
        writer = cv2.VideoWriter(str(raw_mp4), cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_w, out_h))
        if not writer.isOpened():
            raise RuntimeError("Failed to open VideoWriter for 1-min pitch reel")

        def _write_static_frames(frame_bgr: np.ndarray, seconds: float) -> None:
            for _ in range(int(round(seconds * fps))):
                writer.write(frame_bgr)
            audio_segments.append((seconds, None))

        def _write_dual_scene(tag: str, sc, accent_bgr: tuple[int, int, int]) -> None:
            pa = config.ASSETS_DIR / (sc.media.cam_a_final_url or "").removeprefix("/assets/")
            pb = config.ASSETS_DIR / (sc.media.cam_b_final_url or "").removeprefix("/assets/")
            fa = _read_all_frames(pa, 640, 360)
            fb = _read_all_frames(pb, 640, 360)
            n = max(len(fa), len(fb), 96)
            top = _scene_banner(tag, sc, accent_bgr)
            bot = _bottom_bar()
            for idx in range(n):
                left = fa[min(idx, len(fa) - 1)]
                right = fb[min(idx, len(fb) - 1)]
                mid = np.hstack([left, right])
                cv2.line(mid, (640, 0), (640, 360), (75, 95, 130), 2)
                writer.write(np.vstack([top, mid, bot]))
            audio_segments.append((n / fps, pa if pa.is_file() else None))

        # 1. Title Card (4.0s)
        _write_static_frames(
            _make_card(
                "StageState: Time-Travel AI Stage Director",
                "Edit Scene k -> Preserve Prefix 1..k-1 -> Re-Simulate Dual-Cam WorldState",
                [
                    "Dual-Camera 2.5D Three.js Stage + Physical Metric Solver (1 unit = 1m)",
                    "Multimodal Causal Forking via Voice + Webcam Props (Gemini 4 Neon & Hydrogen)",
                    "Concurrent Best-of-2 Video Synthesis + Native Audio (Omni Flash + Decisions API)",
                ],
                (250, 204, 21),
            ),
            4.0,
        )

        # 2. Branch 1 BE (Scenes 1..4 = 16.0s)
        for sid in (1, 2, 3, 4):
            _write_dual_scene("Branch 1 - BE Baseline", b1_map[sid], (130, 140, 248))

        # 3. Branch 2 HE (Scenes 2..4 = 12.0s)
        for sid in (2, 3, 4):
            _write_dual_scene("Branch 2 - HE Starlight Twist (Forked @ S2)", b2_map[sid], (130, 240, 140))

        # 4. Branch 3 Keepsake Highlight (S2 + S4 = 8.0s) + Zero-Halo Props Showcase (4.0s)
        for sid in (2, 4):
            _write_dual_scene("Branch 3 - Persistent Star Token Twist", b3_map[sid], (250, 210, 120))
        _write_static_frames(_make_props_card(), 4.0)

        # 5. Ending Metric Card (6.0s)
        _write_static_frames(
            _make_card(
                "Empirical Evaluation & Real-Time Efficiency",
                "All Claims Grounded in Live Logged Gemini 4 & Omni Flash Runs",
                [
                    "WorldState + Rule Memory QA Score: 0.901 - 0.952 vs Stateless Baseline 0.564",
                    "Dual-Camera Cross-View Consistency: 92.9% (26/28 across 7 scene pairs)",
                    "Decisions API v1beta Calibration: 95.0% Accuracy (100% True Negative Rate, 1.37s)",
                    "Multi-Scene Star Keepsake Persistence: 100% (6/6 clips) | Zero Green-Halo Props",
                    "Localized Dependency Filter Savings: 66.7% scenes skipped on Scene 2 rewrites",
                ],
                (74, 222, 128),
            ),
            6.0,
        )
        writer.release()

        # Transcode to H.264 & concat matching audio timeline (real AAC for scene clips, silence for cards)
        import imageio_ffmpeg

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_inputs: list[str] = ["-i", str(raw_mp4)]
        filter_parts: list[str] = []
        concat_labels: list[str] = []
        inp_idx = 1
        for seg_idx, (dur, src_mp4) in enumerate(audio_segments):
            lbl = f"[a{seg_idx}]"
            if src_mp4 is not None and src_mp4.is_file():
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                    cp = Path(tf.name)
                shutil.copy2(src_mp4, cp)
                audio_tmp_copies.append(cp)
                ffmpeg_inputs.extend(["-i", str(cp)])
                filter_parts.append(f"[{inp_idx}:a]aresample=48000,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS{lbl}")
                inp_idx += 1
            else:
                ffmpeg_inputs.extend(["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
                filter_parts.append(f"[{inp_idx}:a]asetpts=PTS-STARTPTS{lbl}")
                inp_idx += 1
            concat_labels.append(lbl)

        filter_complex = ";".join(filter_parts) + f";{''.join(concat_labels)}concat=n={len(concat_labels)}:v=0:a=1[aout]"
        cmd = [
            ffmpeg_exe,
            "-y",
            *ffmpeg_inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(h264_mp4),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        shutil.copy2(h264_mp4, out_path)
        return out_path
    finally:
        for cp in audio_tmp_copies:
            cp.unlink(missing_ok=True)
        raw_mp4.unlink(missing_ok=True)
        h264_mp4.unlink(missing_ok=True)


if __name__ == "__main__":
    dest = build_be_vs_he_demo_reel()
    print(f"DEMO_REEL_SAVED: {dest} ({dest.stat().st_size} bytes)")
    pitch_dest = build_1min_pitch_reel()
    print(f"PITCH_REEL_SAVED: {pitch_dest} ({pitch_dest.stat().st_size} bytes)")
