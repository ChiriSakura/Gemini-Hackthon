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
from backend.director.hydrogen_judge import judge, score_clip_alignment, write_repair_constraint
from backend.director.rule_memory import add_rule
from backend.logger import log_function_call, log_genai_call
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
EVAL_STATS_PATH = config.ASSETS_DIR / "eval_media_stats.jsonl"

# In-memory cache mapping resolved local asset path -> (file_uri, mime_type).
_UPLOAD_CACHE: dict[str, tuple[str, str]] = {}
_UPLOAD_LOCK = asyncio.Lock()

# In-memory render progress tracker for parallel render progress polling (W1-T3B).
_RENDER_PROGRESS: dict[str, dict] = {}


def is_browser_h264(video_path: Path) -> bool:
    """Verify an MP4 file uses H.264/avc1 codec natively playable in Chrome <video> tags."""
    if not video_path.is_file():
        return False
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return False
    try:
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).lower()
        return fourcc in {"h264", "avc1", "x264"}
    finally:
        cap.release()


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


def _extract_tail_frame_sync(video_path: Path, tail_path: Path) -> Optional[Path]:
    """Extract the final readable video frame as a PNG image using OpenCV."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
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
        tail_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(tail_path), last_frame)
        return tail_path
    finally:
        cap.release()


def _build_shot_prompt(
    scene: Scene,
    cam_id: str,
    has_first_frame: bool,
    has_preview_video: bool,
    extra_constraint: str = "",
) -> str:
    """Build an Omni Flash prompt enforcing camera angle, SI metric scale, gravity, and props."""
    cam_desc = (
        "Camera A front-wide full-stage shot showing the entire 3x3 glass conservatory grid"
        if cam_id == "cam_a"
        else "Camera B 45-degree side close-up framing the characters and central table props"
    )
    lighting_desc = {
        "warm_dim": "warm dim lantern light at rainy night",
        "cold_blue": "cold stormy blue rainy night lighting",
        "warm_transition": "soft warm golden light breaking through rain",
        "warm_gold_snow": "warm golden starlight with gentle falling snow",
    }.get(scene.lighting, scene.lighting)

    actors_desc = ", ".join(
        f"{a.name} ({a.id}) at grid {a.grid} facing {a.facing} doing {a.action}"
        + (f" holding {a.holding_prop}" if a.holding_prop else "")
        for a in scene.actors
    )
    props_desc = ", ".join(
        f"{p.id} at grid {p.grid} ({'on floor' if p.on_floor else 'on table'}, state={p.state})"
        for p in scene.stage_props
    )

    parts = [
        config.STYLE_ANCHOR_PROMPT,
        f"2.5D anime glass conservatory stage animation. {cam_desc}.",
        f"Action & narration: {scene.narration}",
        f"Lighting: {lighting_desc}.",
        f"Audio & SFX mood: {scene.audio_mood or scene.vocal_emotion}.",
    ]
    if actors_desc:
        parts.append(f"Actors: {actors_desc}.")
    if props_desc:
        parts.append(f"Props: {props_desc}.")
    if scene.environment_notes:
        parts.append(f"Persistent environment details: {scene.environment_notes}.")
    if scene.camera_shake:
        parts.append("Include a subtle dramatic camera shake on impact.")
    if extra_constraint:
        parts.append(f"Strict visual constraints: {extra_constraint}.")
    if has_first_frame:
        parts.append("Use the first image as the starting frame for seamless continuity.")
    if has_preview_video:
        parts.append("Transform the stage motion from the preview video into rich 2.5D anime style while preserving exact spatial layout.")
    else:
        parts.append("Match character and prop appearances strictly to the provided reference images, with no extra unintended characters.")

    parts.append(
        "Physical scale & gravity: Lia is a 1.62m teenage girl, Nyx is a small realistic 0.30m domestic black cat "
        "(clearly below Lia's knee height, never dog-sized), the central round glasshouse table is 0.75m waist-high "
        "(0.76m diameter), and the crystal vase is 0.32m tall. Every standing character and object rests firmly on the "
        "floor or tabletop with natural contact shadows and real gravity—zero floating objects, zero clipping inside "
        "the table, NO fade-in from black, NO title cards, and each character holds at most ONE prop (if holding "
        "red_umbrella, Lia holds exactly ONE single open red umbrella)."
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
) -> tuple[str, Optional[str], JudgeResult, int, int]:
    """
    Generate 2 parallel Omni Flash candidates for one scene + camera, score via `judge()` +
    `score_clip_alignment()`, retry up to `config.MAX_QA_RETRIES` with corrective constraints
    on failure, log 1-take vs 2-take metrics, and return
    `(final_url, tail_frame_url, judge_result, selected_idx, retries)`.
    """
    model_id = _resolve_video_model()
    prompt = _build_shot_prompt(
        scene=scene,
        cam_id=cam_id,
        has_first_frame=bool(prev_tail_path),
        has_preview_video=bool(preview_path),
        extra_constraint=extra_constraint,
    )

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
        final_url = f"/assets/finals/scene_{scene.scene_id}_{cam_id}.mp4"
        tail_url = f"/assets/tails/scene_{scene.scene_id}_{cam_id}_tail.png"
        return final_url, tail_url, qa_res, 0, retries

    # Resolve & upload reference assets (capped at 6 total images including start frame).
    local_prev_tail = _resolve_asset_to_local_path(prev_tail_path)
    local_preview_any = _resolve_asset_to_local_path(preview_path)
    local_preview_img: Optional[Path] = None
    local_preview: Optional[Path] = None
    if local_preview_any:
        if local_preview_any.suffix.lower() in {".mp4", ".webm"}:
            local_preview = local_preview_any
        elif local_preview_any.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            local_preview_img = local_preview_any

    start_frame_path = local_preview_img or local_prev_tail
    max_refs = 5 if start_frame_path else 6
    seen_paths: set[str] = set()
    local_refs: list[Path] = []
    for ref in reference_images:
        p = _resolve_asset_to_local_path(ref)
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
    temp_artifacts: list[Path] = []

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
        current_constraint = f"{current_constraint} {repair_note}".strip()

    assert best_qa is not None
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

    canonical_video = config.FINALS_DIR / f"scene_{scene.scene_id}_{cam_id}.mp4"
    canonical_tail = config.TAILS_DIR / f"scene_{scene.scene_id}_{cam_id}_tail.png"

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


@log_function_call
async def render_scene_media(
    scene: Scene,
    reference_images: list[str],
    cam_a_preview_path: Optional[str] = None,
    cam_b_preview_path: Optional[str] = None,
    prev_tail_a: Optional[str] = None,
    prev_tail_b: Optional[str] = None,
    tail_inspector: Optional[Callable[[int, str], Awaitable[str]]] = None,
) -> SceneMedia:
    """Concurrently render Camera A and Camera B for a single scene and update tail-frame notes."""
    preview_a = cam_a_preview_path or scene.media.cam_a_preview_url
    preview_b = cam_b_preview_path or scene.media.cam_b_preview_url

    res_a, res_b = await asyncio.gather(
        render_single_camera_shot(scene, "cam_a", prev_tail_a, reference_images, preview_path=preview_a),
        render_single_camera_shot(scene, "cam_b", prev_tail_b, reference_images, preview_path=preview_b),
    )

    avg_score = round((res_a[2].overall_score + res_b[2].overall_score) / 2, 3)
    both_have_video = bool(res_a[0] and res_b[0])
    media = SceneMedia(
        cam_a_preview_url=scene.media.cam_a_preview_url,
        cam_b_preview_url=scene.media.cam_b_preview_url,
        cam_a_final_url=res_a[0] or None,
        cam_b_final_url=res_b[0] or None,
        cam_a_tail_frame_url=res_a[1],
        cam_b_tail_frame_url=res_b[1],
        qa_result=res_a[2],
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
