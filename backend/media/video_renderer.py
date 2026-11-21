"""
Concurrent Dual-Take Omni Flash Video Renderer & QA Loop (Owned by P2 / worker-1).

Feature & Use Cases:
- Renders `needs_rerender=True` scenes concurrently across Camera A (front wide)
  and Camera B (45-degree side close-up) using `asyncio.gather` (`360p`, `4s` per
  clip for hackathon live-demo speed).
- Runs `config.DUAL_TAKE_COUNT` (2) parallel candidate takes per shot via
  `config.MODEL_VIDEO` (`gemini-omni-flash-preview`), extracts tail frames via
  OpenCV, evaluates both candidates via unified `judge()`, keeps the higher
  confidence take, and triggers up to `config.MAX_QA_RETRIES` corrective retries
  using `write_repair_constraint()` when both takes fall below threshold.
- Triggers `inspect_tail_frame()` to write emergent visual details back into
  `scene.environment_notes` for causal continuity across scenes.
"""

import asyncio
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import cv2
from google import genai
from google.genai import types

from backend import config
from backend.director.hydrogen_judge import judge, write_repair_constraint
from backend.logger import log_function_call, log_genai_call
from backend.schemas import BranchState, JudgeItem, JudgeResult, Scene, SceneMedia

_VIDEO_MODEL_ALIASES = {
    "omni-flash": "gemini-omni-flash-preview",
    "gemini-omni-1.2-flash": "gemini-omni-flash-preview",
}

# In-memory cache mapping resolved local asset path -> (file_uri, mime_type).
_UPLOAD_CACHE: dict[str, tuple[str, str]] = {}
_UPLOAD_LOCK = asyncio.Lock()


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
    """Build a crisp Omni Flash prompt enforcing dual-camera angle, lighting, props, and mood."""
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
        "Start immediately with crisp visible stage lighting; NO fade-in from black and NO text or title cards. "
        "Each character holds at most ONE prop matching their state (if holding red_umbrella, Lia holds exactly ONE single open red umbrella, never a duplicate umbrella). "
        "Maintain strict left/center/right spatial placement and camera consistency."
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
    client = genai.Client(http_options=types.HttpOptions(timeout=600_000))
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
) -> tuple[Optional[Path], Optional[Path], JudgeResult]:
    """Generate one candidate take, extract its tail frame, and evaluate via unified `judge()`."""
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
        meta = await asyncio.to_thread(
            _generate_omni_take_sync,
            model_id,
            input_parts,
            duration_int,
            config.VIDEO_RESOLUTION,
            config.VIDEO_ASPECT_RATIO,
            out_video,
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
            {"cam_id": cam_id, "take_idx": take_idx, "attempt": attempt},
            {"error": err_msg, "elapsed_sec": elapsed},
        )
        questions = scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"]
        failed_items = [JudgeItem(question=q, passed=False, confidence=0.0, reason=err_msg) for q in questions]
        return None, None, JudgeResult(overall_score=0.0, all_passed=False, items=failed_items)

    questions = scene.qa_questions or [f"Does Camera {cam_id} match: {scene.narration}?"]
    media_paths = [str(out_video)] + ([str(tail_path)] if tail_path else [])
    qa_res = await judge(questions, context_text=prompt, media_paths=media_paths)
    return out_video, tail_path, qa_res


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
    Generate 2 parallel Omni Flash candidates for one scene + camera, score via `judge()`,
    retry up to `config.MAX_QA_RETRIES` with corrective constraints on failure, and return
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

    # Resolve & upload reference assets (capped at 6 total images including prev_tail).
    local_prev_tail = _resolve_asset_to_local_path(prev_tail_path)
    max_refs = 5 if local_prev_tail else 6
    seen_paths: set[str] = set()
    local_refs: list[Path] = []
    for ref in reference_images:
        p = _resolve_asset_to_local_path(ref)
        if p and str(p.resolve()) not in seen_paths:
            seen_paths.add(str(p.resolve()))
            local_refs.append(p)
        if len(local_refs) >= max_refs:
            break

    local_preview_any = _resolve_asset_to_local_path(preview_path)
    local_preview_img: Optional[Path] = None
    local_preview: Optional[Path] = None
    if local_preview_any:
        if local_preview_any.suffix.lower() in {".mp4", ".webm"}:
            local_preview = local_preview_any
        elif local_preview_any.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            local_preview_img = local_preview_any

    start_frame_path = local_preview_img or local_prev_tail
    uploaded_prev_tail = await _get_or_upload_uri(start_frame_path, use_cache=False) if start_frame_path else None
    uploaded_refs = list(await asyncio.gather(*(_get_or_upload_uri(p, use_cache=True) for p in local_refs)))
    uploaded_preview = await _get_or_upload_uri(local_preview, use_cache=False) if local_preview else None

    current_constraint = extra_constraint
    retries = 0
    best_video: Optional[Path] = None
    best_tail: Optional[Path] = None
    best_qa: Optional[JudgeResult] = None
    best_idx = 0
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
        for v_path, t_path, _ in takes:
            if v_path:
                temp_artifacts.append(v_path)
            if t_path:
                temp_artifacts.append(t_path)

        # Pick the candidate with highest judge() overall_score.
        winner_idx, winner = max(enumerate(takes), key=lambda pair: pair[1][2].overall_score)
        best_video, best_tail, best_qa = winner
        best_idx = winner_idx

        if best_qa.overall_score >= config.QA_PASS_THRESHOLD or attempt >= config.MAX_QA_RETRIES:
            break

        retries += 1
        repair_note = await write_repair_constraint(scene, best_qa)
        current_constraint = f"{current_constraint} {repair_note}".strip()

    assert best_qa is not None
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

    # Remove intermediate take files so backend/assets/ keeps only canonical winners.
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
        selected_candidate_index=res_a[3],  # type: ignore[arg-type]
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
    """Concurrently render all scenes with `needs_rerender=True` in `branch`."""
    ref_images = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
    ] + [p.image_url for p in branch.props_registry.values()]

    target_scenes = [s for s in branch.scenes if s.needs_rerender]

    async def _run_scene(scene: Scene) -> None:
        # Three-layer consistency rule: anchor on each scene's own 2.5D preview,
        # NEVER chaining from the previous generated clip's tail frame.
        scene.media = await render_scene_media(
            scene,
            ref_images,
            cam_a_preview_path=scene.media.cam_a_preview_url,
            cam_b_preview_path=scene.media.cam_b_preview_url,
            tail_inspector=tail_inspector,
        )

    await asyncio.gather(*(_run_scene(s) for s in target_scenes))
    return branch
