"""
Concurrent Dual-Take Omni Flash Video Renderer & QA Loop (Owned by P2 / worker-1).

Feature & Use Cases:
- Renders `needs_rerender=True` scenes concurrently across Camera A (front wide)
  and Camera B (45-degree side close-up) using `asyncio.gather` (`360p`, `4s` per
  clip for hackathon live-demo speed).
- Anchors each scene on its own 2.5D Three.js preview clip/frame + locked character
  sheets + `config.STYLE_ANCHOR_PROMPT` + SI metric physical scale rules (`1.62m`
  Lia, `0.30m` Nyx cat, `0.75m` table, `0.32m` vase, contact shadows).
- Runs `config.DUAL_TAKE_COUNT` (2) parallel candidate takes per shot with a 60s
  timeout safety guard (`asyncio.wait_for`), scores each take via unified `judge()`
  (Decisions API `gemini-decision-1` + physical scale QA) combined with
  `score_clip_alignment()`, logs 1-take vs 2-take metrics into
  `backend/assets/eval_media_stats.jsonl`, triggers up to `config.MAX_QA_RETRIES`
  corrective retries on failure, and runs `inspect_tail_frame()` feedback.
"""

import asyncio
import base64
import copy
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

import cv2
import numpy as np
from google import genai
from google.genai import types

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.director.hydrogen_judge import judge, score_clip_alignment, write_repair_constraint
from backend.logger import log_function_call, log_genai_call
from backend.media.prop_maker import _resolve_image_model, estimate_prop_height_m
from backend.schemas import BranchState, JudgeItem, JudgeResult, Scene, SceneMedia

_VIDEO_MODEL_ALIASES = {
    "omni-flash": "gemini-omni-flash-preview",
    "gemini-omni-1.2-flash": "gemini-omni-flash-preview",
}

PER_TAKE_TIMEOUT_SEC = 60.0
PHYSICAL_QA_QUESTION = (
    "Is the black cat realistically small (below the girl's knee height) with all characters "
    "and props resting naturally on surfaces without floating?"
)
ARTIFACT_FREE_QA_QUESTION = (
    "Is this a clean full-bleed nighttime anime frame with NO black borders, NO coordinate axes or numbers, "
    "NO duplicate umbrellas or duplicate star tokens, and consistent nighttime atmosphere?"
)
CROSS_CAM_THRESHOLD = 0.80
EVAL_STATS_PATH = config.ASSETS_DIR / "eval_media_stats.jsonl"

# In-memory cache mapping (resolved_path, mtime_ns, size) -> (file_uri, mime_type, uploaded_at).
_UPLOAD_CACHE_TTL_SEC = 12 * 3600.0
_UPLOAD_CACHE: dict[tuple[str, int, int], tuple[str, str, float]] = {}
_UPLOAD_INFLIGHT: dict[tuple[str, int, int], asyncio.Task[tuple[str, str]]] = {}
_UPLOAD_LOCK = asyncio.Lock()

# In-memory render progress tracker + SSE subscriber registry for real-time progress streaming.
_RENDER_PROGRESS: dict[str, dict] = {}
_PROGRESS_SUBSCRIBERS: dict[str, set[asyncio.Queue[dict]]] = {}


def _notify_progress_subscribers(branch_id: str, snapshot: dict) -> None:
    """Push a progress snapshot copy to all active SSE subscribers without blocking."""
    queues = _PROGRESS_SUBSCRIBERS.get(branch_id)
    if not queues:
        return
    payload = copy.deepcopy(snapshot)
    for q in list(queues):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def subscribe_render_progress(branch_id: str) -> asyncio.Queue[dict]:
    """Register an SSE subscriber queue for `branch_id` render progress events."""
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)
    _PROGRESS_SUBSCRIBERS.setdefault(branch_id, set()).add(q)
    return q


def unsubscribe_render_progress(branch_id: str, q: asyncio.Queue[dict]) -> None:
    """Remove an SSE subscriber queue for `branch_id` cleanly on disconnect."""
    queues = _PROGRESS_SUBSCRIBERS.get(branch_id)
    if not queues:
        return
    queues.discard(q)
    if not queues:
        _PROGRESS_SUBSCRIBERS.pop(branch_id, None)


def _open_video_capture_safe(video_path: Path) -> tuple[cv2.VideoCapture, Optional[Path]]:
    """
    Open a video file with OpenCV safely even when the Windows path contains Unicode characters.

    Returns `(cap, temp_copy_path)` — caller must release `cap` and unlink `temp_copy_path` if not None.
    """
    path_str = str(video_path)
    if path_str.isascii():
        cap = cv2.VideoCapture(path_str)
        if cap.isOpened():
            return cap, None
        cap.release()

    import tempfile

    suffix = video_path.suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    shutil.copy2(video_path, tmp_path)
    cap = cv2.VideoCapture(str(tmp_path))
    return cap, tmp_path


def is_browser_h264(video_path: Path) -> bool:
    """Verify an MP4 file uses H.264/avc1 codec natively playable in Chrome <video> tags (Unicode-path safe)."""
    if not video_path.is_file():
        return False
    cap, tmp_path = _open_video_capture_safe(video_path)
    try:
        if not cap.isOpened():
            return False
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).lower()
        return fourcc in {"h264", "avc1", "x264"}
    finally:
        cap.release()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _update_render_progress(
    branch_id: str,
    scene_id: int,
    status: str,
    qa_score: Optional[float] = None,
    fallback: Optional[bool] = None,
    branch_status: Optional[str] = None,
) -> None:
    """Update per-scene progress metadata for a branch render job and push to SSE subscribers."""
    entry = _RENDER_PROGRESS.setdefault(
        branch_id,
        {"branch_id": branch_id, "status": "running", "updated_at": time.time(), "scenes": {}},
    )
    if branch_status is not None:
        entry["status"] = branch_status
    elif status in (
        "queued",
        "rendering",
        "rendering_split_screen",
        "rendering_cam_a",
        "deriving_cam_b_keyframes",
        "rendering_cam_b",
        "cross_cam_qa",
        "uploading_refs",
        "qa_judging",
        "content_blocked_retrying",
    ):
        entry["status"] = "running"
    scene_entry = entry["scenes"].setdefault(str(scene_id), {"scene_id": scene_id, "status": status})
    scene_entry["status"] = status
    if qa_score is not None:
        scene_entry["qa_score"] = qa_score
    if fallback is not None:
        scene_entry["fallback_to_preview"] = fallback
    entry["updated_at"] = round(time.time(), 2)
    _notify_progress_subscribers(branch_id, entry)


def publish_scene_progress(
    branch_id: str,
    scene_id: int,
    status: str,
    qa_score: Optional[float] = None,
    fallback: Optional[bool] = None,
    branch_status: Optional[str] = None,
) -> None:
    """Public helper allowing FastAPI single-scene renders to stream live SSE progress updates."""
    _update_render_progress(
        branch_id,
        scene_id,
        status,
        qa_score=qa_score,
        fallback=fallback,
        branch_status=branch_status,
    )


def set_branch_progress_status(branch_id: str, status: str) -> None:
    """Update top-level branch render status and notify SSE subscribers."""
    entry = _RENDER_PROGRESS.setdefault(
        branch_id,
        {"branch_id": branch_id, "status": status, "updated_at": time.time(), "scenes": {}},
    )
    entry["status"] = status
    entry["updated_at"] = round(time.time(), 2)
    _notify_progress_subscribers(branch_id, entry)


def get_render_progress(branch_id: str) -> dict:
    """Return latest parallel render progress snapshot for `branch_id` (for FastAPI progress polling)."""
    entry = _RENDER_PROGRESS.get(branch_id)
    if not entry:
        return {"branch_id": branch_id, "status": "idle", "scenes": {}}
    return entry


def list_render_progress() -> dict[str, dict]:
    """Return all tracked branch render progress snapshots."""
    return dict(_RENDER_PROGRESS)


def _resolve_video_model() -> str:
    """Resolve `config.MODEL_VIDEO`, mapping placeholder aliases to the live API model ID."""
    return _VIDEO_MODEL_ALIASES.get(config.MODEL_VIDEO, config.MODEL_VIDEO)


def _resolve_asset_to_local_path(asset_ref: Optional[str]) -> Optional[Path]:
    """Convert `/assets/...`, `/shared-assets/...`, or filesystem strings into an existing local Path."""
    if not asset_ref:
        return None
    normalized = asset_ref.strip()
    if normalized.startswith("/shared-assets/"):
        candidate = config.SHARED_ASSETS_DIR / normalized.removeprefix("/shared-assets/").lstrip("/")
        if candidate.is_file():
            return candidate
    if normalized.startswith("/assets/"):
        candidate = config.ASSETS_DIR / normalized.removeprefix("/assets/").lstrip("/")
        if candidate.is_file():
            return candidate
    candidate = Path(normalized)
    if candidate.is_file():
        return candidate
    rel_candidate = config.BASE_DIR / normalized.lstrip("/")
    if rel_candidate.is_file():
        return rel_candidate
    return None


def _detect_mime_type(path: Path) -> str:
    """Detect MIME type for local images and video files."""
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }.get(ext, "application/octet-stream")


def _upload_file_sync(path: Path) -> tuple[str, str]:
    """Upload a local media file to Gemini Files API and wait until ACTIVE."""
    client = genai.Client(http_options=types.HttpOptions(timeout=180_000))
    uploaded = client.files.upload(file=str(path))
    while getattr(getattr(uploaded, "state", None), "name", None) == "PROCESSING":
        time.sleep(0.5)
        uploaded = client.files.get(name=uploaded.name)
    state_name = getattr(getattr(uploaded, "state", None), "name", "ACTIVE")
    if state_name == "FAILED":
        raise RuntimeError(f"Files API processing failed for {path}")
    mime = getattr(uploaded, "mime_type", None) or _detect_mime_type(path)
    return uploaded.uri, mime


def _upload_cache_key(path: Path) -> tuple[str, int, int]:
    """Build an mtime + size aware cache key so updated assets never reuse stale Files API URIs."""
    resolved = path.resolve()
    st = resolved.stat()
    return (str(resolved), int(st.st_mtime_ns), int(st.st_size))


def _evict_upload_cache(paths: list[Path]) -> None:
    """Evict cached Files API URIs for given local paths (e.g. after 403/404 URI expiry)."""
    resolved_set = {str(p.resolve()) for p in paths if p.exists()}
    stale_keys = [k for k in _UPLOAD_CACHE if k[0] in resolved_set]
    for k in stale_keys:
        _UPLOAD_CACHE.pop(k, None)


async def _get_or_upload_uri(path: Path, use_cache: bool = True) -> tuple[str, str]:
    """
    Upload a local asset once and reuse its Files API URI across concurrent takes (mtime & TTL safe).

    Uses per-key in-flight coalescing rather than holding `_UPLOAD_LOCK` during network I/O so
    all distinct reference assets upload concurrently (~2s instead of ~13s serial) while identical
    assets shared across Camera A and Camera B still upload only once.
    """
    if not use_cache:
        return await asyncio.to_thread(_upload_file_sync, path)

    key = _upload_cache_key(path)
    now = time.time()
    cached = _UPLOAD_CACHE.get(key)
    if cached is not None and (now - cached[2]) < _UPLOAD_CACHE_TTL_SEC:
        return cached[0], cached[1]

    loop = asyncio.get_running_loop()
    async with _UPLOAD_LOCK:
        now = time.time()
        cached = _UPLOAD_CACHE.get(key)
        if cached is not None and (now - cached[2]) < _UPLOAD_CACHE_TTL_SEC:
            return cached[0], cached[1]

        inflight = _UPLOAD_INFLIGHT.get(key)
        if inflight is None or inflight.done() or inflight.get_loop() is not loop:
            async def _do_upload() -> tuple[str, str]:
                try:
                    uri, mime = await asyncio.to_thread(_upload_file_sync, path)
                    _UPLOAD_CACHE[key] = (uri, mime, time.time())
                    return uri, mime
                finally:
                    if _UPLOAD_INFLIGHT.get(key) is task:
                        _UPLOAD_INFLIGHT.pop(key, None)

            task: asyncio.Task[tuple[str, str]] = asyncio.create_task(_do_upload())
            _UPLOAD_INFLIGHT[key] = task
        else:
            task = inflight

    return await task


def _write_png_safe(path: Path, frame: np.ndarray) -> bool:
    """Write an OpenCV BGR frame as PNG safely even when path contains non-ASCII characters (Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame)
    if not ok:
        return False
    path.write_bytes(encoded.tobytes())
    return True


def _extract_tail_frame_sync(video_path: Path, tail_path: Path) -> Optional[Path]:
    """Extract the final readable video frame as a PNG image using OpenCV."""
    cap, tmp_path = _open_video_capture_safe(video_path)
    if not cap.isOpened():
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        return None
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        last_frame = None
        if total > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total - 1))
            ok, frame = cap.read()
            if ok and frame is not None:
                last_frame = frame
        if last_frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                last_frame = frame
        if last_frame is None:
            return None
        return tail_path if _write_png_safe(tail_path, last_frame) else None
    finally:
        cap.release()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


def _prepare_preview_media_sync(
    preview_video: Path,
    target_duration_sec: int,
) -> tuple[Optional[Path], Optional[Path]]:
    """
    Extract a crisp start-frame PNG from a `.webm`/`.mp4` preview and decide whether the
    raw video matches `target_duration_sec` closely enough for Omni Flash video-edit mode.

    Browser `MediaRecorder` `.webm` clips frequently vary by +/-0.1s..0.3s, which triggers
    Omni Flash 400 `Edited video duration does not match config duration`. Extracting the
    start frame safely anchors spatial layout without risking a 400 failure.
    """
    cap, tmp_path = _open_video_capture_safe(preview_video)
    if not cap.isOpened():
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        return None, None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        start_img_path: Optional[Path] = None

        # Grab first non-black frame within first 10 frames.
        for _ in range(10):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if float(np.mean(frame)) > 4.0 or start_img_path is None:
                candidate = config.PREVIEWS_DIR / f"{preview_video.stem}_{uuid.uuid4().hex[:6]}_extracted_start.png"
                if _write_png_safe(candidate, frame):
                    if start_img_path is not None and start_img_path != candidate:
                        start_img_path.unlink(missing_ok=True)
                    start_img_path = candidate
                if float(np.mean(frame)) > 4.0 and start_img_path is not None:
                    break

        use_raw_video = False
        if preview_video.suffix.lower() == ".mp4" and fps > 1.0 and frame_count > 1.0:
            duration_est = frame_count / fps
            if abs(duration_est - float(target_duration_sec)) <= 0.05:
                use_raw_video = True

        return start_img_path, (preview_video if use_raw_video else None)
    finally:
        cap.release()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


def _grid_to_blocking(grid: list[float]) -> str:
    """Translate `[col, row]` into natural cinematography blocking (never raw decimal arrays)."""
    col = float(grid[0]) if len(grid) >= 1 else 1.0
    row = float(grid[1]) if len(grid) >= 2 else 1.0
    if col < 0.65:
        horiz = "on the left side of the conservatory"
    elif col > 1.35:
        horiz = "on the right side of the conservatory"
    else:
        horiz = "near the center round table"
    if row < 0.65:
        depth = "toward the back glass wall"
    elif row > 1.45:
        depth = "in the foreground"
    else:
        depth = "at mid-stage"
    return f"{horiz} ({depth})"


_UMBRELLA_OPEN_CUES = re.compile(
    r"\b(open(s|ed|ing)?|unfurl(s|ed|ing)?|under (her|the|a|his|their)( \w+){0,3} umbrella|shared umbrella|"
    r"shelter(s|ed|ing)?|canopy|shield(s|ed|ing)?)\b",
    re.IGNORECASE,
)


def _umbrella_is_open(scene: Scene) -> bool:
    """The umbrella is drawn open only when the scene text says so; indoors it stays furled by default."""
    return bool(_UMBRELLA_OPEN_CUES.search(f"{scene.title} {scene.narration}"))


def _describe_prop_with_scale(
    prop_id: str,
    state: str = "intact",
    on_floor: bool = False,
    opened: bool = False,
    entry_motion: str = "place",
    prop_descriptions: Optional[dict[str, str]] = None,
) -> str:
    """Return a concise, physically anchored natural description preserving exact captured visual traits & SI scale."""
    appearance = (prop_descriptions or {}).get(prop_id, "").strip()
    if prop_id == "red_umbrella":
        if on_floor:
            return "ONE single closed crimson red long-handle umbrella (~0.86m, roughly half Lia's height) lying flat on the floor"
        if opened:
            return "ONE single open crimson red long-handle umbrella (~0.86m canopy)"
        return (
            "ONE single CLOSED, furled crimson red long-handle umbrella (~0.86m) held upright at her side like a walking "
            "cane: curved handle at the top in her hand, tip straight down touching the floor, NOT open, NOT raised, "
            "NOT pointing forward, no canopy above her head"
        )
    if prop_id == "crystal_vase":
        return (
            "shattered clear crystal vase shards and scattered blue rose petals lying flat on the dry wooden floor"
            if state == "broken" or on_floor
            else "ONE single intact clear crystal vase (~0.32m tall, ~42% of table height) holding ONE glowing blue rose resting squarely on the center round table"
        )
    if prop_id == "star_token":
        if entry_motion == "throw_arc":
            return "ONE single small palm-sized (~0.10m, fits inside one hand) glowing golden star keepsake flying in along an incoming throw arc from the front camera"
        return "ONE single small palm-sized (~0.10m, fits inside one hand) glowing golden star keepsake"
    h_m = estimate_prop_height_m(prop_id, appearance)
    table_pct = max(8, min(140, int(round((h_m / 0.75) * 100))))
    size_cue = (
        "small palm-sized object"
        if h_m <= 0.12
        else ("compact tabletop object" if h_m <= 0.25 else "medium stage prop")
    )
    if entry_motion == "throw_arc" and state != "held":
        loc = "entering along an incoming throw arc from the front camera before settling onto the table or floor"
    elif state == "held":
        loc = "held naturally in hand"
    elif on_floor:
        loc = "resting flat on the floor with contact shadow"
    else:
        loc = "resting squarely on the center round table with contact shadow"
    label = appearance if appearance else prop_id.replace("_", " ")
    return f"ONE single {prop_id.replace('_', ' ')} ({label}; {size_cue}, ~{h_m:.2f}m tall / ~{table_pct}% of table height, {loc}, state={state})"


def _trim_dark_borders_sync(img_path: Path) -> tuple[Path, bool]:
    """
    Crop dark letterboxing/pillarboxing border bars (R-40: up to 6% per edge when border luminance < 16)
    into a clean temporary PNG so start-frame anchoring never seeds black corner frames.
    """
    raw_bytes = img_path.read_bytes()
    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None or bgr.size == 0:
        return img_path, False
    h, w = bgr.shape[:2]
    if h < 64 or w < 64:
        return img_path, False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    max_y = max(1, int(round(h * 0.06)))
    max_x = max(1, int(round(w * 0.06)))

    top = 0
    while top < max_y and float(np.median(gray[top, :])) < 16.0:
        top += 1
    bot = 0
    while bot < max_y and float(np.median(gray[h - 1 - bot, :])) < 16.0:
        bot += 1
    left = 0
    while left < max_x and float(np.median(gray[:, left])) < 16.0:
        left += 1
    right = 0
    while right < max_x and float(np.median(gray[:, w - 1 - right])) < 16.0:
        right += 1

    if top + bot + left + right == 0:
        return img_path, False
    cropped = bgr[top : h - bot, left : w - right]
    if cropped.size == 0:
        return img_path, False
    resized = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_CUBIC)
    out_tmp = config.PREVIEWS_DIR / f"{img_path.stem}_cropped_{uuid.uuid4().hex[:6]}.png"
    if _write_png_safe(out_tmp, resized):
        return out_tmp, True
    return img_path, False


def _milden_safety_phrasing(text: str) -> str:
    """Soften false-positive safety trigger phrases (R-39) while preserving exact narrative meaning."""
    replacements = (
        ("crashing onto", "tipping onto"),
        ("shattering", "scattering"),
        ("shattered", "broken"),
        ("violence", "commotion"),
    )
    out = text
    for src, dst in replacements:
        out = out.replace(src, dst).replace(src.capitalize(), dst.capitalize())
    return out


def _resolve_clean_actor_ref(path: Path, scene: Optional[Scene] = None) -> Path:
    """
    Swap raw standee references for cleaner Omni Flash equivalents:
    - `lia.png` -> umbrella-free `lia_clean_ref.png` (prevents duplicate closed umbrellas).
    - `crystal_vase.png` -> `crystal_vase_broken.png` when the scene's vase is broken
      (prevents drawing a second intact vase on the table alongside floor shards).
    """
    lower = path.name.lower()
    if lower == "lia.png":
        clean_ref = config.ACTORS_DIR / "lia_clean_ref.png"
        if clean_ref.is_file():
            return clean_ref
    if lower == "crystal_vase.png" and scene is not None:
        vase_broken = any(p.id == "crystal_vase" and p.state == "broken" for p in scene.stage_props)
        if vase_broken:
            broken_ref = config.PROPS_DIR / "crystal_vase_broken.png"
            if broken_ref.is_file():
                return broken_ref
    return path


def _select_scene_ref_images(
    scene: Scene,
    reference_images: list[str],
    max_refs: int,
    cam_id: Optional[str] = None,
) -> list[Path]:
    """
    Prioritize and prune reference images to ONLY actors and props active in `scene`,
    plus isolated umbrella/chair refs and the fixed empty-conservatory stage room ref (`cam_a`/`cam_b`).
    """
    active_actor_ids = {a.id.lower() for a in scene.actors}
    active_prop_ids = {p.id.lower() for p in scene.stage_props}
    for a in scene.actors:
        if a.holding_prop:
            active_prop_ids.add(a.holding_prop.lower())

    # Inject fixed isolated red_umbrella & wooden_chair assets when referenced in scene/notes.
    expanded_refs = list(reference_images)
    if "red_umbrella" in active_prop_ids:
        umb_name = "red_umbrella_open.png" if _umbrella_is_open(scene) else "red_umbrella_closed.png"
        umb_path = config.PROPS_DIR / umb_name
        if umb_path.is_file():
            expanded_refs.append(str(umb_path))
    notes_blob = f"{scene.narration} {scene.environment_notes}".lower()
    if "wooden_chair" in active_prop_ids or "chair" in notes_blob:
        chair_path = config.PROPS_DIR / "wooden_chair.png"
        if chair_path.is_file():
            active_prop_ids.add("wooden_chair")
            expanded_refs.append(str(chair_path))

    prioritized_actors: list[Path] = []
    prioritized_props: list[Path] = []
    stage_refs: list[Path] = []
    companion_refs: list[Path] = []
    seen_paths: set[str] = set()

    if cam_id in {"cam_a", "cam_b"}:
        room_ref = config.ASSETS_DIR / "stages" / f"conservatory_{cam_id}.png"
        if room_ref.is_file():
            seen_paths.add(str(room_ref.resolve()))
            stage_refs.append(room_ref)

    for ref in expanded_refs:
        raw_p = _resolve_asset_to_local_path(ref)
        if not raw_p:
            continue
        p = _resolve_clean_actor_ref(raw_p, scene=scene)
        resolved_key = str(p.resolve())
        if resolved_key in seen_paths:
            continue

        stem = raw_p.stem.lower()
        parent_name = raw_p.parent.name.lower()

        if stem in active_actor_ids or stem in {"lia_clean_ref"}:
            if stem == "lia_clean_ref" and "lia" not in active_actor_ids:
                continue
            seen_paths.add(resolved_key)
            prioritized_actors.append(p)
        elif (
            stem in active_prop_ids
            or (stem == "crystal_vase_broken" and "crystal_vase" in active_prop_ids)
            or (stem in {"red_umbrella_open", "red_umbrella_closed"} and "red_umbrella" in active_prop_ids)
        ):
            seen_paths.add(resolved_key)
            prioritized_props.append(p)
        elif parent_name in {"tails", "previews"}:
            seen_paths.add(resolved_key)
            companion_refs.append(p)
        elif parent_name in {"props", "actors", "stages"}:
            continue
        else:
            seen_paths.add(resolved_key)
            companion_refs.append(p)

    # When Camera B starts from a pre-synthesized +45deg geometric keyframe (start_frame_path),
    # omit room refs & frontal Cam A frames so Omni Flash stays locked strictly to the +45deg start frame.
    has_cam_a_anchor = cam_id == "cam_b" and any(
        "cam_a" in p.name.lower() for p in companion_refs
    )
    if cam_id == "cam_b" and not stage_refs:
        selected = companion_refs + prioritized_actors + prioritized_props
    elif has_cam_a_anchor:
        selected = companion_refs + prioritized_actors + prioritized_props
    else:
        # Actors + fixed room ref first (never evicted by extra props), then active props, then companion frames.
        selected = prioritized_actors + stage_refs + prioritized_props + companion_refs
    return selected[:max_refs]


_ACTION_PHRASES = {
    "drink": "lifting the held object to her lips and clearly drinking from it",
    "eat": "bringing the held snack to her mouth and clearly eating it",
    "hold_up": "holding the object up in front of her chest and looking at it",
}


def _action_phrase(action: str) -> str:
    """Spell out prop-use actions so the video model animates them instead of a neutral pose."""
    return _ACTION_PHRASES.get(action, action)


def _size_word(height_m: float) -> str:
    if height_m < 0.12:
        return "smaller than Lia's hand"
    if height_m < 0.24:
        return "about as long as Lia's hand"
    if height_m < 0.45:
        return "about as long as Lia's forearm"
    if height_m < 0.80:
        return "about up to Lia's hip"
    return "close to Lia's own height"


def _build_size_chart(scene: Scene) -> str:
    """One sentence fixing every on-stage height relative to Lia, shared by both cameras."""
    parts = [
        "Lia is 1.62 m tall",
        "Nyx the cat is 0.30 m tall at the head, clearly below Lia's knee",
        "the round table top is 0.75 m high, about Lia's hip",
    ]
    seen: set[str] = set()
    prop_ids = [a.holding_prop for a in scene.actors if a.holding_prop] + [p.id for p in scene.stage_props]
    for pid in prop_ids:
        if not pid or pid in seen:
            continue
        seen.add(pid)
        if pid == "crystal_vase":
            parts.append("the crystal vase is 0.32 m tall, about as long as Lia's forearm")
            continue
        if pid == "red_umbrella":
            parts.append("the red umbrella is 0.86 m long, a little over half of Lia's height")
            continue
        label = re.sub(r"_[0-9a-f]{4,8}$", "", pid).replace("_", " ")
        height = float(estimate_prop_height_m(label))
        parts.append(f"the {label} is {height:.2f} m tall, {_size_word(height)}")
    return "Relative size chart (identical in every shot and in both cameras): " + "; ".join(parts) + "."


def _build_shot_prompt(
    scene: Scene,
    cam_id: str,
    has_first_frame: bool,
    has_preview_video: bool,
    extra_constraint: str = "",
    prop_descriptions: Optional[dict[str, str]] = None,
) -> str:
    """Build a natural-language Omni Flash prompt enforcing camera angle, room lock, sculpture freeze, and SI scale."""
    if cam_id == "cam_a":
        cam_desc = (
            "Camera A WIDE ESTABLISHING front-center full-stage shot (IGNORE any mention of Camera B or close-up in narration): "
            "pull the camera back to show the entire conservatory floor, all three arched glass walls, the central 0.75m round "
            "cafe table ALWAYS clearly visible in the middle, and full bodies of all characters from head to shoes, "
            "filling the whole frame edge to edge from the very first frame with no black borders or bars"
        )
    else:
        cam_desc = (
            "Camera B GEOMETRICALLY DERIVED +45-DEGREE RIGHT-SIDE ORBIT OF CAMERA A (Reference Image 1) "
            "(IGNORE any mention of Camera A wide shot in narration): "
            "This MUST be the EXACT SAME instant, EXACT SAME conservatory room architecture (identical arched glass mullions, "
            "identical ceiling dome ribs, identical wall lanterns, identical wooden floor planks, identical chair presence/absence, "
            "identical outdoor skyline), EXACT SAME props, and EXACT SAME character 3D world-space positions & face/head gaze "
            "directions shown in Reference Image 1 (Camera A) — ONLY the virtual camera has orbited +45 degrees clockwise to the "
            "front-right around the central round table! "
            "STRICT 3D PERSPECTIVE & FACING LOCK: because the camera moved +45 degrees right (closer to Nyx on the right), "
            "Nyx appears closer in the right foreground/midground, the center table and props sit angled centrally in two-point "
            "perspective, the left glass wall recedes diagonally toward back-left, and Lia stands slightly further left-midground. "
            "Keep Lia's and Nyx's head/face direction FIXED IN 3D WORLD SPACE identical to Reference Image 1 (e.g. if Lia faces "
            "right toward the table/cat or drinks her beverage in Camera A, keep her facing that EXACT SAME world target in "
            "Camera B — NEVER rotate Lia's face toward the lens or flip Nyx's gaze direction). "
            "Vertical lines stay strictly vertical, floor and horizon stay level (NO Dutch tilt, NO camera roll), edge-to-edge full frame"
        )

    lighting_desc = {
        "warm_dim": "NIGHTTIME dark rainy sky visible outside all glass panes, illuminated inside by warm dim amber wall lanterns",
        "cold_blue": "NIGHTTIME stormy midnight-blue rain streaking down all glass panes, cold moody blue moonlight inside",
        "warm_transition": "NIGHTTIME dark indigo starry night sky outside (STRICTLY NIGHTTIME, NEVER sunny daylight), warm amber indoor lantern glow softly replacing the cold rain",
        "warm_gold_snow": "NIGHTTIME deep starry sky outside, magical warm golden starlight and gentle falling snowflakes inside the conservatory",
    }.get(scene.lighting, f"NIGHTTIME glass conservatory with {scene.lighting} lighting")

    held_prop_ids: set[str] = set()
    actor_phrases: list[str] = []
    for a in scene.actors:
        blocking = _grid_to_blocking(a.grid)
        desc = f"{a.name} ({'silver-haired anime girl in starry midnight-blue cloak' if a.id == 'lia' else 'small realistic black cat with golden moon collar'}) positioned {blocking}, facing {a.facing}, performing {_action_phrase(a.action)}"
        if a.holding_prop:
            held_prop_ids.add(a.holding_prop)
            desc += (
                f" holding {_describe_prop_with_scale(a.holding_prop, state='held', on_floor=False, opened=_umbrella_is_open(scene), prop_descriptions=prop_descriptions)}"
            )
        actor_phrases.append(desc)

    prop_phrases: list[str] = []
    seen_props: set[str] = set()
    has_catch_action = any(a.action == "catch_from_camera" for a in scene.actors)
    has_throw_arc_prop = any(getattr(p, "entry_motion", "place") == "throw_arc" for p in scene.stage_props)
    for p in scene.stage_props:
        if p.id in held_prop_ids or p.id in seen_props:
            continue
        seen_props.add(p.id)
        blocking = _grid_to_blocking(p.grid)
        prop_entry = getattr(p, "entry_motion", "place")
        prop_phrases.append(
            f"{_describe_prop_with_scale(p.id, state=p.state, on_floor=p.on_floor, entry_motion=prop_entry, prop_descriptions=prop_descriptions)} ({blocking})"
        )

    safe_narration = _milden_safety_phrasing(scene.narration)
    parts = [
        config.STYLE_ANCHOR_PROMPT,
        "Full-bleed borderless anime cinema frame inside a nighttime rooftop glass conservatory — NO black borders, NO coordinate axes, NO tick marks, NO numbers, NO text or captions.",
        f"{cam_desc}.",
        f"PRIMARY PHYSICAL ACTION TO ANIMATE VISIBLY (highest priority — characters MUST perform this exact action clearly throughout the shot): {safe_narration}",
        _build_size_chart(scene),
        (
            "All weather stays OUTSIDE the glass. The room interior is completely dry and calm: no rain, mist, fog, wind, "
            "lightning, snow, water drops or puddles indoors, and the wooden floor is dry."
        ),
        f"Time & lighting: {lighting_desc}.",
        f"Audio & SFX mood: {scene.audio_mood or scene.vocal_emotion}.",
    ]
    if cam_id == "cam_b":
        parts.append(
            "Room & 3D character geometry lock: copy the EXACT room architecture, ceiling dome ribs, arched glass panes, "
            "wall lanterns, furniture presence/absence, table props, and 3D world-space character face/body directions "
            "from Camera A (Reference Image 1), rotated ONLY by a +45-degree right-side camera orbit."
        )
    elif (config.ASSETS_DIR / "stages" / "conservatory_cam_a.png").is_file():
        parts.append(
            "Room architecture lock: glass window arches, wall lanterns, floor tiles, and center table layout MUST match the empty conservatory room reference image identically across scenes and cameras."
        )
    if actor_phrases:
        parts.append("Characters: " + "; ".join(actor_phrases) + ".")
    if prop_phrases:
        parts.append("Stage props: " + "; ".join(prop_phrases) + ".")
    if scene.environment_notes:
        clean_notes = _milden_safety_phrasing(
            scene.environment_notes.replace("warm_sunlight_streaming", "warm_night_lantern_glow").replace("lush_trees_outside", "dark_night_trees_outside")
        )
        parts.append(f"Persistent scene details: {clean_notes}.")
    if scene.camera_shake:
        parts.append("Include a gentle camera tremor at the moment of surprise.")
    if extra_constraint:
        parts.append(f"Strict visual constraints: {extra_constraint}.")
    if has_first_frame:
        parts.append("Use the first image as the starting frame for seamless spatial continuity.")
    if has_preview_video:
        parts.append("Transform the stage motion from the preview video into rich anime cinema style while preserving exact character placement.")
    else:
        parts.append("Match character and prop designs strictly to the provided reference images, with ZERO extra unintended characters.")

    # Only prompt forward camera grab when explicitly requested (catch_from_camera / throw_arc);
    # default props rest naturally on table/floor.
    if has_catch_action or has_throw_arc_prop:
        parts.append(
            "Explicit toss/hand-off choreography: ONLY when explicitly handed/tossed to her — Lia leans gracefully forward "
            "toward the camera lens to catch the flying item out of mid-air, then naturally interacts with it."
        )
    elif prop_phrases:
        parts.append(
            "Default prop placement: keep all newly placed stage props resting naturally on the round center table or "
            "conservatory floor without characters reaching toward the camera."
        )

    if getattr(scene, "end_freeze_mode", "none") == "sculpture":
        parts.append(
            "Final beat tableau freeze: during the final 0.5 seconds, all characters, pets, clothes, and props come to a poised, "
            "completely motionless frozen-sculpture pose holding grounded feet and clear gaze for seamless stage freeze."
        )

    parts.append(
        "Physical scale & gravity: Lia is a 1.62m teenage girl, Nyx is a small realistic 0.30m sitting domestic black cat "
        "(clearly below Lia's knee height, never dog-sized), the central round conservatory table is ALWAYS present in the center "
        "at 0.75m waist height (0.76m diameter), the crystal vase is 0.32m tall, and any star keepsake is a single tiny 0.10m "
        "palm-sized charm. Feet, paws, table legs, and props rest solidly on real surfaces with soft contact shadows—zero floating "
        "objects, zero table clipping, NO fade-in from black, and NEVER draw duplicate umbrellas or duplicate stars."
    )
    return " ".join(parts)


def _generate_omni_take_sync(
    model_id: str,
    input_parts: list[dict],
    duration_sec: int,
    resolution: str,
    aspect_ratio: str,
    out_video_path: Path,
) -> dict:
    """Run one Omni Flash Interactions API generation call and download the `.mp4` locally."""
    client = genai.Client(http_options=types.HttpOptions(timeout=55_000))
    interaction = client.interactions.create(
        model=model_id,
        input=input_parts,
        response_format={
            "type": "video",
            "delivery": "uri",
            "aspect_ratio": aspect_ratio,
            "duration": f"{duration_sec}s",
            "resolution": resolution,
        },
    )
    output_video = getattr(interaction, "output_video", None)
    if not output_video or not getattr(output_video, "uri", None):
        raise RuntimeError(f"Omni Flash returned no output_video for {out_video_path.name}")

    video_bytes = client.files.download(file=output_video.uri)
    out_video_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_video_path, "wb") as f:
        f.write(video_bytes)
    return {
        "interaction_id": getattr(interaction, "id", None),
        "bytes": len(video_bytes),
        "saved_path": str(out_video_path),
    }


def _append_eval_media_stat(record: dict) -> None:
    """Append a single JSONL record of 1-take vs 2-take QA scores for `eval/run_eval.py`."""
    try:
        EVAL_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVAL_STATS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _run_candidate_take(
    scene: Scene,
    cam_id: str,
    take_idx: int,
    attempt: int,
    model_id: str,
    uploaded_refs: list[tuple[str, str]],
    uploaded_prev_tail: Optional[tuple[str, str]],
    uploaded_preview: Optional[tuple[str, str]],
    extra_constraint: str,
    job_uid: str = "",
    fast_live_mode: bool = False,
    prop_descriptions: Optional[dict[str, str]] = None,
) -> tuple[Optional[Path], Optional[Path], JudgeResult, float]:
    """Generate one candidate take (60s timeout guard), extract tail frame, and score via Decisions API."""
    uid_part = f"_{job_uid}" if job_uid else f"_{uuid.uuid4().hex[:6]}"
    out_video = config.FINALS_DIR / f"scene_{scene.scene_id}_{cam_id}{uid_part}_r{attempt}_take{take_idx}.mp4"
    out_tail = config.TAILS_DIR / f"scene_{scene.scene_id}_{cam_id}{uid_part}_r{attempt}_take{take_idx}_tail.png"
    prompt = _build_shot_prompt(
        scene=scene,
        cam_id=cam_id,
        has_first_frame=uploaded_prev_tail is not None,
        has_preview_video=uploaded_preview is not None,
        extra_constraint=extra_constraint,
        prop_descriptions=prop_descriptions,
    )

    input_parts: list[dict] = []
    if uploaded_prev_tail is not None:
        input_parts.append({"type": "image", "uri": uploaded_prev_tail[0], "mime_type": uploaded_prev_tail[1]})
    for uri, mime in uploaded_refs:
        input_parts.append({"type": "image", "uri": uri, "mime_type": mime})
    if uploaded_preview is not None:
        input_parts.append({"type": "video", "uri": uploaded_preview[0], "mime_type": uploaded_preview[1]})
    input_parts.append({"type": "text", "text": prompt})

    duration_int = max(3, min(10, int(round(scene.duration_sec or config.VIDEO_DURATION_SEC))))
    t0 = time.perf_counter()
    try:
        try:
            meta = await asyncio.wait_for(
                asyncio.to_thread(
                    _generate_omni_take_sync,
                    model_id,
                    input_parts,
                    duration_int,
                    config.VIDEO_RESOLUTION,
                    config.VIDEO_ASPECT_RATIO,
                    out_video,
                ),
                timeout=PER_TAKE_TIMEOUT_SEC,
            )
        except Exception as first_exc:
            first_lower = str(first_exc).lower()
            # If video-edit mode rejects preview duration/format (400), retry once with image-start anchor only.
            if uploaded_preview is not None and ("400" in str(first_exc) or "duration" in first_lower):
                fallback_parts = [p for p in input_parts if p.get("type") != "video"]
                meta = await asyncio.wait_for(
                    asyncio.to_thread(
                        _generate_omni_take_sync,
                        model_id,
                        fallback_parts,
                        duration_int,
                        config.VIDEO_RESOLUTION,
                        config.VIDEO_ASPECT_RATIO,
                        out_video,
                    ),
                    timeout=PER_TAKE_TIMEOUT_SEC,
                )
            elif any(tok in first_lower for tok in ("content_blocked", "safety", "blocked", "moderation")):
                # R-39: Omni Flash input filter occasionally blocks benign prompts (~2-6s fast fail);
                # retry once immediately with concise, calm, lower-cased storybook phrasing.
                cam_soft = (
                    "wide front-center full-stage establishing angle showing the entire glass conservatory room"
                    if cam_id == "cam_a"
                    else "level 45-degree front-right side close-up across the central round wooden table (vertical pillars straight, level floor, no Dutch tilt)"
                )
                soft_prompt = _milden_safety_phrasing(
                    f"{config.STYLE_ANCHOR_PROMPT} Full-bleed borderless nighttime anime film scene inside the glass conservatory, "
                    f"{cam_soft}. Gentle storybook action: {scene.narration} Lighting: nighttime {scene.lighting} mood with warm lanterns. "
                    "Keep exact room architecture, character designs, realistic small black cat size, and props matching the reference images, "
                    "settling calmly into a balanced still frame at the end."
                )
                soft_parts = [p for p in input_parts if p.get("type") != "text"] + [{"type": "text", "text": soft_prompt}]
                meta = await asyncio.wait_for(
                    asyncio.to_thread(
                        _generate_omni_take_sync,
                        model_id,
                        soft_parts,
                        duration_int,
                        config.VIDEO_RESOLUTION,
                        config.VIDEO_ASPECT_RATIO,
                        out_video,
                    ),
                    timeout=PER_TAKE_TIMEOUT_SEC,
                )
            else:
                raise
        elapsed = round(time.perf_counter() - t0, 2)
        tail_path = await asyncio.to_thread(_extract_tail_frame_sync, out_video, out_tail)
        log_genai_call(
            model_id,
            {"prompt": prompt, "duration": f"{duration_int}s", "resolution": config.VIDEO_RESOLUTION},
            {"cam_id": cam_id, "take_idx": take_idx, "attempt": attempt, "ref_count": len(uploaded_refs)},
            {**meta, "elapsed_sec": elapsed, "tail_frame": str(tail_path) if tail_path else None},
        )
    except Exception as exc:
        elapsed = round(time.perf_counter() - t0, 2)
        raw_err = f"{type(exc).__name__}: {exc}"
        lower_err = raw_err.lower()
        is_blocked = any(tok in lower_err for tok in ("content_blocked", "safety", "blocked", "moderation"))
        err_msg = (
            f"[content_blocked] Safety filter blocked generation; retrying softer phrasing ({raw_err})"
            if is_blocked
            else raw_err
        )
        log_genai_call(
            model_id,
            {"prompt": prompt},
            {"cam_id": cam_id, "take_idx": take_idx, "attempt": attempt, "timeout_sec": PER_TAKE_TIMEOUT_SEC, "content_blocked": is_blocked},
            {"error": err_msg, "elapsed_sec": elapsed},
        )
        questions = list(scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"])
        failed_items = [JudgeItem(question=q, passed=False, confidence=0.0, reason=err_msg) for q in questions]
        return None, None, JudgeResult(overall_score=0.0, all_passed=False, items=failed_items), 0.0

    questions = list(scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"])
    if not fast_live_mode:
        if PHYSICAL_QA_QUESTION not in questions:
            questions.append(PHYSICAL_QA_QUESTION)
        if ARTIFACT_FREE_QA_QUESTION not in questions:
            questions.append(ARTIFACT_FREE_QA_QUESTION)

    media_paths = [str(out_video)]
    if fast_live_mode:
        qa_res = await judge(questions, context_text=prompt, media_paths=media_paths)
        return out_video, tail_path, qa_res, qa_res.overall_score

    target_desc = f"{cam_id}: {scene.narration}. Lighting={scene.lighting}."
    qa_res, align_score = await asyncio.gather(
        judge(questions, context_text=prompt, media_paths=media_paths),
        score_clip_alignment(target_desc, media_paths=media_paths),
    )
    combined_score = round(0.70 * qa_res.overall_score + 0.30 * align_score, 3)
    return out_video, tail_path, qa_res, combined_score


@log_function_call
async def render_single_camera_shot(
    scene: Scene,
    cam_id: str,
    prev_tail_path: Optional[str],
    reference_images: list[str],
    preview_path: Optional[str] = None,
    extra_constraint: str = "",
    overwrite_canonical: bool = False,
    fast_live_mode: bool = False,
    prop_descriptions: Optional[dict[str, str]] = None,
) -> tuple[str, Optional[str], JudgeResult, int, int]:
    """
    Generate parallel Omni Flash candidates (1 take when `fast_live_mode=True` for 10-20s turnaround,
    else 2 takes) for one scene + camera, score via `judge()`, retry on failure without polluting
    `rule_memory`, log 1-take vs 2-take metrics, and return `(final_url, tail_frame_url, judge_result, selected_idx, retries)`.
    """
    model_id = _resolve_video_model()
    prompt = _build_shot_prompt(
        scene=scene,
        cam_id=cam_id,
        has_first_frame=bool(prev_tail_path),
        has_preview_video=bool(preview_path),
        extra_constraint=extra_constraint,
        prop_descriptions=prop_descriptions,
    )
    run_tag = "" if overwrite_canonical else f"_{uuid.uuid4().hex[:6]}"

    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        log_genai_call(
            model_id,
            {"prompt": prompt, "resolution": config.VIDEO_RESOLUTION, "duration": config.VIDEO_DURATION_SEC},
            {"dual_take": config.DUAL_TAKE_COUNT, "refs": reference_images, "preview": preview_path, "prev_tail": prev_tail_path},
            {"mock": True},
        )
        qa_res = await judge(
            scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"],
            context_text=prompt,
        )
        retries = 0
        if qa_res.overall_score < config.QA_PASS_THRESHOLD and retries < config.MAX_QA_RETRIES:
            _ = await write_repair_constraint(scene, qa_res)
            retries += 1
        final_url = f"/assets/finals/scene_{scene.scene_id}_{cam_id}{run_tag}.mp4"
        tail_url = f"/assets/tails/scene_{scene.scene_id}_{cam_id}{run_tag}_tail.png"
        return final_url, tail_url, qa_res, 0, retries

    # Resolve & upload reference assets (capped at 6 total images including start frame).
    duration_int = max(3, min(10, int(round(scene.duration_sec or config.VIDEO_DURATION_SEC))))
    local_prev_tail = _resolve_asset_to_local_path(prev_tail_path)
    local_preview_any = _resolve_asset_to_local_path(preview_path)
    local_preview_img: Optional[Path] = None
    local_preview: Optional[Path] = None
    extracted_preview_img: Optional[Path] = None
    if local_preview_any:
        if local_preview_any.suffix.lower() in {".mp4", ".webm"}:
            local_preview_img, local_preview = await asyncio.to_thread(
                _prepare_preview_media_sync, local_preview_any, duration_int
            )
            extracted_preview_img = local_preview_img
        elif local_preview_any.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            local_preview_img = local_preview_any

    if local_prev_tail is not None and "cam_b_45deg" in local_prev_tail.name.lower():
        start_frame_path = local_prev_tail
    else:
        start_frame_path = local_preview_img or local_prev_tail
    trimmed_start_artifact: Optional[Path] = None
    if start_frame_path is not None and start_frame_path.is_file():
        start_frame_path, was_trimmed = await asyncio.to_thread(_trim_dark_borders_sync, start_frame_path)
        if was_trimmed:
            trimmed_start_artifact = start_frame_path

    max_refs = 5 if start_frame_path else 6
    ref_cam_id = None if (start_frame_path and "cam_b_45deg" in start_frame_path.name.lower()) else cam_id
    local_refs = _select_scene_ref_images(scene, reference_images, max_refs=max_refs, cam_id=ref_cam_id)

    uploaded_prev_tail = await _get_or_upload_uri(start_frame_path, use_cache=False) if start_frame_path else None
    uploaded_refs = list(await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in local_refs)))
    uploaded_preview = await _get_or_upload_uri(local_preview, use_cache=False) if local_preview else None

    job_uid = uuid.uuid4().hex[:8]
    current_constraint = extra_constraint
    retries = 0
    best_video: Optional[Path] = None
    best_tail: Optional[Path] = None
    best_qa: Optional[JudgeResult] = None
    best_combined = -1.0
    best_idx = 0
    first_take0_score: Optional[float] = None
    first_take1_score: Optional[float] = None
    temp_artifacts: list[Path] = []
    if extracted_preview_img is not None:
        temp_artifacts.append(extracted_preview_img)
    if trimmed_start_artifact is not None:
        temp_artifacts.append(trimmed_start_artifact)

    canonical_video = config.FINALS_DIR / f"scene_{scene.scene_id}_{cam_id}{run_tag}.mp4"
    canonical_tail = config.TAILS_DIR / f"scene_{scene.scene_id}_{cam_id}{run_tag}_tail.png"

    max_retries = 0 if fast_live_mode else config.MAX_QA_RETRIES
    takes_per_attempt = 1 if fast_live_mode else config.DUAL_TAKE_COUNT

    try:
        for attempt in range(max_retries + 1):
            takes = await asyncio.gather(
                *(
                    _run_candidate_take(
                        scene=scene,
                        cam_id=cam_id,
                        take_idx=idx,
                        attempt=attempt,
                        model_id=model_id,
                        uploaded_refs=uploaded_refs,
                        uploaded_prev_tail=uploaded_prev_tail,
                        uploaded_preview=uploaded_preview,
                        extra_constraint=current_constraint,
                        job_uid=job_uid,
                        fast_live_mode=fast_live_mode,
                        prop_descriptions=prop_descriptions,
                    )
                    for idx in range(takes_per_attempt)
                )
            )
            for v_path, t_path, _, _ in takes:
                if v_path:
                    temp_artifacts.append(v_path)
                if t_path:
                    temp_artifacts.append(t_path)

            if attempt == 0:
                first_take0_score = takes[0][2].overall_score if len(takes) > 0 else None
                first_take1_score = takes[1][2].overall_score if len(takes) > 1 else None

            # If every take failed due to an expired/403/404 Files API URI, evict & re-upload once before retry.
            if all(t[0] is None for t in takes) and any(
                "403" in (it.reason or "") or "404" in (it.reason or "") or "not found" in (it.reason or "").lower()
                for t in takes
                for it in t[2].items
            ):
                _evict_upload_cache(local_refs)
                uploaded_refs = list(await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in local_refs)))

            # Pick candidate maximizing combined QA + Decisions alignment score (among valid videos).
            winner_idx, winner = max(
                enumerate(takes),
                key=lambda pair: (pair[1][0] is not None, pair[1][3]),
            )
            cand_video, cand_tail, cand_qa, cand_combined = winner

            # Regression guard: only replace overall best across attempts when candidate is strictly better
            # (or when no valid video has been captured yet).
            if best_qa is None or (cand_video is not None and (best_video is None or cand_combined > best_combined)):
                best_video, best_tail, best_qa, best_combined = cand_video, cand_tail, cand_qa, cand_combined
                best_idx = winner_idx

            if best_qa.overall_score >= config.QA_PASS_THRESHOLD or attempt >= max_retries:
                break

            retries += 1
            repair_note = await write_repair_constraint(scene, cand_qa)
            current_constraint = f"{current_constraint} {repair_note}".strip()

        assert best_qa is not None
        # R-14: Keep global rule_memory clean (do NOT auto-write per-scene failure repairs into timeline rules).

        _append_eval_media_stat(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "scene_id": scene.scene_id,
                "cam_id": cam_id,
                "take_0_score": first_take0_score,
                "take_1_score": first_take1_score,
                "selected_idx": best_idx,
                "selected_qa_score": best_qa.overall_score,
                "selected_combined_score": round(max(0.0, best_combined), 3),
                "repaired_score": best_qa.overall_score if retries > 0 else None,
                "retries": retries,
                "passed": best_qa.overall_score >= config.QA_PASS_THRESHOLD,
            }
        )

        final_url: Optional[str] = None
        tail_url: Optional[str] = None
        if best_video and best_video.is_file():
            shutil.copy2(best_video, canonical_video)
            final_url = f"/assets/finals/{canonical_video.name}"
        if best_tail and best_tail.is_file():
            shutil.copy2(best_tail, canonical_tail)
            tail_url = f"/assets/tails/{canonical_tail.name}"

        return final_url or "", tail_url, best_qa, best_idx, retries
    finally:
        for artifact in temp_artifacts:
            if artifact not in {canonical_video, canonical_tail}:
                artifact.unlink(missing_ok=True)


async def _check_cross_camera_consistency(
    scene: Scene,
    cam_a_tail_url: Optional[str],
    cam_b_tail_url: Optional[str],
) -> Optional[JudgeItem]:
    """Fast (~0.9s) single-condition Decisions API checks comparing Cam A & Cam B tail frames (R-13)."""
    if config.DEMO_MODE or not config.GEMINI_API_KEY or not config.USE_DECISIONS_API:
        return None
    path_a = _resolve_asset_to_local_path(cam_a_tail_url)
    path_b = _resolve_asset_to_local_path(cam_b_tail_url)
    if not path_a or not path_b:
        return None
    vase_broken = any(p.id == "crystal_vase" and p.state == "broken" for p in scene.stage_props)
    vase_expect = "broken shards on the floor" if vase_broken else "intact on the center table"
    q_map = {
        "room": {
            "type": "noul",
            "instructions": f"Do Image 1 (Cam A) and Image 2 (Cam B) depict matching {scene.lighting} conservatory architecture and lighting?",
        },
        "actors": {
            "type": "noul",
            "instructions": "Do Lia and the small black cat stay in matching pose and relative sides of the round table in both images?",
        },
        "vase": {
            "type": "noul",
            "instructions": f"Does the crystal vase appear {vase_expect} in both Image 1 and Image 2?",
        },
    }
    try:
        payload = build_decision_input(f"Cross-camera check for Scene {scene.scene_id}: {scene.narration}", [path_a, path_b])
        res = await create_decision(payload, q_map)
        answers = getattr(res, "answers", None) or {}
        probs = [float(getattr(answers.get(k), "noul", 0.5)) if answers.get(k) is not None else 0.5 for k in q_map]
        all_ok = all(p >= CROSS_CAM_THRESHOLD for p in probs)
        min_p = min(probs)
        avg_p = sum(probs) / len(probs)
        conf = avg_p if all_ok else max(0.0, 1.0 - min_p)
        return JudgeItem(
            question="[Dual-Cam] Camera A & Camera B room, actor, and vase consistency",
            passed=all_ok,
            confidence=round(conf, 3),
            reason=f"Cross-Cam Decision-1 (thresh={CROSS_CAM_THRESHOLD:.2f}) P(room={probs[0]:.2f}, actors={probs[1]:.2f}, vase={probs[2]:.2f})",
        )
    except Exception:
        return None


def _merge_dual_cam_qa(
    qa_a: JudgeResult,
    qa_b: JudgeResult,
    cross_cam_item: Optional[JudgeItem] = None,
) -> JudgeResult:
    """Combine BOTH Camera A (`[CAM A]`) and Camera B (`[CAM B]`) QA items for ClapperboardQA."""
    merged_items: list[JudgeItem] = []
    for item_a in qa_a.items:
        q_text = item_a.question if item_a.question.startswith("[CAM ") else f"[CAM A] {item_a.question}"
        merged_items.append(
            JudgeItem(
                question=q_text,
                passed=item_a.passed,
                confidence=item_a.confidence,
                reason=item_a.reason,
            )
        )
    for item_b in qa_b.items:
        q_text = item_b.question if item_b.question.startswith("[CAM ") else f"[CAM B] {item_b.question}"
        merged_items.append(
            JudgeItem(
                question=q_text,
                passed=item_b.passed,
                confidence=item_b.confidence,
                reason=item_b.reason,
            )
        )
    if cross_cam_item is not None:
        merged_items.append(cross_cam_item)
    def _item_p_true(it: JudgeItem) -> float:
        if it.passed:
            return it.confidence
        # Error/blocked items carry passed=False & confidence=0.0 -> score 0.0 (not 1.0 - 0.0 = 1.0).
        return 0.0 if it.confidence <= 0.0 else max(0.0, 1.0 - it.confidence)

    overall = (
        round(sum(_item_p_true(it) for it in merged_items) / len(merged_items), 3)
        if merged_items
        else round((qa_a.overall_score + qa_b.overall_score) / 2, 3)
    )
    return JudgeResult(
        overall_score=overall,
        all_passed=all(it.passed for it in merged_items),
        items=merged_items,
    )


def _synthesize_cam_b_45deg_keyframe_sync(
    cam_a_frame_path: Path,
    scene: Scene,
    out_path: Path,
) -> Optional[Path]:
    """
    Geometrically derive a true level +45-degree front-right novel-view keyframe for Camera B by combining
    the asymmetrical 45-degree conservatory perspective anchor (`conservatory_cam_b.png`) with Camera A's
    actual rendered frame (`cam_a_frame_path`) via `config.MODEL_IMAGE` (`gemini-nano-banana-2.1`).
    """
    if not cam_a_frame_path.is_file():
        return None
    try:
        raw_a = cam_a_frame_path.read_bytes()
        mime_a = "image/png" if cam_a_frame_path.suffix.lower() == ".png" else "image/jpeg"
        room_b_path = config.ASSETS_DIR / "stages" / "conservatory_cam_b.png"
        img_model = _resolve_image_model()
        client = genai.Client(http_options=types.HttpOptions(timeout=60_000))

        input_parts: list[dict] = []
        if room_b_path.is_file():
            raw_room_b = room_b_path.read_bytes()
            input_parts.append({"type": "image", "data": base64.b64encode(raw_room_b).decode("ascii"), "mime_type": "image/jpeg"})
            input_parts.append({"type": "image", "data": base64.b64encode(raw_a).decode("ascii"), "mime_type": mime_a})
            prompt = (
                "MULTI-VIEW 45-DEGREE CAMERA SYNTHESIS: "
                "Use IMAGE 1 strictly as the EXACT 45-DEGREE SIDE-ANGLE CAMERA PERSPECTIVE, level horizontal floor, "
                "vertical window pillars, and diagonal glass conservatory room layout (REMOVE the two empty wooden chairs "
                "from Image 1 unless chairs appear in Image 2). "
                "Place the EXACT characters, outfits, props, table items, lighting, and physical action from IMAGE 2 "
                "(Camera A front view) into IMAGE 1's exact 45-degree right-side perspective! "
                "STRICT 3D WORLD-SPACE POSITION & FACING LOCK: "
                "1) Because Image 1's camera stands +45 degrees to the front-right of Image 2's camera: "
                "silver-haired Lia stands on the LEFT side of the round table (matching her exact outfit, pose, held item, "
                "and rightward 3D facing direction from Image 2 — looking across the table toward the cat/prop, NOT staring "
                "into Camera B); the round wooden table in the center carries EVERY exact prop from Image 2 (identical vase, "
                "identical beverage/snack/phone/etc.); and small black cat Nyx sits CLOSER on the RIGHT side of the table "
                "(seen from a rear-right / side three-quarter angle facing left toward the table & Lia, matching Image 2's 3D gaze). "
                "2) Keep floor, tabletop, and all vertical mullions 100% level/vertical like Image 1 (ZERO tilt, ZERO extra chairs, "
                "full-bleed 16:9 anime frame). "
                f"Exact action shown: {scene.narration}"
            )
        else:
            input_parts.append({"type": "image", "data": base64.b64encode(raw_a).decode("ascii"), "mime_type": mime_a})
            prompt = (
                "Re-photograph this exact anime conservatory moment from a level 45-degree front-right side camera angle "
                "(right glass wall close on right, left glass wall receding diagonally left, perfectly flat horizontal floor "
                "and tabletop, vertical window pillars, Nyx closer on right foreground facing left, Lia on left facing right). "
                f"Scene: {scene.narration}"
            )
        input_parts.append({"type": "text", "text": prompt})

        interaction = client.interactions.create(
            model=img_model,
            input=input_parts,
            response_format={"type": "image", "aspect_ratio": "16:9"},
        )
        img_data = None
        output_image = getattr(interaction, "output_image", None)
        if output_image and getattr(output_image, "data", None):
            img_data = base64.b64decode(output_image.data)
        else:
            for step in getattr(interaction, "steps", []) or []:
                if getattr(step, "type", None) == "model_output":
                    for block in getattr(step, "content", []) or []:
                        if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                            img_data = base64.b64decode(block.data)
                            break
                if img_data:
                    break
        if not img_data:
            return None
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(img_data)
        log_genai_call(
            img_model,
            {"task": "cam_b_45deg_geometric_keyframe", "scene_id": scene.scene_id, "source_cam_a": str(cam_a_frame_path)},
            {"aspect_ratio": "16:9"},
            {"saved_path": str(out_path), "bytes": len(img_data)},
        )
        return out_path
    except Exception as exc:
        log_genai_call(
            _resolve_image_model(),
            {"task": "cam_b_45deg_geometric_keyframe", "scene_id": scene.scene_id},
            {"fallback": True},
            {"error": f"{type(exc).__name__}: {exc}"},
        )
        return None


def _extract_frame_at_sync(video_path: Path, t_sec: float, out_path: Path) -> Optional[Path]:
    """Save the frame at `t_sec` of `video_path` as PNG (Windows Unicode-path safe)."""
    cap, tmp_path = _open_video_capture_safe(video_path)
    try:
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t_sec * fps))))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path if _write_png_safe(out_path, frame) else None
    finally:
        cap.release()
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


_CAM_B_POS = (3.25, 3.55)  # world x, z of Camera B in the stage blueprint (about 45 degrees right of Camera A)
_FACING_VEC = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "front": (0.0, 1.0), "back": (0.0, -1.0)}


def _cam_b_blocking(scene: Scene) -> str:
    """Describe, from stage geometry, where each actor lands in Camera B's frame and which side of them it sees."""
    import math

    fx, fz = -_CAM_B_POS[0], -_CAM_B_POS[1]
    norm = math.hypot(fx, fz)
    fx, fz = fx / norm, fz / norm
    rx, rz = -fz, fx  # camera right vector
    rows = []
    for actor in scene.actors:
        wx, wz = (actor.grid[0] - 1) * 2.1, (actor.grid[1] - 1) * 1.7
        side = wx * rx + wz * rz
        depth = (wx - _CAM_B_POS[0]) * fx + (wz - _CAM_B_POS[1]) * fz
        tx, tz = _CAM_B_POS[0] - wx, _CAM_B_POS[1] - wz
        vx, vz = _FACING_VEC.get(str(actor.facing), (0.0, 1.0))
        cosang = (tx * vx + tz * vz) / (math.hypot(tx, tz) or 1.0)
        ang = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
        if ang < 30:
            view = "seen from the front"
        elif ang < 75:
            view = "seen in three-quarter front view"
        elif ang < 105:
            view = "seen in side profile"
        elif ang < 150:
            view = "seen in three-quarter back view"
        else:
            view = "seen from behind"
        where = "left of the table" if side < -0.4 else ("right of the table" if side > 0.4 else "near the table")
        dist = "close to the camera" if depth < 4.2 else "further from the camera"
        rows.append((side, f"{actor.name} is {where}, {dist}, {view}"))
    rows.sort(key=lambda r: r[0])
    if not rows:
        return ""
    return "In Camera B's frame, from left to right: " + "; ".join(r[1] for r in rows) + "."


def _derive_cam_b_keyframe_sync(
    cam_a_frame: Path, out_path: Path, size_chart: str, layout_b: Optional[Path] = None, blocking: str = ""
) -> Path:
    """Ask the image model to re-shoot one Camera A frame from Camera B's position (+45 degree orbit)."""
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=45_000))
    prompt = (
        "Image 1 is a frame from Camera A, the front wide shot of an anime stage scene. "
        "Redraw the EXACT SAME instant as seen by Camera B. Camera B stands 45 degrees to the right of Camera A on a "
        "circle around the central round table, a little lower and closer, and looks back at the table. "
        "The result must be a clearly different viewpoint from Image 1: the floor boards and the back wall now run "
        "diagonally, the right-hand glass wall of Image 1 is now straight ahead in the background, whatever stood on "
        "the right side of the stage is now nearest to the camera, and whatever stood on the left is further away. "
        "Everything in the 3D scene stays identical: the same room (same arched glass walls, lanterns, floor and furniture), "
        "the same characters with the same poses, clothes and held objects, the same props on the same floor spots, "
        "the same lighting and weather. Do not add, remove, duplicate or resize anything. "
        "There is exactly ONE of each character and each object, the same count as in Image 1: never draw a second "
        "copy of a character. "
        "Each character keeps the same body and face direction RELATIVE TO THE ROOM: whoever faces the table, a window or "
        "another character in Image 1 still faces that same thing here, so we now see them from 45 degrees further "
        "round. Never turn a character to face the new camera. "
        f"{blocking} {size_chart} "
        "The camera is on a tripod and perfectly level: wall posts and window frames are exactly vertical, no tilt. "
        "The picture fills the whole 16:9 frame, anime cel-shaded like Image 1, "
        "with no borders, no text and no inset."
    )
    parts: list[dict] = [
        {"type": "image", "data": base64.b64encode(cam_a_frame.read_bytes()).decode("ascii"), "mime_type": "image/png"}
    ]
    if layout_b and layout_b.is_file():
        parts.append(
            {"type": "image", "data": base64.b64encode(layout_b.read_bytes()).decode("ascii"), "mime_type": "image/png"}
        )
        prompt += (
            " Image 2 is a plain grey geometric layout sketch taken from Camera B's exact position. Copy its camera "
            "angle, its floor and wall perspective, and where the table and each character sit in the frame and how "
            "large they are. Ignore its flat grey look and its flat cut-out figures: all appearance, poses and "
            "lighting come from Image 1."
        )
    parts.append({"type": "text", "text": prompt})
    interaction = client.interactions.create(
        model=_resolve_image_model(),
        input=parts,
        response_format={"type": "image", "aspect_ratio": "16:9"},
    )
    output_image = getattr(interaction, "output_image", None)
    data = getattr(output_image, "data", None) if output_image else None
    if not data:
        raise RuntimeError("image model returned no Camera B keyframe")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
    return out_path


CAM_B_SAME_VIEW_THRESHOLD = 0.6


def _read_grayscale_safe(image_path: Path) -> Optional[np.ndarray]:
    """Read an image in grayscale safely even when Windows paths contain Unicode characters."""
    if not image_path.is_file():
        return None
    buf = np.frombuffer(image_path.read_bytes(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


def _same_view_score(cam_a_frame: Path, cam_b_frame: Path) -> float:
    """
    How much Camera B's frame is just a crop or zoom of Camera A's frame (0 = different view, 1 = same view).

    A real 45 degree orbit changes the whole layout, so no scaled copy of B matches anywhere inside A.
    """
    img_a = _read_grayscale_safe(cam_a_frame)
    img_b = _read_grayscale_safe(cam_b_frame)
    if img_a is None or img_b is None:
        return 0.0
    img_a = cv2.resize(img_a, (320, 180))
    img_b = cv2.resize(img_b, (320, 180))
    best = 0.0
    for scale in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
        template = cv2.resize(img_b, (int(320 * scale), int(180 * scale)))
        best = max(best, float(cv2.matchTemplate(img_a, template, cv2.TM_CCOEFF_NORMED).max()))
    return round(best, 3)


def _derive_rotated_cam_b_keyframe_sync(
    cam_a_frame: Path, out_path: Path, size_chart: str, layout_b: Optional[Path], blocking: str, attempts: int = 3
) -> float:
    """Derive Camera B's keyframe, redrawing when the result is only a crop of Camera A. Returns the same-view score kept."""
    best_score = 2.0
    best_bytes: Optional[bytes] = None
    for attempt in range(attempts):
        trial = out_path.with_name(f"{out_path.stem}_try{attempt}.png")
        try:
            _derive_cam_b_keyframe_sync(cam_a_frame, trial, size_chart, layout_b, blocking)
        except Exception:
            continue
        score = _same_view_score(cam_a_frame, trial)
        if score < best_score:
            best_score, best_bytes = score, trial.read_bytes()
        trial.unlink(missing_ok=True)
        if score < CAM_B_SAME_VIEW_THRESHOLD:
            break
    if best_bytes is None:
        raise RuntimeError("image model returned no Camera B keyframe")
    out_path.write_bytes(best_bytes)
    return best_score


def _update_cam_b_keyframe_sync(cam_b_first: Path, cam_a_last: Path, out_path: Path, size_chart: str) -> Path:
    """Make Camera B's last frame by editing its first frame: same viewpoint and room, state taken from Camera A's last frame."""
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=45_000))
    prompt = (
        "Image 1 is Camera B's view of an anime stage scene at the start of a shot. Image 2 is Camera A's front view "
        "of the same scene at the END of the shot. Edit Image 1 so it shows the end of the shot: move and re-pose the "
        "characters and props so their positions on the floor, poses, expressions and held objects match Image 2. "
        "Keep Image 1's camera viewpoint, framing, room, furniture, lighting and every object's size EXACTLY as they are; "
        "the camera does not move. Do not copy Image 2's viewpoint. Do not add or remove anything that Image 2 does not "
        f"add or remove. {size_chart} Full 16:9 frame, no borders, no text."
    )
    parts = [
        {"type": "image", "data": base64.b64encode(cam_b_first.read_bytes()).decode("ascii"), "mime_type": "image/png"},
        {"type": "image", "data": base64.b64encode(cam_a_last.read_bytes()).decode("ascii"), "mime_type": "image/png"},
        {"type": "text", "text": prompt},
    ]
    interaction = client.interactions.create(
        model=_resolve_image_model(),
        input=parts,
        response_format={"type": "image", "aspect_ratio": "16:9"},
    )
    output_image = getattr(interaction, "output_image", None)
    data = getattr(output_image, "data", None) if output_image else None
    if not data:
        raise RuntimeError("image model returned no Camera B end keyframe")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
    return out_path


async def _render_cam_b_from_keyframes(
    scene: Scene,
    cam_a_final_url: str,
    cam_a_tail_url: Optional[str],
) -> Optional[tuple[str, Optional[str], JudgeResult, int, int]]:
    """
    Render Camera B as an interpolation between two keyframes derived from Camera A's own clip.

    Camera B's first and last frames are Camera A's first and last frames re-shot from a +45 degree orbit,
    so room, poses, props and relative sizes follow Camera A at both ends of the shot.
    Returns None when any step fails so the caller can fall back to the reference-image path.
    """
    cam_a_video = _resolve_asset_to_local_path(cam_a_final_url)
    if not cam_a_video or not cam_a_video.is_file():
        return None
    stem_b = cam_a_video.stem.replace("cam_a", "cam_b") + "_kf"
    a_first = config.TAILS_DIR / f"{cam_a_video.stem}_first.png"
    a_last = _resolve_asset_to_local_path(cam_a_tail_url) if cam_a_tail_url else None
    if not a_last or not a_last.is_file():
        a_last = await asyncio.to_thread(_extract_tail_frame_sync, cam_a_video, config.TAILS_DIR / f"{cam_a_video.stem}_last.png")
    a_first_ok = await asyncio.to_thread(_extract_frame_at_sync, cam_a_video, 0.3, a_first)
    if not a_first_ok or not a_last:
        return None

    size_chart = _build_size_chart(scene)
    layout_b: Optional[Path] = None
    try:
        from backend.media.stage_blueprint import render_scene_blueprint

        layout_b = await asyncio.to_thread(
            render_scene_blueprint, scene, "cam_b", config.TAILS_DIR / f"{stem_b}_layout.png"
        )
    except Exception:
        layout_b = None  # the keyframe still works from the text description alone
    b_first = config.TAILS_DIR / f"{stem_b}_first.png"
    b_last = config.TAILS_DIR / f"{stem_b}_last.png"
    t0 = time.perf_counter()
    same_view = await asyncio.to_thread(
        _derive_rotated_cam_b_keyframe_sync, a_first, b_first, size_chart, layout_b, _cam_b_blocking(scene)
    )
    if same_view >= CAM_B_SAME_VIEW_THRESHOLD:
        return None  # never got a real orbit: let the caller use the reference-image path instead
    # The end keyframe is an edit of the start keyframe, so Camera B keeps one fixed viewpoint for the whole shot.
    await asyncio.to_thread(_update_cam_b_keyframe_sync, b_first, a_last, b_last, size_chart)
    keyframe_sec = round(time.perf_counter() - t0, 2)

    up_first, up_last = await asyncio.gather(
        _get_or_upload_uri(b_first, use_cache=False),
        _get_or_upload_uri(b_last, use_cache=False),
    )
    narration = _milden_safety_phrasing(scene.narration)
    prompt = (
        "Camera B shot: a level three-quarter side view, 45 degrees to the right of the main camera, medium close-up. "
        "Image 1 is the exact first frame and Image 2 is the exact last frame of this shot: animate smoothly from "
        "Image 1 to Image 2 with natural motion. The camera is locked on a tripod: no pan, no orbit, no zoom, no cuts. "
        f"Action to show: {narration} "
        "Keep the room, the characters, the props, their positions and their sizes exactly as drawn in the two images; "
        "do not add or remove anything. "
        f"{size_chart} "
        "Anime cel-shaded style, nighttime glass conservatory, full frame with no borders or text."
    )
    input_parts = [
        {"type": "image", "uri": up_first[0], "mime_type": up_first[1]},
        {"type": "image", "uri": up_last[0], "mime_type": up_last[1]},
        {"type": "text", "text": prompt},
    ]
    duration_int = max(3, min(10, int(round(scene.duration_sec or config.VIDEO_DURATION_SEC))))
    out_video = config.FINALS_DIR / f"{stem_b}.mp4"
    out_tail = config.TAILS_DIR / f"{stem_b}_tail.png"
    t1 = time.perf_counter()
    await asyncio.wait_for(
        asyncio.to_thread(
            _generate_omni_take_sync,
            _resolve_video_model(),
            input_parts,
            duration_int,
            config.VIDEO_RESOLUTION,
            config.VIDEO_ASPECT_RATIO,
            out_video,
        ),
        timeout=PER_TAKE_TIMEOUT_SEC,
    )
    video_sec = round(time.perf_counter() - t1, 2)
    tail_path = await asyncio.to_thread(_extract_tail_frame_sync, out_video, out_tail)
    questions = list(scene.qa_questions) or [f"Does Camera B show: {scene.narration}?"]
    qa_res = await judge(questions, context_text=prompt, media_paths=[str(out_video)])
    log_genai_call(
        model=_resolve_video_model(),
        prompt={"mode": "cam_b_from_cam_a_keyframes", "scene_id": scene.scene_id},
        config={"keyframe_sec": keyframe_sec, "video_sec": video_sec, "same_view_score": same_view},
        output={"video": out_video.name, "qa": qa_res.overall_score},
    )
    return (
        f"/assets/finals/{out_video.name}",
        f"/assets/tails/{out_tail.name}" if tail_path else None,
        qa_res,
        0,
        0,
    )


def _read_bgr_safe(image_path: Path) -> Optional[np.ndarray]:
    """Read a BGR image safely even when Windows paths contain Unicode characters."""
    if not image_path.is_file():
        return None
    buf = np.frombuffer(image_path.read_bytes(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _resolve_ffmpeg_exe() -> str:
    """Resolve a working ffmpeg binary (prefers bundled imageio_ffmpeg on Windows, falls back to PATH)."""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _compose_split_frame_sync(cam_a_frame: Path, cam_b_frame: Path, out_path: Path) -> Path:
    """Put Camera A (left) and Camera B (right) side by side in one 16:9 frame, with black bars above and below."""
    img_a = _read_bgr_safe(cam_a_frame)
    img_b = _read_bgr_safe(cam_b_frame)
    if img_a is None or img_b is None:
        raise RuntimeError("missing start frame for the split-screen render")
    canvas = np.zeros((720, 1280, 3), np.uint8)
    canvas[180:540, 0:640] = cv2.resize(img_a, (640, 360))
    canvas[180:540, 640:1280] = cv2.resize(img_b, (640, 360))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not _write_png_safe(out_path, canvas):
        raise RuntimeError("could not write the split-screen start frame")
    return out_path


def _crop_split_panel_sync(split_video: Path, right_panel: bool, out_path: Path) -> Path:
    """Cut one panel (left = Camera A, right = Camera B) out of the split-screen clip, keeping the sound."""
    ffmpeg_exe = _resolve_ffmpeg_exe()
    x_expr = "iw/2" if right_panel else "0"
    subprocess.run(
        [
            ffmpeg_exe, "-y", "-loglevel", "error", "-i", str(split_video),
            "-filter:v", f"crop=iw/2:ih/2:{x_expr}:ih/4",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(out_path),
        ],
        check=True,
        timeout=60,
    )
    return out_path


async def _render_split_screen_pair(
    scene: Scene, cam_a_start: Path, cam_b_start: Path
) -> tuple[tuple[str, Optional[str], JudgeResult, int, int], tuple[str, Optional[str], JudgeResult, int, int]]:
    """
    Render Camera A and Camera B in ONE video generation, as two panels of a split screen, then cut them apart.

    Because both views come out of the same generation, the actors do the same action at the same instant in both,
    and each camera stays where its start frame put it. Returns (camera A result, camera B result).
    """
    tag = uuid.uuid4().hex[:6]
    stem = f"scene_{scene.scene_id}_sync_{tag}"
    split_frame = await asyncio.to_thread(
        _compose_split_frame_sync, cam_a_start, cam_b_start, config.TAILS_DIR / f"{stem}_start.png"
    )
    uploaded = await _get_or_upload_uri(split_frame, use_cache=False)
    umbrella_rule = (
        "" if _umbrella_is_open(scene) else "The red umbrella stays closed and furled for the whole clip. "
    )
    prompt = (
        "Split-screen video. The frame holds two panels side by side with black bars above and below, exactly as in "
        "Image 1. Both panels show the SAME room and the SAME moment, filmed by two synchronized cameras that never move. "
        "LEFT panel = Camera A, the front wide shot. RIGHT panel = Camera B, the fixed 45 degree side shot of the same scene. "
        "Every movement happens at exactly the same instant in both panels: the same characters do the same action "
        "with the same timing, only seen from the two camera positions. Both cameras are locked on tripods: no pan, "
        "no orbit, no zoom, no cut. Keep the black bars and the vertical split exactly where they are for the whole clip. "
        f"Action in the scene: {_milden_safety_phrasing(scene.narration)} "
        f"{umbrella_rule}"
        "Every character and object keeps the size it has in Image 1, in both panels. "
        "All weather stays outside the glass; the room interior and the floor are dry. Anime cel-shaded style. "
        "No text, numbers, labels, arrows, measurement lines, captions or watermarks anywhere in the picture."
    )
    input_parts = [
        {"type": "image", "uri": uploaded[0], "mime_type": uploaded[1]},
        {"type": "text", "text": prompt},
    ]
    duration_int = max(3, min(10, int(round(scene.duration_sec or config.VIDEO_DURATION_SEC))))
    split_video = config.FINALS_DIR / f"{stem}_split.mp4"
    t0 = time.perf_counter()
    await asyncio.wait_for(
        asyncio.to_thread(
            _generate_omni_take_sync, _resolve_video_model(), input_parts, duration_int, "720p",
            config.VIDEO_ASPECT_RATIO, split_video,
        ),
        timeout=90.0,
    )
    video_sec = round(time.perf_counter() - t0, 2)
    out_a = config.FINALS_DIR / f"scene_{scene.scene_id}_cam_a_{tag}_sync.mp4"
    out_b = config.FINALS_DIR / f"scene_{scene.scene_id}_cam_b_{tag}_sync.mp4"
    await asyncio.gather(
        asyncio.to_thread(_crop_split_panel_sync, split_video, False, out_a),
        asyncio.to_thread(_crop_split_panel_sync, split_video, True, out_b),
    )
    tail_a = await asyncio.to_thread(_extract_tail_frame_sync, out_a, config.TAILS_DIR / f"{out_a.stem}_tail.png")
    tail_b = await asyncio.to_thread(_extract_tail_frame_sync, out_b, config.TAILS_DIR / f"{out_b.stem}_tail.png")
    questions = list(scene.qa_questions) or [f"Does the clip show: {scene.narration}?"]
    qa_a, qa_b = await asyncio.gather(
        judge(questions, context_text=scene.narration, media_paths=[str(out_a)]),
        judge(questions, context_text=scene.narration, media_paths=[str(out_b)]),
    )
    log_genai_call(
        model=_resolve_video_model(),
        prompt={"mode": "split_screen_pair", "scene_id": scene.scene_id},
        config={"video_sec": video_sec},
        output={"cam_a": out_a.name, "cam_b": out_b.name, "qa_a": qa_a.overall_score, "qa_b": qa_b.overall_score},
    )
    return (
        (f"/assets/finals/{out_a.name}", f"/assets/tails/{out_a.stem}_tail.png" if tail_a else None, qa_a, 0, 0),
        (f"/assets/finals/{out_b.name}", f"/assets/tails/{out_b.stem}_tail.png" if tail_b else None, qa_b, 0, 0),
    )


async def _split_pair_from_cam_a(scene: Scene, cam_a_final_url: str):
    """First scene of a chain: take Camera A's opening frame, derive Camera B's opening frame from it, render both together."""
    cam_a_video = _resolve_asset_to_local_path(cam_a_final_url)
    if not cam_a_video or not cam_a_video.is_file():
        return None
    a_first = config.TAILS_DIR / f"{cam_a_video.stem}_first.png"
    if not await asyncio.to_thread(_extract_frame_at_sync, cam_a_video, 0.3, a_first):
        return None
    layout_b: Optional[Path] = None
    try:
        from backend.media.stage_blueprint import render_scene_blueprint

        layout_b = await asyncio.to_thread(
            render_scene_blueprint, scene, "cam_b", config.TAILS_DIR / f"{cam_a_video.stem}_layout_b.png"
        )
    except Exception:
        layout_b = None
    b_first = config.TAILS_DIR / f"{cam_a_video.stem}_camb_first.png"
    same_view = await asyncio.to_thread(
        _derive_rotated_cam_b_keyframe_sync, a_first, b_first, _build_size_chart(scene), layout_b, _cam_b_blocking(scene)
    )
    if same_view >= CAM_B_SAME_VIEW_THRESHOLD:
        return None
    return await _render_split_screen_pair(scene, a_first, b_first)


@log_function_call
async def render_scene_media(
    scene: Scene,
    reference_images: list[str],
    cam_a_preview_path: Optional[str] = None,
    cam_b_preview_path: Optional[str] = None,
    prev_tail_a: Optional[str] = None,
    prev_tail_b: Optional[str] = None,
    tail_inspector: Optional[Callable[[int, str], Awaitable[str]]] = None,
    overwrite_canonical: bool = False,
    fast_live_mode: bool = False,
    prop_descriptions: Optional[dict[str, str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> SceneMedia:
    """Render Camera A then derive Camera B (+45 deg orbit), emitting granular SSE progress phases."""
    def _emit(phase: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(phase)
            except Exception:
                pass

    preview_a = cam_a_preview_path or scene.media.cam_a_preview_url
    preview_b = cam_b_preview_path or scene.media.cam_b_preview_url

    # Step 1: Synchronized 1-pass dual-camera split-screen render when both opening/tail frames exist.
    split_pair = None
    live_keyframe_enabled = (
        config.CAM_B_KEYFRAMES_FROM_A
        and not fast_live_mode
        and not config.DEMO_MODE
        and bool(config.GEMINI_API_KEY)
    )
    opening_a = config.ASSETS_DIR / "stages" / "opening_cam_a.png"
    opening_b = config.ASSETS_DIR / "stages" / "opening_cam_b.png"
    use_opening = scene.scene_id == 1 and not (prev_tail_a and prev_tail_b) and opening_a.is_file() and opening_b.is_file()
    if live_keyframe_enabled and ((prev_tail_a and prev_tail_b) or use_opening):
        _emit("rendering_split_screen")
        try:
            start_a = opening_a if use_opening else _resolve_asset_to_local_path(prev_tail_a)
            start_b = opening_b if use_opening else _resolve_asset_to_local_path(prev_tail_b)
            if start_a and start_b and start_a.is_file() and start_b.is_file():
                split_pair = await _render_split_screen_pair(scene, start_a, start_b)
        except Exception as exc:
            log_genai_call(
                model=_resolve_video_model(),
                prompt={"mode": "split_screen_pair", "scene_id": scene.scene_id},
                config={},
                output={"fallback": f"{type(exc).__name__}: {str(exc)[:200]}"},
            )
            split_pair = None

    if split_pair is not None:
        res_a = split_pair[0]
    else:
        _emit("rendering_cam_a")
        res_a = await render_single_camera_shot(
            scene,
            "cam_a",
            prev_tail_a,
            reference_images,
            preview_path=preview_a,
            overwrite_canonical=overwrite_canonical,
            fast_live_mode=fast_live_mode,
            prop_descriptions=prop_descriptions,
        )

    # Step 2: If split-screen wasn't used yet, derive a synchronized split-screen pair from Camera A's
    # opening frame (standard mode), falling back cleanly to the single +45 deg tripod start keyframe.
    res_b_keyframed = split_pair[1] if split_pair else None
    if split_pair is None and live_keyframe_enabled and res_a[0]:
        _emit("deriving_cam_b_keyframes")
        try:
            first_pair = await _split_pair_from_cam_a(scene, res_a[0])
            if first_pair:
                res_a, res_b_keyframed = first_pair
        except Exception as exc:  # any failure falls back to the single-keyframe/reference path below
            log_genai_call(
                model=_resolve_video_model(),
                prompt={"mode": "cam_b_from_cam_a_keyframes", "scene_id": scene.scene_id},
                config={},
                output={"fallback": f"{type(exc).__name__}: {str(exc)[:200]}"},
            )
            res_b_keyframed = None

    if res_b_keyframed is not None:
        res_b = res_b_keyframed
    else:
        _emit("rendering_cam_b")
        cam_b_refs = list(reference_images)
        cam_b_start_frame = prev_tail_b
        cam_b_extra = ""
        derived_keyframe: Optional[Path] = None
        if res_a[1]:
            cam_a_local = _resolve_asset_to_local_path(res_a[1])
            if (not config.DEMO_MODE) and config.GEMINI_API_KEY and cam_a_local is not None:
                kf_out = config.PREVIEWS_DIR / f"scene_{scene.scene_id}_cam_b_45deg_{uuid.uuid4().hex[:6]}.png"
                derived_keyframe = await asyncio.to_thread(_synthesize_cam_b_45deg_keyframe_sync, cam_a_local, scene, kf_out)
                if derived_keyframe is not None:
                    cam_b_start_frame = str(derived_keyframe)
            if derived_keyframe is not None:
                # Do NOT include 0-degree frontal Cam A in cam_b_refs when +45deg start keyframe succeeded,
                # otherwise Omni Flash drifts back to 0-degree frontal framing!
                cam_b_extra = (
                    "LOCKED +45-DEGREE STATIC TRIPOD CAMERA: start directly from the +45-degree diagonal side-angle "
                    "starting frame (Image 1) and DO NOT pan, orbit, or drift back to a frontal symmetrical view! "
                    "Keep the exact +45-degree asymmetrical room perspective, level floor/tabletop, vertical window pillars, "
                    "and exact character positions & 3D facing directions throughout all 4 seconds while animating the scene action."
                )
            else:
                cam_b_refs = [res_a[1]] + cam_b_refs
                cam_b_extra = (
                    "GEOMETRIC DERIVATION FROM CAMERA A (+45-DEGREE RIGHT-SIDE ORBIT): keep the EXACT +45-degree diagonal "
                    "camera perspective matching Camera A's exact room architecture, ceiling arches, lanterns, furniture, "
                    "table props, lighting, and exact 3D world-space character positions & head/face directions."
                )
        try:
            res_b = await render_single_camera_shot(
                scene,
                "cam_b",
                cam_b_start_frame,
                cam_b_refs,
                preview_path=preview_b,
                extra_constraint=cam_b_extra,
                overwrite_canonical=overwrite_canonical,
                fast_live_mode=fast_live_mode,
                prop_descriptions=prop_descriptions,
            )
        finally:
            if derived_keyframe is not None:
                derived_keyframe.unlink(missing_ok=True)

    if not fast_live_mode:
        _emit("cross_cam_qa")
    cross_item = None if fast_live_mode else await _check_cross_camera_consistency(scene, res_a[1], res_b[1])
    # If cameras contradict each other (< CROSS_CAM_THRESHOLD=0.80 on any check in standard quality mode),
    # retry ONLY the weaker camera once conditioned on the stronger camera's tail frame (with regression guard).
    # A Camera B clip derived from Camera A's own frames is already pinned to A at both ends;
    # re-rendering it independently here would throw that away, so the retry only applies to the old path.
    if (not fast_live_mode) and res_b_keyframed is None and cross_item is not None and not cross_item.passed:
        if res_b[2].overall_score <= res_a[2].overall_score:
            conditioned_refs = list(reference_images) + ([res_a[1]] if res_a[1] else [])
            cand_b = await render_single_camera_shot(
                scene,
                "cam_b",
                prev_tail_b,
                conditioned_refs,
                preview_path=preview_b,
                extra_constraint=(
                    "Match Camera A's exact nighttime conservatory lighting, character wardrobe, placement around the central "
                    "round table, and crystal vase intact/broken state from a true 45-degree diagonal side close-up angle."
                ),
                overwrite_canonical=overwrite_canonical,
                prop_descriptions=prop_descriptions,
            )
            if cand_b[0]:
                cand_cross = await _check_cross_camera_consistency(scene, res_a[1], cand_b[1])
                if (cand_cross is not None and cand_cross.passed) or cand_b[2].overall_score >= res_b[2].overall_score:
                    res_b = cand_b
                    cross_item = cand_cross
        else:
            conditioned_refs = list(reference_images) + ([res_b[1]] if res_b[1] else [])
            cand_a = await render_single_camera_shot(
                scene,
                "cam_a",
                prev_tail_a,
                conditioned_refs,
                preview_path=preview_a,
                extra_constraint=(
                    "Match Camera B's exact nighttime conservatory lighting, character wardrobe, placement around the central "
                    "round table, and crystal vase intact/broken state from a wide front-center establishing full-stage angle."
                ),
                overwrite_canonical=overwrite_canonical,
                prop_descriptions=prop_descriptions,
            )
            if cand_a[0]:
                cand_cross = await _check_cross_camera_consistency(scene, cand_a[1], res_b[1])
                if (cand_cross is not None and cand_cross.passed) or cand_a[2].overall_score >= res_a[2].overall_score:
                    res_a = cand_a
                    cross_item = cand_cross

    merged_qa = _merge_dual_cam_qa(res_a[2], res_b[2], cross_item)
    avg_score = round((res_a[2].overall_score + res_b[2].overall_score) / 2, 3)
    both_have_video = bool(res_a[0] and res_b[0])
    media = SceneMedia(
        cam_a_preview_url=scene.media.cam_a_preview_url,
        cam_b_preview_url=scene.media.cam_b_preview_url,
        cam_a_final_url=res_a[0] or None,
        cam_b_final_url=res_b[0] or None,
        cam_a_tail_frame_url=res_a[1],
        cam_b_tail_frame_url=res_b[1],
        qa_result=merged_qa,
        qa_score=avg_score,
        qa_retries=max(res_a[4], res_b[4]),
        selected_candidate_index=int(res_a[3]),
        fallback_to_preview=(not both_have_video) or (avg_score < config.QA_PASS_THRESHOLD),
    )
    if tail_inspector and media.cam_a_tail_frame_url:
        local_tail = _resolve_asset_to_local_path(media.cam_a_tail_frame_url)
        inspector_arg = str(local_tail) if local_tail else media.cam_a_tail_frame_url
        scene.environment_notes = await tail_inspector(scene.scene_id, inspector_arg)
    return media


@log_function_call
async def render_branch_scenes(
    branch: BranchState,
    tail_inspector: Optional[Callable[[int, str], Awaitable[str]]] = None,
    fast_live_mode: bool = False,
    prop_descriptions: Optional[dict[str, str]] = None,
) -> BranchState:
    """Concurrently render all scenes with `needs_rerender=True` in `branch` anchored on 2.5D previews."""
    ref_images = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
    ] + [p.image_url for p in branch.props_registry.values()]
    merged_prop_descs = {
        pid: (p.appearance_summary or p.name)
        for pid, p in branch.props_registry.items()
    }
    if prop_descriptions:
        merged_prop_descs.update(prop_descriptions)

    target_scenes = [s for s in branch.scenes if s.needs_rerender]
    for s in branch.scenes:
        _update_render_progress(
            branch.branch_id,
            s.scene_id,
            "queued" if s.needs_rerender else "reused",
        )

    async def _run_scene(scene: Scene) -> None:
        # Three-layer consistency rule: anchor on each scene's own 2.5D preview,
        # NEVER chaining from the previous generated clip's tail frame.
        _update_render_progress(branch.branch_id, scene.scene_id, "rendering")
        scene.media = await render_scene_media(
            scene,
            ref_images,
            cam_a_preview_path=scene.media.cam_a_preview_url,
            cam_b_preview_path=scene.media.cam_b_preview_url,
            tail_inspector=tail_inspector,
            fast_live_mode=fast_live_mode,
            prop_descriptions=merged_prop_descs or None,
        )
        _update_render_progress(
            branch.branch_id,
            scene.scene_id,
            "fallback" if scene.media.fallback_to_preview else "done",
            qa_score=scene.media.qa_score,
            fallback=scene.media.fallback_to_preview,
        )

    await asyncio.gather(*(_run_scene(s) for s in target_scenes))
    if branch.branch_id in _RENDER_PROGRESS:
        set_branch_progress_status(branch.branch_id, "completed")
    return branch


def _has_audio_stream(ffmpeg_exe: str, video_path: Path) -> bool:
    """Detect whether `video_path` contains an audio stream via ffmpeg probe."""
    proc = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-i", str(video_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return "Audio:" in (proc.stderr or "")


def export_branch_movie_sync(branch: BranchState, mode: str = "cam_a") -> dict:
    """
    Stitch all rendered clips in `branch` (ordered by `scene_id`) into a single
    downloadable H.264+AAC `.mp4` movie (`cam_a`, `cam_b`, or `dual_cam` side-by-side).
    """
    import imageio_ffmpeg

    norm_mode = mode if mode in ("cam_a", "cam_b", "dual_cam") else "cam_a"
    ordered = sorted(branch.scenes, key=lambda s: s.scene_id)
    clip_pairs: list[tuple[int, Path, Optional[Path]]] = []
    for scene in ordered:
        pa = _resolve_asset_to_local_path(scene.media.cam_a_final_url)
        pb = _resolve_asset_to_local_path(scene.media.cam_b_final_url)
        if norm_mode == "cam_a" and pa is not None:
            clip_pairs.append((scene.scene_id, pa, None))
        elif norm_mode == "cam_b" and (pb or pa) is not None:
            clip_pairs.append((scene.scene_id, pb or pa, None))  # type: ignore[arg-type]
        elif norm_mode == "dual_cam" and pa is not None:
            clip_pairs.append((scene.scene_id, pa, pb or pa))

    if not clip_pairs:
        raise ValueError(f"Branch {branch.branch_id} has no rendered final clips to export")

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    safe_branch = re.sub(r"[^a-zA-Z0-9_-]", "_", branch.branch_id)
    out_path = config.FINALS_DIR / f"export_{safe_branch}_{norm_mode}.mp4"

    cmd: list[str] = [ffmpeg_exe, "-y"]
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    input_idx = 0

    for seg_i, (_sid, path_a, path_b) in enumerate(clip_pairs):
        cmd.extend(["-i", str(path_a)])
        idx_a = input_idx
        input_idx += 1
        has_aud = _has_audio_stream(ffmpeg_exe, path_a)

        if norm_mode == "dual_cam" and path_b is not None:
            cmd.extend(["-i", str(path_b)])
            idx_b = input_idx
            input_idx += 1
            filter_parts.append(
                f"[{idx_a}:v]scale=640:360:force_original_aspect_ratio=decrease,"
                f"pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[va{seg_i}]"
            )
            filter_parts.append(
                f"[{idx_b}:v]scale=640:360:force_original_aspect_ratio=decrease,"
                f"pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[vb{seg_i}]"
            )
            filter_parts.append(f"[va{seg_i}][vb{seg_i}]hstack=inputs=2[v{seg_i}]")
        else:
            filter_parts.append(
                f"[{idx_a}:v]scale=640:360:force_original_aspect_ratio=decrease,"
                f"pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[v{seg_i}]"
            )

        if has_aud:
            filter_parts.append(
                f"[{idx_a}:a]aresample=48000,aformat=channel_layouts=stereo[a{seg_i}]"
            )
        else:
            filter_parts.append(f"anullsrc=r=48000:cl=stereo:d=4.0[a{seg_i}]")
        concat_inputs.append(f"[v{seg_i}][a{seg_i}]")

    n_seg = len(clip_pairs)
    filter_parts.append(f"{''.join(concat_inputs)}concat=n={n_seg}:v=1:a=1[vout][aout]")
    filter_complex = ";".join(filter_parts)

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"ffmpeg export failed (rc={proc.returncode}): {(proc.stderr or '')[-400:]}")

    return {
        "branch_id": branch.branch_id,
        "mode": norm_mode,
        "scenes_stitched": [sid for sid, _, _ in clip_pairs],
        "scene_count": n_seg,
        "duration_est_sec": round(n_seg * float(config.VIDEO_DURATION_SEC), 1),
        "video_url": f"/assets/finals/{out_path.name}",
        "bytes": out_path.stat().st_size,
        "is_h264": is_browser_h264(out_path),
    }


async def export_branch_movie(branch: BranchState, mode: str = "cam_a") -> dict:
    """Async wrapper running ffmpeg branch movie stitch off the event loop."""
    return await asyncio.to_thread(export_branch_movie_sync, branch, mode)
