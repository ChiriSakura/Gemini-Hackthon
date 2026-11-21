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
import json
import shutil
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
from backend.director.rule_memory import add_rule
from backend.logger import log_function_call, log_genai_call
from backend.media.prop_maker import estimate_prop_height_m
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
CROSS_CAM_THRESHOLD = 0.65
EVAL_STATS_PATH = config.ASSETS_DIR / "eval_media_stats.jsonl"

# In-memory cache mapping resolved local asset path -> (file_uri, mime_type).
_UPLOAD_CACHE: dict[str, tuple[str, str]] = {}
_UPLOAD_LOCK = asyncio.Lock()

# In-memory render progress tracker for parallel render progress polling (W1-T3B).
_RENDER_PROGRESS: dict[str, dict] = {}


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
) -> None:
    """Update per-scene progress metadata for a branch render job."""
    entry = _RENDER_PROGRESS.setdefault(
        branch_id,
        {"branch_id": branch_id, "status": "running", "updated_at": time.time(), "scenes": {}},
    )
    scene_entry = entry["scenes"].setdefault(str(scene_id), {"scene_id": scene_id, "status": status})
    scene_entry["status"] = status
    if qa_score is not None:
        scene_entry["qa_score"] = qa_score
    if fallback is not None:
        scene_entry["fallback_to_preview"] = fallback
    entry["updated_at"] = round(time.time(), 2)


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


async def _get_or_upload_uri(path: Path, use_cache: bool = True) -> tuple[str, str]:
    """Upload a local asset once and reuse its Files API URI across concurrent takes."""
    key = str(path.resolve())
    if use_cache and key in _UPLOAD_CACHE:
        return _UPLOAD_CACHE[key]
    async with _UPLOAD_LOCK:
        if use_cache and key in _UPLOAD_CACHE:
            return _UPLOAD_CACHE[key]
        uri, mime = await asyncio.to_thread(_upload_file_sync, path)
        if use_cache:
            _UPLOAD_CACHE[key] = (uri, mime)
        return uri, mime


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
                candidate = config.PREVIEWS_DIR / f"{preview_video.stem}_extracted_start.png"
                if _write_png_safe(candidate, frame):
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


def _describe_prop_with_scale(prop_id: str, state: str = "intact", on_floor: bool = False) -> str:
    """Return a concise, physically anchored natural description for a single prop."""
    if prop_id == "red_umbrella":
        return (
            "ONE single closed crimson red long-handle umbrella (~0.86m) lying flat on the floor"
            if on_floor
            else "ONE single open crimson red long-handle umbrella (~0.86m)"
        )
    if prop_id == "crystal_vase":
        return (
            "shattered clear crystal vase shards and scattered blue rose petals lying flat on the wet floor"
            if state == "broken" or on_floor
            else "ONE single intact clear crystal vase (~0.32m) holding ONE glowing blue rose resting squarely on the center round table"
        )
    if prop_id == "star_token":
        return "ONE single small palm-sized (~0.10m) glowing golden star keepsake"
    h_m = estimate_prop_height_m(prop_id)
    loc = "resting flat on the floor" if on_floor else "resting on the center round table"
    return f"ONE single {prop_id.replace('_', ' ')} (~{h_m:.2f}m tall, {loc}, state={state})"


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


def _build_shot_prompt(
    scene: Scene,
    cam_id: str,
    has_first_frame: bool,
    has_preview_video: bool,
    extra_constraint: str = "",
) -> str:
    """Build a natural-language Omni Flash prompt enforcing camera angle, nighttime continuity, and SI scale."""
    if cam_id == "cam_a":
        cam_desc = (
            "Camera A WIDE ESTABLISHING front-center full-stage shot (IGNORE any mention of Camera B or close-up in narration): "
            "pull the camera back to show the entire conservatory floor, all three arched glass walls, the central 0.75m round "
            "cafe table ALWAYS clearly visible in the middle, and full bodies of all characters from head to shoes"
        )
    else:
        cam_desc = (
            "Camera B STRONG 45-DEGREE DIAGONAL SIDE CLOSE-UP from the front-right corner looking obliquely across "
            "the central round table toward the girl and cat (IGNORE any mention of Camera A wide shot in narration): "
            "clear three-quarter diagonal perspective depth, noticeably tighter medium close-up framing than Camera A (NEVER flat front-on)"
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
        desc = f"{a.name} ({'silver-haired anime girl in starry midnight-blue cloak' if a.id == 'lia' else 'small realistic black cat with golden moon collar'}) positioned {blocking}, facing {a.facing}, performing {a.action}"
        if a.holding_prop:
            held_prop_ids.add(a.holding_prop)
            desc += f" holding {_describe_prop_with_scale(a.holding_prop, state='held', on_floor=False)}"
        actor_phrases.append(desc)

    # Deduplicate props already described on an actor so Omni Flash never draws 2 umbrellas or 2 stars.
    prop_phrases: list[str] = []
    seen_props: set[str] = set()
    for p in scene.stage_props:
        if p.id in held_prop_ids or p.id in seen_props:
            continue
        seen_props.add(p.id)
        blocking = _grid_to_blocking(p.grid)
        prop_phrases.append(f"{_describe_prop_with_scale(p.id, state=p.state, on_floor=p.on_floor)} ({blocking})")

    parts = [
        config.STYLE_ANCHOR_PROMPT,
        "Full-bleed borderless anime cinema frame inside a nighttime rooftop glass conservatory — NO black borders, NO coordinate axes, NO tick marks, NO numbers, NO text or captions.",
        f"{cam_desc}.",
        f"Story action: {scene.narration}",
        f"Time & lighting: {lighting_desc}.",
        f"Audio & SFX mood: {scene.audio_mood or scene.vocal_emotion}.",
    ]
    if actor_phrases:
        parts.append("Characters: " + "; ".join(actor_phrases) + ".")
    if prop_phrases:
        parts.append("Stage props: " + "; ".join(prop_phrases) + ".")
    if scene.environment_notes:
        clean_notes = scene.environment_notes.replace("warm_sunlight_streaming", "warm_night_lantern_glow").replace("lush_trees_outside", "dark_night_trees_outside")
        parts.append(f"Persistent scene details: {clean_notes}.")
    if scene.camera_shake:
        parts.append("Include a subtle dramatic camera shake on impact.")
    if extra_constraint:
        parts.append(f"Strict visual constraints: {extra_constraint}.")
    if has_first_frame:
        parts.append("Use the first image as the starting frame for seamless spatial continuity.")
    if has_preview_video:
        parts.append("Transform the stage motion from the preview video into rich anime cinema style while preserving exact character placement.")
    else:
        parts.append("Match character and prop designs strictly to the provided reference images, with ZERO extra unintended characters.")

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
) -> tuple[Optional[Path], Optional[Path], JudgeResult, float]:
    """Generate one candidate take (60s timeout guard), extract tail frame, and score via Decisions API."""
    out_video = config.FINALS_DIR / f"scene_{scene.scene_id}_{cam_id}_r{attempt}_take{take_idx}.mp4"
    out_tail = config.TAILS_DIR / f"scene_{scene.scene_id}_{cam_id}_r{attempt}_take{take_idx}_tail.png"
    prompt = _build_shot_prompt(
        scene=scene,
        cam_id=cam_id,
        has_first_frame=uploaded_prev_tail is not None,
        has_preview_video=uploaded_preview is not None,
        extra_constraint=extra_constraint,
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
            # If video-edit mode rejects preview duration/format (400), retry once with image-start anchor only.
            if uploaded_preview is not None and ("400" in str(first_exc) or "duration" in str(first_exc).lower()):
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
        err_msg = f"{type(exc).__name__}: {exc}"
        log_genai_call(
            model_id,
            {"prompt": prompt},
            {"cam_id": cam_id, "take_idx": take_idx, "attempt": attempt, "timeout_sec": PER_TAKE_TIMEOUT_SEC},
            {"error": err_msg, "elapsed_sec": elapsed},
        )
        questions = list(scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"])
        failed_items = [JudgeItem(question=q, passed=False, confidence=0.0, reason=err_msg) for q in questions]
        return None, None, JudgeResult(overall_score=0.0, all_passed=False, items=failed_items), 0.0

    questions = list(scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"])
    if PHYSICAL_QA_QUESTION not in questions:
        questions.append(PHYSICAL_QA_QUESTION)
    if ARTIFACT_FREE_QA_QUESTION not in questions:
        questions.append(ARTIFACT_FREE_QA_QUESTION)

    media_paths = [str(out_video)]
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
) -> tuple[str, Optional[str], JudgeResult, int, int]:
    """
    Generate 2 parallel Omni Flash candidates for one scene + camera, score via `judge()` +
    `score_clip_alignment()`, retry up to `config.MAX_QA_RETRIES` with corrective constraints
    on failure (distilling successful fixes into `rule_memory`), log 1-take vs 2-take metrics,
    and return `(final_url, tail_frame_url, judge_result, selected_idx, retries)`.
    """
    model_id = _resolve_video_model()
    prompt = _build_shot_prompt(
        scene=scene,
        cam_id=cam_id,
        has_first_frame=bool(prev_tail_path),
        has_preview_video=bool(preview_path),
        extra_constraint=extra_constraint,
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

    start_frame_path = local_preview_img or local_prev_tail
    max_refs = 5 if start_frame_path else 6
    seen_paths: set[str] = set()
    local_refs: list[Path] = []
    for ref in reference_images:
        p = _resolve_asset_to_local_path(ref)
        if p:
            p = _resolve_clean_actor_ref(p, scene=scene)
        if p and str(p.resolve()) not in seen_paths:
            seen_paths.add(str(p.resolve()))
            local_refs.append(p)
        if len(local_refs) >= max_refs:
            break

    uploaded_prev_tail = await _get_or_upload_uri(start_frame_path, use_cache=False) if start_frame_path else None
    uploaded_refs = list(await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in local_refs)))
    uploaded_preview = await _get_or_upload_uri(local_preview, use_cache=False) if local_preview else None

    current_constraint = extra_constraint
    retries = 0
    best_video: Optional[Path] = None
    best_tail: Optional[Path] = None
    best_qa: Optional[JudgeResult] = None
    best_combined = 0.0
    best_idx = 0
    first_take0_score: Optional[float] = None
    first_take1_score: Optional[float] = None
    last_repair_note: str = ""
    temp_artifacts: list[Path] = []
    if extracted_preview_img is not None:
        temp_artifacts.append(extracted_preview_img)

    for attempt in range(config.MAX_QA_RETRIES + 1):
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
                )
                for idx in range(config.DUAL_TAKE_COUNT)
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

        # Pick candidate maximizing combined QA + Decisions alignment score (among valid videos).
        winner_idx, winner = max(
            enumerate(takes),
            key=lambda pair: (pair[1][0] is not None, pair[1][3]),
        )
        best_video, best_tail, best_qa, best_combined = winner
        best_idx = winner_idx

        if best_qa.overall_score >= config.QA_PASS_THRESHOLD or attempt >= config.MAX_QA_RETRIES:
            break

        retries += 1
        repair_note = await write_repair_constraint(scene, best_qa)
        last_repair_note = repair_note
        current_constraint = f"{current_constraint} {repair_note}".strip()

    assert best_qa is not None
    if retries > 0 and best_qa.overall_score >= config.QA_PASS_THRESHOLD and last_repair_note:
        add_rule(f"Scene {scene.scene_id} ({cam_id}) continuity fix: {last_repair_note}")

    _append_eval_media_stat(
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scene_id": scene.scene_id,
            "cam_id": cam_id,
            "take_0_score": first_take0_score,
            "take_1_score": first_take1_score,
            "selected_idx": best_idx,
            "selected_qa_score": best_qa.overall_score,
            "selected_combined_score": best_combined,
            "repaired_score": best_qa.overall_score if retries > 0 else None,
            "retries": retries,
            "passed": best_qa.overall_score >= config.QA_PASS_THRESHOLD,
        }
    )

    canonical_video = config.FINALS_DIR / f"scene_{scene.scene_id}_{cam_id}{run_tag}.mp4"
    canonical_tail = config.TAILS_DIR / f"scene_{scene.scene_id}_{cam_id}{run_tag}_tail.png"

    final_url: Optional[str] = None
    tail_url: Optional[str] = None
    if best_video and best_video.is_file():
        shutil.copy2(best_video, canonical_video)
        final_url = f"/assets/finals/{canonical_video.name}"
    if best_tail and best_tail.is_file():
        shutil.copy2(best_tail, canonical_tail)
        tail_url = f"/assets/tails/{canonical_tail.name}"

    for artifact in temp_artifacts:
        if artifact not in {canonical_video, canonical_tail}:
            artifact.unlink(missing_ok=True)

    return final_url or "", tail_url, best_qa, best_idx, retries


async def _check_cross_camera_consistency(
    scene: Scene,
    cam_a_tail_url: Optional[str],
    cam_b_tail_url: Optional[str],
) -> Optional[JudgeItem]:
    """Fast (~0.9s) Decisions API check comparing Camera A & Camera B tail frames for spatial/prop consistency."""
    if config.DEMO_MODE or not config.GEMINI_API_KEY or not config.USE_DECISIONS_API:
        return None
    path_a = _resolve_asset_to_local_path(cam_a_tail_url)
    path_b = _resolve_asset_to_local_path(cam_b_tail_url)
    if not path_a or not path_b:
        return None
    question = (
        "Do Camera A (wide front view, first image) and Camera B (45-deg side close-up, second image) "
        f"consistently depict the same scene ({scene.lighting} nighttime mood, same characters, same vase intact/broken state)?"
    )
    try:
        payload = build_decision_input(f"Cross-camera check for Scene {scene.scene_id}: {scene.narration}", [path_a, path_b])
        res = await create_decision(payload, {"dual_cam": {"type": "noul", "instructions": question}})
        ans = (getattr(res, "answers", None) or {}).get("dual_cam")
        p_true = float(getattr(ans, "noul", 0.5)) if ans is not None else 0.5
        conf = float(getattr(ans, "confidence", max(p_true, 1.0 - p_true))) if ans is not None else 0.5
        return JudgeItem(
            question="[Dual-Cam] Camera A & Camera B spatial, prop, and nighttime lighting consistency",
            passed=p_true >= 0.5,
            confidence=round(conf, 3),
            reason=f"Cross-Cam Decision-1 P(true)={p_true:.2f}",
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
    overall = (
        round(
            sum(it.confidence if it.passed else (1.0 - it.confidence) for it in merged_items) / len(merged_items),
            3,
        )
        if merged_items
        else round((qa_a.overall_score + qa_b.overall_score) / 2, 3)
    )
    return JudgeResult(
        overall_score=overall,
        all_passed=all(it.passed for it in merged_items),
        items=merged_items,
    )


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
) -> SceneMedia:
    """Concurrently render Camera A and Camera B, run cross-camera consistency QA, and update tail notes."""
    preview_a = cam_a_preview_path or scene.media.cam_a_preview_url
    preview_b = cam_b_preview_path or scene.media.cam_b_preview_url

    res_a, res_b = await asyncio.gather(
        render_single_camera_shot(
            scene,
            "cam_a",
            prev_tail_a,
            reference_images,
            preview_path=preview_a,
            overwrite_canonical=overwrite_canonical,
        ),
        render_single_camera_shot(
            scene,
            "cam_b",
            prev_tail_b,
            reference_images,
            preview_path=preview_b,
            overwrite_canonical=overwrite_canonical,
        ),
    )

    cross_item = await _check_cross_camera_consistency(scene, res_a[1], res_b[1])
    # If cameras contradict each other, retry ONLY the weaker camera once with alignment constraint.
    if cross_item is not None and not cross_item.passed and cross_item.confidence >= CROSS_CAM_THRESHOLD:
        if res_b[2].overall_score <= res_a[2].overall_score:
            res_b = await render_single_camera_shot(
                scene,
                "cam_b",
                prev_tail_b,
                reference_images,
                preview_path=preview_b,
                extra_constraint=(
                    "Match Camera A's exact nighttime conservatory lighting, character placement around the central round table, "
                    "and crystal vase intact/broken state from a true 45-degree diagonal side close-up angle."
                ),
                overwrite_canonical=overwrite_canonical,
            )
        else:
            res_a = await render_single_camera_shot(
                scene,
                "cam_a",
                prev_tail_a,
                reference_images,
                preview_path=preview_a,
                extra_constraint=(
                    "Match Camera B's exact nighttime conservatory lighting, character placement around the central round table, "
                    "and crystal vase intact/broken state from a wide front-center establishing full-stage angle."
                ),
                overwrite_canonical=overwrite_canonical,
            )
        cross_item = await _check_cross_camera_consistency(scene, res_a[1], res_b[1])

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
) -> BranchState:
    """Concurrently render all scenes with `needs_rerender=True` in `branch` anchored on 2.5D previews."""
    ref_images = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
    ] + [p.image_url for p in branch.props_registry.values()]

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
        _RENDER_PROGRESS[branch.branch_id]["status"] = "completed"
        _RENDER_PROGRESS[branch.branch_id]["updated_at"] = round(time.time(), 2)
    return branch
