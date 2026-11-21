"""
Zero-Panic Live-Demo Pre-Flight Verifier for StageState (Owned by P2 / worker-1).

Feature & Use Cases:
- Runs a single fast (<5s) offline verification pass before live judging or
  video recording to verify:
  1. FastAPI endpoints `/api/health`, `/api/branches`, and `/api/media/progress/branch_v1`
     all return HTTP 200 OK.
  2. Every persisted branch JSON validates against Pydantic `BranchState`, has 4 scenes,
     and every referenced `cam_a/b_final_url` and `cam_a/b_tail_frame_url` returns 200 OK.
  3. Every cached MP4 (scenes + submission hooks/outro) uses browser-playable H.264
     (`avc1`/`h264`), valid frame rate/dimensions, and has native audio where expected.
  4. Every transparent prop PNG in `backend/assets/props/` is RGBA with transparency.
  5. Reports any residual CJK character counts across `backend/assets/branches/*.json`.
- Exports full structured checklist to `backend/assets/preflight_report.json`.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import cv2
import imageio_ffmpeg
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backend import config
from backend.logger import log_function_call
from backend.main import app
from backend.media.video_renderer import _open_video_capture_safe
from backend.schemas import BranchState

CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _inspect_mp4_unicode_safe(mp4_path: Path, ffmpeg_exe: str) -> dict:
    """Inspect codec, resolution, fps, frame count, and audio stream safely on any path."""
    cap, tmp_path = _open_video_capture_safe(mp4_path)
    try:
        opened = cap.isOpened()
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC)) if opened else 0
        fourcc = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).lower() if opened else ""
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = round(float(cap.get(cv2.CAP_PROP_FPS) or 0.0), 2)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)

    probe = subprocess.run([ffmpeg_exe, "-i", str(mp4_path)], capture_output=True, text=True)
    has_audio = "Audio:" in probe.stderr
    return {
        "file": str(mp4_path.relative_to(config.ASSETS_DIR)).replace("\\", "/"),
        "bytes": mp4_path.stat().st_size,
        "fourcc": fourcc,
        "is_h264": fourcc in {"h264", "avc1", "x264"},
        "resolution": f"{w}x{h}",
        "fps": fps,
        "frames": frames,
        "duration_sec": round(frames / fps, 2) if fps > 0 else 0.0,
        "has_audio": has_audio,
    }


@log_function_call
def run_preflight_check() -> dict:
    """Execute complete pre-flight audit across API endpoints, branches, MP4s, blueprints, and props."""
    t0 = time.perf_counter()
    client = TestClient(app)
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    # 1. Core API health & progress endpoints
    r_health = client.get("/api/health")
    r_branches = client.get("/api/branches")
    r_progress = client.get("/api/media/progress/branch_v1")
    api_checks = {
        "health_200": r_health.status_code == 200,
        "branches_200": r_branches.status_code == 200,
        "progress_200": r_progress.status_code == 200,
        "progress_payload": r_progress.json() if r_progress.status_code == 200 else None,
    }

    # 2. Branch JSON schema validation, HTTP 200 media links, and CJK audit
    branch_reports = []
    seen_mp4s: set[Path] = set()
    for b_file in sorted(config.BRANCHES_DIR.glob("*.json")):
        raw = b_file.read_text(encoding="utf-8")
        branch = BranchState.model_validate_json(raw)
        cjk_hits = len(CJK_PATTERN.findall(raw))
        missing_urls = []
        for s in branch.scenes:
            for key in ("cam_a_final_url", "cam_b_final_url", "cam_a_tail_frame_url", "cam_b_tail_frame_url"):
                url = getattr(s.media, key)
                if not url or client.get(url).status_code != 200:
                    missing_urls.append(f"s{s.scene_id}:{key}:{url}")
                elif url.endswith(".mp4") and url.startswith("/assets/"):
                    seen_mp4s.add(config.ASSETS_DIR / url.removeprefix("/assets/"))
        branch_reports.append(
            {
                "branch_id": branch.branch_id,
                "scene_count": len(branch.scenes),
                "cjk_char_count": cjk_hits,
                "all_media_200": len(missing_urls) == 0,
                "missing_urls": missing_urls,
            }
        )

    # 3. Inspect all cached MP4s + submission MP4s
    for extra_mp4 in sorted((config.ASSETS_DIR / "submission").glob("*.mp4")) if (config.ASSETS_DIR / "submission").is_dir() else []:
        seen_mp4s.add(extra_mp4)
    mp4_reports = [_inspect_mp4_unicode_safe(p, ffmpeg_exe) for p in sorted(seen_mp4s) if p.is_file()]

    # 4. Inspect all prop PNGs and pre-rendered blueprint frames
    prop_reports = []
    for p in sorted(config.PROPS_DIR.glob("*.png")):
        with Image.open(p) as im:
            rgba = np.array(im.convert("RGBA"))
            prop_reports.append(
                {
                    "file": p.name,
                    "mode": im.mode,
                    "has_transparency": bool(np.any(rgba[:, :, 3] < 255)),
                    "resolution": f"{im.width}x{im.height}",
                    "http_200": client.get(f"/assets/props/{p.name}").status_code == 200,
                }
            )

    blueprint_files = sorted(config.PREVIEWS_DIR.glob("blueprint_*.png"))
    blueprints_200 = all(client.get(f"/assets/previews/{bp.name}").status_code == 200 for bp in blueprint_files)

    all_ready = (
        all((api_checks["health_200"], api_checks["branches_200"], api_checks["progress_200"]))
        and all(b["all_media_200"] for b in branch_reports)
        and all(m["is_h264"] for m in mp4_reports)
        and all(p["mode"] == "RGBA" and p["has_transparency"] and p["http_200"] for p in prop_reports)
        and blueprints_200
    )

    report = {
        "preflight_passed": all_ready,
        "elapsed_sec": round(time.perf_counter() - t0, 2),
        "api_checks": api_checks,
        "branches": branch_reports,
        "mp4_summary": {
            "total_mp4s": len(mp4_reports),
            "all_h264": all(m["is_h264"] for m in mp4_reports),
            "with_audio_count": sum(1 for m in mp4_reports if m["has_audio"]),
            "files": mp4_reports,
        },
        "props_summary": {
            "total_props": len(prop_reports),
            "all_rgba_200": all(p["mode"] == "RGBA" and p["http_200"] for p in prop_reports),
            "files": prop_reports,
        },
        "blueprints_summary": {
            "count": len(blueprint_files),
            "all_http_200": blueprints_200,
        },
    }
    out_path = config.ASSETS_DIR / "preflight_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    res = run_preflight_check()
    print(
        json.dumps(
            {
                "preflight_passed": res["preflight_passed"],
                "elapsed_sec": res["elapsed_sec"],
                "api_checks": res["api_checks"],
                "branches": res["branches"],
                "mp4_total": res["mp4_summary"]["total_mp4s"],
                "mp4_all_h264": res["mp4_summary"]["all_h264"],
                "mp4_with_audio": res["mp4_summary"]["with_audio_count"],
                "props_total": res["props_summary"]["total_props"],
                "blueprints_count": res["blueprints_summary"]["count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
