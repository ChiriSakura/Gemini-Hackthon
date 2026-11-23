"""
FastAPI Entrypoint for StageState (Coordinated Contract Surface).

Feature & Use Cases:
- Exposes the agreed HTTP API surface shared by P1 (Director), P2 (Media), and P3 (Frontend):
  - `GET  /api/health`: Service status and active model registry.
  - `GET  /api/branches`: List all persisted story branches (starts with cached `branch_v1`).
  - `GET  /api/branches/{branch_id}`: Fetch a specific branch.
  - `POST /api/director/fork`: Accepts 3s webcam+mic `.webm` (or text fallback), parses
    multimodal cues via Hydrogen, creates any physical prop, checks contradictions,
    re-plans `k..4` via Neon, and filters affected scenes via `judge()`.
  - `POST /api/media/create-prop`: Text or webcam snapshot -> transparent PNG prop.
  - `POST /api/media/render-scene`: Renders a scene (or whole branch) concurrently via Omni Flash + `judge()`.
"""

import asyncio
import base64
import inspect
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.director.genai_client import DirectorModelError
from backend.director.hydrogen_judge import (
    check_contradiction,
    classify_rewrite_intent,
    filter_affected_scenes,
    inspect_tail_frame,
    parse_director_input,
)
from backend.director.neon_director import extend_single_scene, plan_scenes
from backend.director.rule_memory import add_rule, load_general_rules, load_rules
from backend.director.improv import improvise_scene
from backend.director.timeline import diff_branches, graft
from backend.director.world_rules import apply_world_rules, keep_actors_clear_of_table, reconcile_inventory
from backend.director.state_tree import (
    delete_branch,
    get_branch,
    is_protected_branch,
    list_branches,
    save_branch,
)
from backend.director.world_manifest import WorldManifest, get_active_world_manifest
from backend.logger import log_function_call
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import (
    build_blocking_preview,
    export_branch_movie,
    get_render_progress,
    publish_scene_progress,
    render_branch_scenes,
    reblock_start_frames,
    render_scene_media,
    subscribe_render_progress,
    unsubscribe_render_progress,
)
from backend.schemas import BranchState, DirectorInputParsed, PropRegistryItem, Scene, SceneMedia, StagePropState

_ALLOWED_PREVIEW_EXTS: set[str] = {".webm", ".mp4", ".png", ".jpg", ".jpeg"}
_BRANCH_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


def _get_branch_lock(branch_id: str) -> asyncio.Lock:
    """Return a per-branch asyncio lock preventing concurrent render clobbering (R-38)."""
    lock = _BRANCH_WRITE_LOCKS.get(branch_id)
    if lock is None:
        lock = asyncio.Lock()
        _BRANCH_WRITE_LOCKS[branch_id] = lock
    return lock


async def _read_upload_bounded(upload: Optional[UploadFile], max_bytes: int, label: str) -> Optional[bytes]:
    """Read an uploaded file with a strict byte ceiling, returning HTTP 413 if oversized."""
    if upload is None:
        return None
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        limit_mb = round(max_bytes / (1024 * 1024), 1)
        raise HTTPException(
            status_code=413,
            detail=f"Uploaded {label} exceeds {limit_mb} MB limit",
        )
    return data


app = FastAPI(title="StageState Director API", version="0.3.0")


@app.exception_handler(DirectorModelError)
async def _handle_director_model_error(_request: Request, exc: DirectorModelError) -> JSONResponse:
    """Return explicit 503/504 JSON with retryable hint instead of an opaque HTTP 500."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            }
        },
    )

_cors_origins = config.CORS_ORIGINS or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=("*" not in _cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/assets", StaticFiles(directory=str(config.ASSETS_DIR)), name="assets")
if config.SHARED_ASSETS_DIR.exists():
    app.mount("/shared-assets", StaticFiles(directory=str(config.SHARED_ASSETS_DIR)), name="shared-assets")


@app.get("/api/health")
@log_function_call
def health_check() -> dict:
    """Return service health, explicit live-vs-mock status (R-01), and centralized Gemini 4 model registry (R-35)."""
    has_key = bool(config.GEMINI_API_KEY)
    live = has_key and not config.DEMO_MODE
    return {
        "status": "ok",
        # `live` is False when no model key is configured or demo mode is on: every answer is canned.
        "live": live,
        "has_api_key": has_key,
        "demo_mode": config.DEMO_MODE,
        "models": {
            "director": config.MODEL_DIRECTOR,
            "judge": config.MODEL_JUDGE,
            "decision": config.MODEL_DECISION if config.USE_DECISIONS_API else None,
            "video": config.MODEL_VIDEO,
            "image": config.MODEL_IMAGE,
        },
    }


@app.get("/api/world/manifest", response_model=WorldManifest)
@log_function_call
def api_get_world_manifest() -> WorldManifest:
    """Return active World Pack manifest (style anchor, room refs, cast SI heights, fast prop templates)."""
    return get_active_world_manifest()


@app.get("/api/branches", response_model=list[BranchState])
@log_function_call
def api_list_branches() -> list[BranchState]:
    """Return all saved branches (seeds `branch_v1` from `shared/mock_branch_1.json` if empty)."""
    return list_branches()


@app.get("/api/timeline/diff")
def api_timeline_diff(a: str = Query(...), b: str = Query(...)) -> dict:
    """Compare two timelines scene by scene: who stands where, who holds what, where every object is."""
    branch_a, branch_b = get_branch(a), get_branch(b)
    if not branch_a or not branch_b:
        raise HTTPException(status_code=404, detail="Branch not found")
    return diff_branches(branch_a, branch_b)


@app.post("/api/timeline/graft")
async def api_timeline_graft(
    base_branch_id: str = Form(...),
    base_scene_id: int = Form(...),
    donor_branch_id: str = Form(...),
    donor_from_scene_id: int = Form(...),
) -> dict:
    """
    Continue one timeline with another timeline's later scenes. When the two worlds do not match at the join,
    a bridging scene is inserted. The new timeline is saved as its own branch; nothing existing is changed.
    """
    base, donor = get_branch(base_branch_id), get_branch(donor_branch_id)
    if not base or not donor:
        raise HTTPException(status_code=404, detail="Branch not found")
    try:
        out, report = graft(base, base_scene_id, donor, donor_from_scene_id, _next_branch_id())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ordered = sorted(out.scenes, key=lambda s: s.scene_id)
    for prev, scene in zip(ordered, ordered[1:]):
        if not scene.media.cam_a_final_url:
            await _attach_blocking_preview(scene, prev, out.props_registry)
    save_branch(out)
    return {"report": report, "branch": json.loads(out.model_dump_json())}


@app.get("/api/branches/{branch_id}", response_model=BranchState)
@log_function_call
def api_get_branch(branch_id: str) -> BranchState:
    """Return a single branch by ID."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
    return branch


@app.delete("/api/branches/{branch_id}")
@log_function_call
async def api_delete_branch(branch_id: str) -> dict:
    """Delete a non-protected experimental live branch (`403` for `branch_v1` and `*_cached`)."""
    if is_protected_branch(branch_id):
        raise HTTPException(status_code=403, detail=f"Protected preset branch cannot be deleted: {branch_id}")
    async with _get_branch_lock(branch_id):
        try:
            deleted = delete_branch(branch_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
    return {"deleted": True, "branch_id": branch_id}


@app.get("/api/director/rules")
@log_function_call
def api_get_rules() -> dict:
    """Return active continuity rules learned from QA repairs."""
    return {"rules": load_rules()}


@app.get("/api/media/progress/{branch_id}")
@log_function_call
def api_get_render_progress(branch_id: str) -> dict:
    """Return live per-scene parallel render progress snapshot for `branch_id`."""
    return get_render_progress(branch_id)


@app.get("/api/media/progress/{branch_id}/stream")
async def api_stream_render_progress(
    branch_id: str,
    request: Request,
    once: bool = Query(False),
) -> StreamingResponse:
    """Server-Sent Events (`text/event-stream`) pushing real-time render progress for `branch_id`."""

    async def _event_generator():
        q = subscribe_render_progress(branch_id)
        try:
            initial = get_render_progress(branch_id)
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"
            if once:
                return
            started = asyncio.get_running_loop().time()
            while True:
                if await request.is_disconnected():
                    break
                if asyncio.get_running_loop().time() - started > 120.0:
                    break
                try:
                    snap = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                    if snap.get("status") == "completed":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe_render_progress(branch_id, q)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _read_json_optional(path: Path) -> Optional[dict]:
    """Safely load an empirical report JSON if present on disk."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@app.get("/api/eval/summary")
@log_function_call
def api_get_eval_summary() -> dict:
    """Return consolidated empirical evaluation reports across P1 (eval/) and P2 (backend/assets/)."""
    return {
        "stress_report": _read_json_optional(config.BASE_DIR / "eval" / "stress_report.json"),
        "latest_eval": _read_json_optional(config.BASE_DIR / "eval" / "latest_report.json"),
        "dual_cam_stats": _read_json_optional(config.ASSETS_DIR / "eval_dual_cam_stats.json"),
        "calibration_20_clips": _read_json_optional(config.ASSETS_DIR / "calibration_20_clips.json"),
        "decisions_multimodal": _read_json_optional(config.ASSETS_DIR / "eval_decisions_multimodal.json"),
        "audio_mood": _read_json_optional(config.ASSETS_DIR / "eval_audio_mood.json"),
        "branch_matrix": _read_json_optional(config.ASSETS_DIR / "eval_branch_matrix.json"),
        "prop_persistence": _read_json_optional(config.ASSETS_DIR / "eval_prop_persistence.json"),
        "cache_savings": _read_json_optional(config.ASSETS_DIR / "eval_cache_savings.json"),
        "resolution_bench": _read_json_optional(config.ASSETS_DIR / "eval_720p_vs_360p.json"),
        "failover_rehearsal": _read_json_optional(config.ASSETS_DIR / "eval_failover_rehearsal.json"),
        "dual_cam_fixed_room": _read_json_optional(config.ASSETS_DIR / "eval_dual_cam_fixed_room.json"),
        "fast_live_latency": _read_json_optional(config.ASSETS_DIR / "eval_fast_live_latency.json"),
        "video_consistency": _read_json_optional(config.ASSETS_DIR / "eval" / "video_consistency.json"),
        "submission_summary": _read_json_optional(config.ASSETS_DIR / "metrics_summary_for_submission.json"),
        "preflight": _read_json_optional(config.ASSETS_DIR / "preflight_report.json"),
    }


def _next_branch_id() -> str:
    """Allocate a collision-free sequential `branch_v<N>` ID across persisted branches."""
    max_num = 1
    for b in list_branches():
        m = re.match(r"^branch_v(\d+)", b.branch_id)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"branch_v{max_num + 1}"


async def _invoke_create_prop(
    *,
    name: str,
    description: str,
    camera_frame_bytes: Optional[bytes],
    frame_timestamp_sec: Optional[float] = None,
    detect_throw_peak: bool = True,
):
    """Call `create_prop` while safely supporting optional `frame_timestamp_sec` & `detect_throw_peak` across P2 updates."""
    sig = inspect.signature(create_prop)
    kwargs: dict = {
        "name": name,
        "description": description,
        "camera_frame_bytes": camera_frame_bytes,
    }
    if "frame_timestamp_sec" in sig.parameters:
        kwargs["frame_timestamp_sec"] = frame_timestamp_sec
    if "detect_throw_peak" in sig.parameters:
        kwargs["detect_throw_peak"] = detect_throw_peak
    return await create_prop(**kwargs)


def _upsert_prop_appearance_note(
    notes: str,
    prop_id: str,
    appearance_summary: str,
    active_prop_ids: set[str],
) -> str:
    """Deduplicate prop appearance notes and prune removed props so multi-turn `environment_notes` never bloats."""
    kept: list[str] = []
    prop_tag_re = re.compile(r"^([a-z0-9_]+):\s*(.+)$", re.IGNORECASE)
    for raw_part in re.split(r"\s*\|\s*", (notes or "").strip()):
        part = raw_part.strip()
        if not part:
            continue
        m = prop_tag_re.match(part)
        if m:
            pid = m.group(1).strip()
            if pid == prop_id or pid not in active_prop_ids:
                continue
        kept.append(part)
    clean_app = appearance_summary.strip()
    if clean_app:
        kept.append(f"{prop_id}: {clean_app}")
    joined = " | ".join(kept)
    return joined[:480] if len(joined) > 480 else joined


def _improv_summary(report: Optional[dict]) -> Optional[str]:
    """One line saying what each actor wanted in an improvised scene and whether it happened."""
    if not report:
        return None
    parts = []
    for item in report.get("intents", []):
        what = item["verb"] + (f" {item['target']}" if item.get("target") else "")
        parts.append(f"{item['actor']}: {what} ({item.get('feeling', '')})" + ("" if item["applied"] else f" - not possible: {item.get('why_not')}"))
    return "Improvised. " + "; ".join(parts)


def _prop_heights(registry: dict) -> dict[str, float]:
    """Physical heights (metres) of the registered props, for drawing them to scale on the 3D stage."""
    return {pid: float(item.height_m) for pid, item in (registry or {}).items() if getattr(item, "height_m", None)}


async def _attach_blocking_preview(scene: Scene, start_state: Optional[Scene], registry: dict) -> bool:
    """Render the 3D blocking preview for `scene` and store its two clip URLs on the scene. Never raises."""
    try:
        preview = await asyncio.to_thread(build_blocking_preview, scene, start_state, _prop_heights(registry))
    except Exception:
        return False
    if not preview:
        return False
    scene.media.cam_a_preview_url, scene.media.cam_b_preview_url = preview
    return True


async def _invoke_render_scene_media(
    scene: Scene,
    refs: list[str],
    *,
    cam_a_preview_path: Optional[str],
    cam_b_preview_path: Optional[str],
    prev_tail_a: Optional[str] = None,
    prev_tail_b: Optional[str] = None,
    fast_live_mode: bool = False,
    prop_descriptions: Optional[dict[str, str]] = None,
    progress_cb=None,
    start_state: Optional[Scene] = None,
    prop_heights: Optional[dict[str, float]] = None,
):
    """Call `render_scene_media` while safely forwarding `prev_tail_a/b`, `fast_live_mode`, `prop_descriptions`, and `progress_cb`."""
    sig = inspect.signature(render_scene_media)
    kwargs: dict = {
        "cam_a_preview_path": cam_a_preview_path,
        "cam_b_preview_path": cam_b_preview_path,
        "tail_inspector": inspect_tail_frame,
    }
    if prev_tail_a and "prev_tail_a" in sig.parameters:
        kwargs["prev_tail_a"] = prev_tail_a
    if prev_tail_b and "prev_tail_b" in sig.parameters:
        kwargs["prev_tail_b"] = prev_tail_b
    if "fast_live_mode" in sig.parameters:
        kwargs["fast_live_mode"] = fast_live_mode
    if "prop_descriptions" in sig.parameters and prop_descriptions:
        kwargs["prop_descriptions"] = prop_descriptions
    if "progress_cb" in sig.parameters and progress_cb is not None:
        kwargs["progress_cb"] = progress_cb
    if "start_state" in sig.parameters and start_state is not None:
        kwargs["start_state"] = start_state
    if "prop_heights" in sig.parameters and prop_heights:
        kwargs["prop_heights"] = prop_heights
    return await render_scene_media(scene, refs, **kwargs)


@app.post("/api/director/fork", response_model=BranchState)
@log_function_call
async def api_director_fork(
    parent_branch_id: str = Form("branch_v1"),
    target_scene_id: int = Form(2),
    text: Optional[str] = Form(None),
    force_ignore_contradiction: bool = Form(False),
    confirm_contradiction: bool = Form(False),
    clip: Optional[UploadFile] = File(None),
) -> BranchState:
    """Fork a story branch from `target_scene_id` using 3s `.webm` gesture+voice clip or text fallback."""
    ignore_conflict = force_ignore_contradiction or confirm_contradiction
    parent = get_branch(parent_branch_id)
    if not parent:
        raise HTTPException(status_code=404, detail=f"Parent branch {parent_branch_id} not found")

    clip_bytes = await _read_upload_bounded(clip, config.MAX_CLIP_UPLOAD_BYTES, "clip")
    parsed = await parse_director_input(
        clip_bytes=clip_bytes,
        clip_mime_type=(clip.content_type if clip else "video/webm") or "video/webm",
        text_fallback=text,
        target_scene_id=target_scene_id,
    )
    # R-16: Always honor the scene explicitly selected by the director/judge
    parsed.target_scene_id = target_scene_id

    updated_registry = dict(parent.props_registry)
    if parsed.has_physical_prop and parsed.prop_name:
        prop_id, prop_item = await _invoke_create_prop(
            name=parsed.prop_name,
            description=parsed.prop_description or parsed.prop_name,
            camera_frame_bytes=clip_bytes,
            frame_timestamp_sec=parsed.prop_frame_timestamp_sec,
        )
        updated_registry[prop_id] = prop_item

    prior_scenes = [s for s in parent.scenes if s.scene_id < parsed.target_scene_id]
    # Run Decisions API intent routing (`choice`) concurrently with contradiction check (`noul`)
    if not ignore_conflict and prior_scenes:
        (has_conflict, reason), _intent_route = await asyncio.gather(
            check_contradiction(parsed.instruction, prior_scenes),
            classify_rewrite_intent(parsed.instruction),
        )
        if has_conflict:
            return BranchState(
                branch_id=f"{parent_branch_id}_conflict",
                branch_label=f"Conflict detected: {parsed.instruction[:28]}",
                parent_branch_id=parent_branch_id,
                forked_from_scene=parsed.target_scene_id,
                rewrite_summary=parsed.instruction,
                contradiction_warning=reason,
                props_registry=updated_registry,
                scenes=parent.scenes,
            )
    else:
        await classify_rewrite_intent(parsed.instruction)

    new_scenes = await plan_scenes(
        parsed_input=parsed,
        base_scenes=parent.scenes,
        parent_branch_id=parent.branch_id,
        props_registry=updated_registry,
        learned_rules=load_general_rules(),
    )
    old_by_id = {s.scene_id: s for s in parent.scenes}
    affected_ids = set(await filter_affected_scenes(parsed.instruction, parent.scenes, parsed.target_scene_id))
    final_scenes: list[Scene] = []
    for scene in new_scenes:
        old_s = old_by_id.get(scene.scene_id)
        if scene.scene_id < parsed.target_scene_id:
            final_scenes.append(scene)
            continue
        state_changed = (
            old_s is None
            or old_s.lighting != scene.lighting
            or [(p.id, p.state, p.on_floor) for p in old_s.stage_props]
            != [(p.id, p.state, p.on_floor) for p in scene.stage_props]
            or [(a.id, a.action, a.holding_prop) for a in old_s.actors]
            != [(a.id, a.action, a.holding_prop) for a in scene.actors]
        )
        if scene.scene_id in affected_ids or state_changed:
            scene.needs_rerender = True
            scene.reused_from_branch = None
            # R-03: A rewritten scene has no clip or QA of its own yet; never inherit the parent's.
            scene.media = SceneMedia()
            final_scenes.append(scene)
        else:
            reused = old_s.model_copy(deep=True)
            reused.needs_rerender = False
            reused.reused_from_branch = parent.branch_id
            final_scenes.append(reused)

    new_branch_id = _next_branch_id()
    new_branch = BranchState(
        branch_id=new_branch_id,
        branch_label=f"Rewrite S{parsed.target_scene_id}: {parsed.instruction[:24]}",
        parent_branch_id=parent.branch_id,
        forked_from_scene=parsed.target_scene_id,
        rewrite_summary=parsed.instruction,
        props_registry=updated_registry,
        scenes=final_scenes,
    )
    save_branch(new_branch)
    return new_branch


@app.post("/api/director/perceive", response_model=DirectorInputParsed)
@log_function_call
async def api_director_perceive(
    target_scene_id: int = Form(2),
    text: Optional[str] = Form(None),
    clip: Optional[UploadFile] = File(None),
) -> DirectorInputParsed:
    """Fast Stage-1 perception (<2.5s): return Heard/Saw cues + prop prompt flag before Neon/Render."""
    clip_bytes = await _read_upload_bounded(clip, config.MAX_CLIP_UPLOAD_BYTES, "clip")
    parsed = await parse_director_input(
        clip_bytes=clip_bytes,
        clip_mime_type=(clip.content_type if clip else "video/webm") or "video/webm",
        text_fallback=text,
        target_scene_id=target_scene_id,
    )
    parsed.target_scene_id = target_scene_id
    return parsed


@app.post("/api/director/extend", response_model=BranchState)
@log_function_call
async def api_director_extend(
    branch_id: str = Form("branch_v1"),
    from_scene_id: Optional[int] = Form(None),
    text: Optional[str] = Form(None),
    end_freeze_mode: str = Form("sculpture"),
    detect_throw_peak: bool = Form(True),
    parsed_directive_json: Optional[str] = Form(None),
    injected_prop_json: Optional[str] = Form(None),
    clip: Optional[UploadFile] = File(None),
    mode: str = Form("directed"),
    improv_seed: Optional[int] = Form(None),
) -> BranchState:
    """
    `mode="improvised"`: `text` is a situation, not a direction. Each actor decides for itself what to do and an
    arbiter turns the wishes into one legal world state (backend/director/improv.py). Otherwise:

    Unbounded Living-Sculpture continuation endpoint (`Design_Infinite_Sculpture.md` Sec 3.2):
    - If `from_scene_id` is omitted or equals the latest scene of `branch_id`, appends a new
      single scene `Scene N+1` onto `branch_id` (protecting `branch_v1` baseline by auto-forking).
    - If `from_scene_id < len(branch.scenes)` (user rewound to an earlier historical sculpture),
      forks a brand-new branch `branch_v<N>` preserving `1..from_scene_id` verbatim and appending `Scene from_scene_id + 1`.
    - Accepts optional `parsed_directive_json` from `/api/director/perceive` to avoid duplicate Hydrogen calls.
    """
    parent = get_branch(branch_id)
    if not parent or not parent.scenes:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found or empty")

    ordered_scenes = sorted(parent.scenes, key=lambda s: s.scene_id)
    anchor_id = from_scene_id if from_scene_id is not None else ordered_scenes[-1].scene_id
    prefix_scenes = [s.model_copy(deep=True) for s in ordered_scenes if s.scene_id <= anchor_id]
    if not prefix_scenes:
        raise HTTPException(status_code=404, detail=f"Scene {anchor_id} not found in {branch_id}")

    next_scene_id = prefix_scenes[-1].scene_id + 1
    clip_bytes = await _read_upload_bounded(clip, config.MAX_CLIP_UPLOAD_BYTES, "clip")
    parsed: Optional[DirectorInputParsed] = None
    if parsed_directive_json and parsed_directive_json.strip():
        try:
            parsed = DirectorInputParsed.model_validate_json(parsed_directive_json)
        except Exception:
            parsed = None
    improvised = mode == "improvised"
    if parsed is None and improvised and text and text.strip():
        parsed = DirectorInputParsed(instruction=text.strip())  # a typed situation needs no perception call
    if parsed is None:
        parsed = await parse_director_input(
            clip_bytes=clip_bytes,
            clip_mime_type=(clip.content_type if clip else "video/webm") or "video/webm",
            text_fallback=text,
            target_scene_id=next_scene_id,
        )
    parsed.target_scene_id = next_scene_id
    parsed.mode = "extend" if anchor_id == ordered_scenes[-1].scene_id else "fork"

    situation_text = parsed.instruction  # before any stage note is appended for the director model
    # The user moved characters on the 3D stage: tell the director where they now stand, so the new scene
    # (and therefore the video) starts from those spots.
    moved_ids = _USER_MOVED_ACTORS.get((branch_id, anchor_id), set())
    if moved_ids:
        notes = [
            f"{actor.name} has moved and now stands {_describe_stage_spot(actor.grid)}"
            for actor in prefix_scenes[-1].actors
            if actor.id in moved_ids
        ]
        if notes:
            parsed.instruction = (
                f"{parsed.instruction} (Stage note from the director's 3D view: {'; '.join(notes)}. "
                "The scene starts with them already standing there; keep their grid there unless this direction moves them.)"
            )

    updated_registry = dict(parent.props_registry)
    thrown_prop_id: Optional[str] = None
    injected_placement: str = "table"
    injected_motion: Optional[str] = None
    explicit_handoff = any(
        kw in (parsed.instruction or "").lower()
        for kw in ("hand you", "give you", "toss", "throw", "catch", "pass you", "to lia", "for you", "for lia", "递给", "给你", "扔给", "丢给")
    )
    if injected_prop_json and injected_prop_json.strip():
        try:
            inj = json.loads(injected_prop_json)
            inj_id = str(inj.get("prop_id") or "").strip()
            inj_item_raw = inj.get("item")
            if inj_id and isinstance(inj_item_raw, dict):
                inj_item = PropRegistryItem.model_validate(inj_item_raw)
                updated_registry[inj_id] = inj_item
                thrown_prop_id = inj_id
                injected_placement = str(inj.get("placement") or "table")
                raw_m = inj.get("entry_motion")
                injected_motion = str(raw_m) if raw_m in ("place", "throw_arc", "held") else None
                if getattr(inj_item, "appearance_summary", ""):
                    parsed.prop_description = inj_item.appearance_summary
        except Exception:
            pass
    if thrown_prop_id is None and parsed.has_physical_prop and parsed.prop_name:
        thrown_prop_id, prop_item = await _invoke_create_prop(
            name=parsed.prop_name,
            description=parsed.prop_description or parsed.prop_name,
            camera_frame_bytes=clip_bytes,
            frame_timestamp_sec=parsed.prop_frame_timestamp_sec,
            detect_throw_peak=detect_throw_peak,
        )
        updated_registry[thrown_prop_id] = prop_item
        if getattr(prop_item, "appearance_summary", ""):
            parsed.prop_description = prop_item.appearance_summary

    improv_report: Optional[dict] = None
    if improvised:
        next_scene, improv_report = await improvise_scene(situation_text, prefix_scenes, seed=improv_seed)
    else:
        next_scene = await extend_single_scene(
            parsed_input=parsed,
            prior_scenes=prefix_scenes,
            props_registry=updated_registry,
            learned_rules=load_general_rules(),
            end_freeze_mode=end_freeze_mode,
        )
    apply_world_rules(prefix_scenes[-1], next_scene)
    if thrown_prop_id:
        eff_motion = injected_motion or ("throw_arc" if explicit_handoff else "place")
        on_floor = injected_placement == "floor"
        prop_grid = [1.05, 1.1] if not on_floor else [1.15, 1.48]
        held_by_actor = any(a.holding_prop == thrown_prop_id for a in next_scene.actors)
        if injected_placement == "held" or held_by_actor:
            for actor in next_scene.actors:
                if actor.id == "lia" and not held_by_actor:
                    actor.holding_prop = thrown_prop_id
            next_scene.stage_props = [p for p in next_scene.stage_props if p.id != thrown_prop_id]
        else:
            existing_sp = next((p for p in next_scene.stage_props if p.id == thrown_prop_id), None)
            if existing_sp:
                existing_sp.entry_motion = eff_motion  # type: ignore[assignment]
                if eff_motion == "throw_arc" and not existing_sp.entry_origin:
                    existing_sp.entry_origin = [1.0, 2.2, 0.8]
            else:
                next_scene.stage_props.append(
                    StagePropState(
                        id=thrown_prop_id,
                        grid=prop_grid,
                        on_floor=on_floor,
                        state="glowing",
                        entry_motion=eff_motion,  # type: ignore[assignment]
                        entry_origin=[1.0, 2.2, 0.8] if eff_motion == "throw_arc" else None,
                    )
                )
        if eff_motion == "throw_arc":
            for actor in next_scene.actors:
                if actor.id == "lia":
                    actor.action = "catch_from_camera"
        prop_app = (getattr(updated_registry[thrown_prop_id], "appearance_summary", "") or "").strip()
        active_ids = {p.id for p in next_scene.stage_props} | {a.holding_prop for a in next_scene.actors if a.holding_prop}
        next_scene.environment_notes = _upsert_prop_appearance_note(
            next_scene.environment_notes,
            thrown_prop_id,
            prop_app,
            active_ids,
        )

    reconcile_inventory(prefix_scenes[-1], next_scene)
    # The instant 3D preview: the stage going from the previous state to the new one, in both views (no model call).
    await _attach_blocking_preview(next_scene, prefix_scenes[-1], updated_registry)

    is_rewound = anchor_id < ordered_scenes[-1].scene_id
    # Never mutate immutable cached baseline branches in-place; fork cleanly instead
    is_protected_cached = branch_id == "branch_v1" or branch_id.endswith("_cached")
    if is_rewound or is_protected_cached:
        for ps in prefix_scenes:
            ps.needs_rerender = False
            if ps.reused_from_branch is None:
                ps.reused_from_branch = parent.branch_id
        new_branch_id = _next_branch_id()
        if moved_ids:
            _USER_MOVED_ACTORS[(new_branch_id, anchor_id)] = set(moved_ids)
            _save_user_moved()
        out_branch = BranchState(
            branch_id=new_branch_id,
            branch_label=f"Continue from S{anchor_id}: {parsed.instruction[:24]}",
            parent_branch_id=parent.branch_id,
            forked_from_scene=next_scene_id,
            rewrite_summary=_improv_summary(improv_report) or parsed.instruction,
            props_registry=updated_registry,
            scenes=[*prefix_scenes, next_scene],
        )
    else:
        parent.props_registry = updated_registry
        parent.rewrite_summary = _improv_summary(improv_report) or parsed.instruction
        parent.scenes = [*prefix_scenes, next_scene]
        out_branch = parent

    save_branch(out_branch)
    return out_branch


@app.post("/api/media/create-prop")
@log_function_call
async def api_media_create_prop(
    name: str = Form(...),
    description: str = Form(""),
    branch_id: str = Form("branch_v1"),
    scene_id: int = Form(1),
    placement: str = Form("table"),
    entry_motion: str = Form("place"),
    detect_throw_peak: bool = Form(True),
    frame_timestamp_sec: Optional[float] = Form(None),
    image: Optional[UploadFile] = File(None),
) -> dict:
    """Create a transparent 2D stage prop from text or a camera image/30s video and sync placement to `branch.scenes`."""
    prop_max_bytes = (
        config.MAX_CLIP_UPLOAD_BYTES
        if (image is not None and ((image.content_type or "").startswith("video/") or (image.filename or "").lower().endswith((".webm", ".mp4"))))
        else config.MAX_IMAGE_UPLOAD_BYTES
    )
    img_bytes = await _read_upload_bounded(image, prop_max_bytes, "prop media")
    prop_id, item = await _invoke_create_prop(
        name=name,
        description=description,
        camera_frame_bytes=img_bytes,
        frame_timestamp_sec=frame_timestamp_sec,
        detect_throw_peak=detect_throw_peak,
    )
    norm_placement = placement if placement in ("table", "held", "floor") else "table"
    norm_motion = entry_motion if entry_motion in ("place", "throw_arc", "held") else ("held" if norm_placement == "held" else "place")
    on_floor = norm_placement == "floor"
    prop_state = "held" if norm_placement == "held" else "glowing"
    prop_grid = [1.08, 1.08] if norm_placement == "table" else ([1.15, 1.48] if on_floor else [0.92, 1.15])
    # R-18: preserve explicit degraded/mock states from prop_maker instead of masking them as "glowing"
    if item.state not in ("degraded_fallback", "mock_placeholder"):
        item.state = prop_state

    appearance_summary = (getattr(item, "appearance_summary", "") or "").strip()
    # R-26: never mutate immutable cached preset baselines (branch_v1 / *_cached) on disk during live prop capture
    is_protected_cached = branch_id == "branch_v1" or branch_id.endswith("_cached")
    if not is_protected_cached:
        async with _get_branch_lock(branch_id):
            branch = get_branch(branch_id)
            if branch:
                branch.props_registry[prop_id] = item
                for s in branch.scenes:
                    if s.scene_id >= scene_id:
                        if norm_placement == "held":
                            for actor in s.actors:
                                if actor.id == "lia":
                                    actor.holding_prop = prop_id
                            s.stage_props = [p for p in s.stage_props if p.id != prop_id]
                        else:
                            existing = next((p for p in s.stage_props if p.id == prop_id), None)
                            if existing:
                                existing.grid = prop_grid
                                existing.on_floor = on_floor
                                existing.state = prop_state
                                existing.entry_motion = norm_motion  # type: ignore[assignment]
                            else:
                                s.stage_props.append(
                                    StagePropState(
                                        id=prop_id,
                                        grid=prop_grid,
                                        on_floor=on_floor,
                                        state=prop_state,
                                        entry_motion=norm_motion,  # type: ignore[assignment]
                                    )
                                )
                        active_ids = {p.id for p in s.stage_props} | {a.holding_prop for a in s.actors if a.holding_prop}
                        s.environment_notes = _upsert_prop_appearance_note(
                            s.environment_notes,
                            prop_id,
                            appearance_summary,
                            active_ids,
                        )
                        s.needs_rerender = True
                save_branch(branch)
    return {
        "prop_id": prop_id,
        "image_url": item.image_url,
        "item": item,
        "placement": norm_placement,
        "entry_motion": norm_motion,
        "appearance_summary": appearance_summary,
    }


async def _save_preview_file(
    branch_id: str,
    scene_id: int,
    cam_id: str,
    upload: Optional[UploadFile],
    png_b64: Optional[str],
    end_png_b64: Optional[str] = None,
) -> Optional[str]:
    """Persist an uploaded `.webm` preview or PNG keyframe fallback to `config.PREVIEWS_DIR`."""
    if end_png_b64:
        end_path = config.PREVIEWS_DIR / f"{branch_id}_s{scene_id}_{cam_id}_end.png"
        end_path.write_bytes(base64.b64decode(end_png_b64.split(",")[-1]))
    if upload is not None:
        raw_ext = Path(upload.filename or "").suffix.lower()
        ext = raw_ext if raw_ext in _ALLOWED_PREVIEW_EXTS else ".webm"
        out_path = config.PREVIEWS_DIR / f"{branch_id}_s{scene_id}_{cam_id}{ext}"
        preview_bytes = await _read_upload_bounded(upload, config.MAX_PREVIEW_UPLOAD_BYTES, "preview")
        out_path.write_bytes(preview_bytes or b"")
        return f"/assets/previews/{out_path.name}"
    if png_b64:
        b64_part = png_b64.split(",")[-1]
        out_path = config.PREVIEWS_DIR / f"{branch_id}_s{scene_id}_{cam_id}_start.png"
        out_path.write_bytes(base64.b64decode(b64_part))
        return f"/assets/previews/{out_path.name}"
    return None


# Characters the user dragged on the 3D stage, per (branch, scene), so the next scene is directed from the new spot.
_USER_MOVED_FILE = config.ASSETS_DIR / "user_moved_actors.json"


def _load_user_moved() -> dict[tuple[str, int], set[str]]:
    try:
        raw = json.loads(_USER_MOVED_FILE.read_text())
        return {(b, int(sid)): set(ids) for b, scenes in raw.items() for sid, ids in scenes.items()}
    except Exception:
        return {}


def _save_user_moved() -> None:
    raw: dict[str, dict[str, list[str]]] = {}
    for (branch_id, scene_id), ids in _USER_MOVED_ACTORS.items():
        raw.setdefault(branch_id, {})[str(scene_id)] = sorted(ids)
    try:
        _USER_MOVED_FILE.write_text(json.dumps(raw, indent=2))
    except Exception:
        pass


_USER_MOVED_ACTORS: dict[tuple[str, int], set[str]] = _load_user_moved()


def _describe_stage_spot(grid: list[float]) -> str:
    """Plain words for a stage grid position, as seen from the front camera."""
    side = "on the left side of the room" if grid[0] < 0.7 else ("on the right side of the room" if grid[0] > 1.3 else "next to the round table")
    depth = "at the back by the windows" if grid[1] < 0.7 else ("at the front, close to the camera" if grid[1] > 1.3 else "in the middle of the floor")
    return f"{side}, {depth}"


# The world rules (item ledger, who is on the table, table clearance) live in backend/director/world_rules.py.
_reconcile_inventory = reconcile_inventory
_keep_actors_clear_of_table = keep_actors_clear_of_table


@app.post("/api/director/move-actor")
async def api_director_move_actor(
    branch_id: str = Form(...),
    scene_id: int = Form(...),
    actor_id: str = Form(...),
    grid_x: float = Form(...),
    grid_y: float = Form(...),
) -> dict:
    """Store a character position the user set by dragging on the 3D stage; later scenes continue from it."""
    async with _get_branch_lock(branch_id):
        branch = get_branch(branch_id)
        if not branch:
            raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
        scene = next((s for s in branch.scenes if s.scene_id == scene_id), None)
        actor = next((a for a in scene.actors if a.id == actor_id), None) if scene else None
        if not actor:
            raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found in scene {scene_id}")
        actor.grid = [max(0.0, min(2.0, grid_x)), max(0.0, min(2.0, grid_y))]
        actor.elevation = "floor"  # a character the user drags is set down on the floor
        _keep_actors_clear_of_table(scene)
        save_branch(branch)
        _USER_MOVED_ACTORS.setdefault((branch_id, scene_id), set()).add(actor_id)
        _save_user_moved()
        return {"branch_id": branch_id, "scene_id": scene_id, "actor_id": actor_id, "grid": actor.grid}


@app.post("/api/media/blocking-preview")
async def api_media_blocking_preview(branch_id: str = Form(...), scene_id: int = Form(...)) -> dict:
    """
    The instant 3D preview of one scene: both views of the stage going from the previous scene's state to this
    one. Drawn from the world state alone (no model call, about two seconds), so it is available at once and
    stays available when no final clip can be rendered.
    """
    async with _get_branch_lock(branch_id):
        branch = get_branch(branch_id)
        if not branch:
            raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
        scene = next((s for s in branch.scenes if s.scene_id == scene_id), None)
        if not scene:
            raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")
        prev_scene = next((s for s in branch.scenes if s.scene_id == scene_id - 1), None)
        if not await _attach_blocking_preview(scene, prev_scene, branch.props_registry):
            raise HTTPException(status_code=503, detail="The 3D stage preview is not available (stage plates missing).")
        # Saved example branches are read-only: their preview is returned but not written into the file.
        if not (branch_id == "branch_v1" or branch_id.endswith("_cached")):
            save_branch(branch)
        return {
            "branch_id": branch_id,
            "scene_id": scene_id,
            "cam_a_preview_url": scene.media.cam_a_preview_url,
            "cam_b_preview_url": scene.media.cam_b_preview_url,
        }


@app.post("/api/media/render-scene")
@log_function_call
async def api_media_render_scene(
    branch_id: str = Form("branch_v1"),
    scene_id: Optional[int] = Form(None),
    scene_json: Optional[str] = Form(None),
    cam_a_preview: Optional[UploadFile] = File(None),
    cam_b_preview: Optional[UploadFile] = File(None),
    cam_a_start_png: Optional[str] = Form(None),
    cam_a_end_png: Optional[str] = Form(None),
    cam_b_start_png: Optional[str] = Form(None),
    cam_b_end_png: Optional[str] = Form(None),
    prev_tail_frame_url: Optional[str] = Form(None),
    prev_tail_cam_b_url: Optional[str] = Form(None),
    fast_live_mode: bool = Form(False),
) -> dict:
    """Render either a specific scene (from `scene_id` or `scene_json` + 2.5D previews) or all `needs_rerender` scenes."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")

    parsed_scene = Scene.model_validate_json(scene_json) if scene_json else None
    resolved_scene_id = scene_id if scene_id is not None else (parsed_scene.scene_id if parsed_scene else None)

    if resolved_scene_id is not None:
        target = next((s for s in branch.scenes if s.scene_id == resolved_scene_id), None)
        if not target and parsed_scene:
            target = parsed_scene
            branch.scenes.append(target)
        if not target:
            raise HTTPException(status_code=404, detail=f"Scene {resolved_scene_id} not found")

        preview_a_url = await _save_preview_file(
            branch_id, target.scene_id, "cam_a", cam_a_preview, cam_a_start_png, cam_a_end_png
        )
        preview_b_url = await _save_preview_file(
            branch_id, target.scene_id, "cam_b", cam_b_preview, cam_b_start_png, cam_b_end_png
        )
        if preview_a_url:
            target.media.cam_a_preview_url = preview_a_url
        if preview_b_url:
            target.media.cam_b_preview_url = preview_b_url

        refs = [str(config.SHARED_ASSETS_DIR / "lia.png"), str(config.SHARED_ASSETS_DIR / "nyx.png")] + [
            p.image_url for p in branch.props_registry.values()
        ]
        prop_descriptions = {
            pid: ((getattr(p, "appearance_summary", "") or "").strip() or p.name)
            for pid, p in branch.props_registry.items()
        }
        # Both cameras continue from the previous scene's last frames; look them up when the caller did not send them.
        prev_scene = next((s for s in branch.scenes if s.scene_id == target.scene_id - 1), None)
        if prev_scene is not None:
            prev_tail_frame_url = prev_tail_frame_url or prev_scene.media.cam_a_tail_frame_url
            prev_tail_cam_b_url = prev_tail_cam_b_url or prev_scene.media.cam_b_tail_frame_url

        # The user dragged characters on the 3D stage after the previous scene was filmed: redraw its two last
        # frames with the characters at their new spots, so this scene starts from there in both views.
        moved_key = (branch_id, target.scene_id - 1)
        moved_now = _USER_MOVED_ACTORS.get(moved_key)
        if (
            moved_now
            and prev_scene is not None
            and prev_tail_frame_url
            and prev_tail_cam_b_url
            and config.GEMINI_API_KEY
            and not config.DEMO_MODE
        ):
            try:
                hint = "; ".join(
                    f"{a.name} now stands {_describe_stage_spot(a.grid)}" for a in prev_scene.actors if a.id in moved_now
                )
                reblocked = await reblock_start_frames(
                    prev_scene, prev_tail_frame_url, prev_tail_cam_b_url, f"Seen from this front view: {hint}."
                )
                if reblocked:
                    prev_tail_frame_url, prev_tail_cam_b_url = reblocked
                    async with _get_branch_lock(branch_id):
                        latest_branch = get_branch(branch_id) or branch
                        latest_prev = next((s for s in latest_branch.scenes if s.scene_id == prev_scene.scene_id), None)
                        if latest_prev is not None:
                            latest_prev.media.cam_a_tail_frame_url = reblocked[0]
                            latest_prev.media.cam_b_tail_frame_url = reblocked[1]
                            save_branch(latest_branch)
                    _USER_MOVED_ACTORS.pop(moved_key, None)
                    _save_user_moved()
            except Exception:  # the scene still renders from the unmoved frames
                pass

        # R-15: Enforce hard per-scene timeout with graceful 2.5D preview fallback + live SSE progress updates
        publish_scene_progress(branch_id, target.scene_id, "rendering", branch_status="running")
        try:
            rendered_media = await asyncio.wait_for(
                _invoke_render_scene_media(
                    target,
                    refs,
                    cam_a_preview_path=target.media.cam_a_preview_url,
                    cam_b_preview_path=target.media.cam_b_preview_url,
                    prev_tail_a=prev_tail_frame_url,
                    prev_tail_b=prev_tail_cam_b_url,
                    fast_live_mode=fast_live_mode,
                    prop_descriptions=prop_descriptions,
                    progress_cb=lambda phase: publish_scene_progress(
                        branch_id, target.scene_id, phase, branch_status="running"
                    ),
                    start_state=prev_scene,
                    prop_heights=_prop_heights(branch.props_registry),
                ),
                # Camera B waits for Camera A when it is derived from A's frames, so allow the extra pass.
                timeout=200.0 if (config.CAM_B_KEYFRAMES_FROM_A and not fast_live_mode) else config.RENDER_SCENE_TIMEOUT_SEC,
            )
            publish_scene_progress(
                branch_id,
                target.scene_id,
                "fallback" if rendered_media.fallback_to_preview else "done",
                qa_score=rendered_media.qa_score,
                fallback=rendered_media.fallback_to_preview,
                branch_status="completed",
            )
        except asyncio.TimeoutError:
            rendered_media = SceneMedia(
                cam_a_preview_url=target.media.cam_a_preview_url,
                cam_b_preview_url=target.media.cam_b_preview_url,
                fallback_to_preview=True,
            )
            publish_scene_progress(
                branch_id,
                target.scene_id,
                "fallback",
                qa_score=0.0,
                fallback=True,
                branch_status="completed",
            )
        except Exception as exc:
            publish_scene_progress(
                branch_id,
                target.scene_id,
                "failed",
                fallback=True,
                branch_status="completed",
            )
            # The video model could not be reached or refused the request. Say so plainly (not a bare 500);
            # the scene keeps its 3D preview, which needs no model.
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            unreachable = any(k in reason for k in ("401", "403", "UNAUTHENTICATED", "PERMISSION_DENIED", "API key"))
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "video_model_unavailable",
                    "message": (
                        "The video model rejected the API key, so no final clip was rendered. "
                        if unreachable
                        else "The final clip could not be rendered. "
                    )
                    + "The scene keeps its 3D preview.",
                    "reason": reason,
                    "retryable": not unreachable,
                },
            ) from exc

        # R-38: Re-read latest branch under per-branch lock and update ONLY this scene_id
        # so concurrent per-scene renders on the same branch never overwrite each other.
        async with _get_branch_lock(branch_id):
            latest = get_branch(branch_id) or branch
            latest_scene = next((s for s in latest.scenes if s.scene_id == target.scene_id), None)
            if latest_scene is None:
                target.media = rendered_media
                target.needs_rerender = False
                latest.scenes.append(target)
                latest_scene = target
            else:
                latest_scene.media = rendered_media
                if target.environment_notes:
                    latest_scene.environment_notes = target.environment_notes
                latest_scene.needs_rerender = False
            save_branch(latest)
            return {
                "scene_id": latest_scene.scene_id,
                "media": latest_scene.media,
                "environment_notes": latest_scene.environment_notes,
            }

    updated = await render_branch_scenes(branch, tail_inspector=inspect_tail_frame)
    save_branch(updated)
    return json.loads(updated.model_dump_json())


@app.post("/api/media/export-branch")
@log_function_call
async def api_media_export_branch(
    branch_id: str = Form("branch_v1"),
    mode: str = Form("cam_a"),
) -> dict:
    """Stitch all rendered scenes of `branch_id` into a single browser-ready H.264+AAC MP4 movie."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
    try:
        return await export_branch_movie(branch, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Branch export failed: {exc}") from exc
