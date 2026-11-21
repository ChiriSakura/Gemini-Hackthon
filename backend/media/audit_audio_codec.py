"""
W1-T5A: Full Cached MP4 Audio-Track, Frame-Count & H.264 Codec Audit (Owned by P2).

Verifies every cached scene MP4 across `branch_v1`, `branch_v2_cached`, and
`branch_v3_starlight_cached` for resolution (`640x360`), FPS (`24fps`),
duration (`~4.0s`), H.264 codec (`is_browser_h264`), and native audio stream
via `ffmpeg`, writing the report to `backend/assets/eval_audio_codec.json`.
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import imageio_ffmpeg

from backend import config
from backend.director.state_tree import get_branch
from backend.media.video_renderer import _open_video_capture_safe, is_browser_h264


def _probe_ffmpeg_streams(mp4_path: Path, ffmpeg_exe: str) -> dict:
    """Inspect video/audio streams of an MP4 via `ffmpeg -i` (Unicode-path safe)."""
    probe_target = mp4_path
    tmp_path: Path | None = None
    if not str(mp4_path).isascii():
        with tempfile.NamedTemporaryFile(suffix=mp4_path.suffix or ".mp4", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        shutil.copy2(mp4_path, tmp_path)
        probe_target = tmp_path

    try:
        proc = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-i", str(probe_target)],
            capture_output=True,
            text=True,
            check=False,
        )
        stderr = proc.stderr or ""
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    video_match = re.search(r"Stream #\d+:\d+.*?Video:\s*([^\r\n]+)", stderr)
    audio_match = re.search(r"Stream #\d+:\d+.*?Audio:\s*([^\r\n]+)", stderr)
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    container_duration_sec = None
    if dur_match:
        h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
        container_duration_sec = round(h * 3600 + m * 60 + s, 3)

    return {
        "video_stream": video_match.group(1).strip() if video_match else None,
        "has_audio_stream": audio_match is not None,
        "audio_stream": audio_match.group(1).strip() if audio_match else None,
        "container_duration_sec": container_duration_sec,
    }


def inspect_mp4_clip(mp4_path: Path, ffmpeg_exe: str) -> dict:
    """Measure resolution, fps, frame count, H.264 playback, and audio stream for one MP4."""
    cap, tmp_path = _open_video_capture_safe(mp4_path)
    try:
        opened = cap.isOpened()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if opened else 0
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if opened else 0
        fps = round(float(cap.get(cv2.CAP_PROP_FPS) or 0.0), 2) if opened else 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if opened else 0
    finally:
        cap.release()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    duration_sec = round(frame_count / fps, 3) if fps > 0 else 0.0
    h264_ok = is_browser_h264(mp4_path)
    streams = _probe_ffmpeg_streams(mp4_path, ffmpeg_exe)
    scene_clip_ok = (
        opened
        and h264_ok
        and width == 640
        and height == 360
        and abs(fps - 24.0) <= 0.5
        and 3.5 <= duration_sec <= 4.5
        and streams["has_audio_stream"]
    )
    return {
        "file": mp4_path.name,
        "bytes": mp4_path.stat().st_size,
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration_sec": duration_sec,
        "is_browser_h264": h264_ok,
        "has_audio_stream": streams["has_audio_stream"],
        "video_stream": streams["video_stream"],
        "audio_stream": streams["audio_stream"],
        "container_duration_sec": streams["container_duration_sec"],
        "scene_spec_passed": scene_clip_ok,
    }


def run_audio_codec_audit() -> dict:
    """Audit all cached branch MP4s across branch_v1, branch_v2_cached, and branch_v3_starlight_cached."""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    target_branches = tuple(
        bid
        for bid in (
            "branch_v1",
            "branch_v2_cached",
            "branch_v3_starlight_cached",
            "branch_v4_umbrella_shield_cached",
        )
        if get_branch(bid) is not None
    )

    clip_refs: dict[str, list[str]] = {}
    clip_paths: dict[str, Path] = {}
    for branch_id in target_branches:
        branch = get_branch(branch_id)
        if branch is None:
            raise RuntimeError(f"Missing cached branch: {branch_id}")
        for scene in branch.scenes:
            for cam_key, url in (
                ("cam_a", scene.media.cam_a_final_url),
                ("cam_b", scene.media.cam_b_final_url),
            ):
                if not url or not url.endswith(".mp4"):
                    continue
                local_path = config.ASSETS_DIR / url.removeprefix("/assets/")
                if not local_path.is_file():
                    raise FileNotFoundError(f"Missing MP4 {local_path} referenced by {branch_id} S{scene.scene_id}")
                clip_paths[url] = local_path
                clip_refs.setdefault(url, []).append(f"{branch_id}:S{scene.scene_id}:{cam_key}")

    clips: list[dict] = []
    for url in sorted(clip_paths):
        entry = inspect_mp4_clip(clip_paths[url], ffmpeg_exe)
        entry["url"] = url
        entry["referenced_by"] = clip_refs[url]
        clips.append(entry)

    demo_reel_path = config.FINALS_DIR / "demo_comparison_be_vs_he.mp4"
    demo_reel_audit = inspect_mp4_clip(demo_reel_path, ffmpeg_exe) if demo_reel_path.is_file() else None

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "branches_audited": list(target_branches),
        "unique_scene_mp4_count": len(clips),
        "all_h264": all(c["is_browser_h264"] for c in clips),
        "all_640x360": all(c["width"] == 640 and c["height"] == 360 for c in clips),
        "all_24fps": all(abs(c["fps"] - 24.0) <= 0.5 for c in clips),
        "all_4s_duration": all(3.5 <= c["duration_sec"] <= 4.5 for c in clips),
        "audio_stream_present_count": sum(1 for c in clips if c["has_audio_stream"]),
        "all_have_audio_stream": all(c["has_audio_stream"] for c in clips),
        "all_scene_specs_passed": all(c["scene_spec_passed"] for c in clips),
        "clips": clips,
        "demo_reel": demo_reel_audit,
    }
    out_path = config.ASSETS_DIR / "eval_audio_codec.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    summary = run_audio_codec_audit()
    compact = {k: v for k, v in summary.items() if k != "clips"}
    print(json.dumps(compact, ensure_ascii=False, indent=2))
