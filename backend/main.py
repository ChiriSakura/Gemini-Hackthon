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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.director.hydrogen_judge import (
    check_contradiction,
    classify_rewrite_intent,
    filter_affected_scenes,
    inspect_tail_frame,
    parse_director_input,
)
from backend.director.neon_director import extend_single_scene, plan_scenes
from backend.director.rule_memory import add_rule, load_general_rules, load_rules
from backend.director.state_tree import get_branch, list_branches, save_branch
from backend.logger import log_function_call
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import get_render_progress, render_branch_scenes, render_scene_media
from backend.schemas import BranchState, DirectorInputParsed, Scene, SceneMedia, StagePropState

_ALLOWED_PREVIEW_EXTS: set[str] = {".webm", ".mp4", ".png", ".jpg", ".jpeg"}
_BRANCH_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


def _get_branch_lock(branch_id: str) -> asyncio.Lock:
    """Return a per-branch asyncio lock preventing concurrent render clobbering (R-38)."""
    lock = _BRANCH_WRITE_LOCKS.get(branch_id)
    if lock is None:
        lock = asyncio.Lock()
        _BRANCH_WRITE_LOCKS[branch_id] = lock
    return lock

app = FastAPI(title="StageState Director API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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


@app.get("/api/branches", response_model=list[BranchState])
@log_function_call
def api_list_branches() -> list[BranchState]:
    """Return all saved branches (seeds `branch_v1` from `shared/mock_branch_1.json` if empty)."""
    return list_branches()


@app.get("/api/branches/{branch_id}", response_model=BranchState)
@log_function_call
def api_get_branch(branch_id: str) -> BranchState:
    """Return a single branch by ID."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
    return branch


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
):
    """Call `render_scene_media` while safely forwarding `prev_tail_a/b`, `fast_live_mode`, and `prop_descriptions`."""
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

    clip_bytes = await clip.read() if clip else None
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
    clip_bytes = await clip.read() if clip else None
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
    clip: Optional[UploadFile] = File(None),
) -> BranchState:
    """
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
    clip_bytes = await clip.read() if clip else None
    parsed: Optional[DirectorInputParsed] = None
    if parsed_directive_json and parsed_directive_json.strip():
        try:
            parsed = DirectorInputParsed.model_validate_json(parsed_directive_json)
        except Exception:
            parsed = None
    if parsed is None:
        parsed = await parse_director_input(
            clip_bytes=clip_bytes,
            clip_mime_type=(clip.content_type if clip else "video/webm") or "video/webm",
            text_fallback=text,
            target_scene_id=next_scene_id,
        )
    parsed.target_scene_id = next_scene_id
    parsed.mode = "extend" if anchor_id == ordered_scenes[-1].scene_id else "fork"

    updated_registry = dict(parent.props_registry)
    thrown_prop_id: Optional[str] = None
    explicit_handoff = any(
        kw in (parsed.instruction or "").lower()
        for kw in ("hand you", "give you", "toss", "throw", "catch", "pass you", "to lia", "for you", "for lia", "递给", "给你", "扔给", "丢给")
    )
    if parsed.has_physical_prop and parsed.prop_name:
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

    next_scene = await extend_single_scene(
        parsed_input=parsed,
        prior_scenes=prefix_scenes,
        props_registry=updated_registry,
        learned_rules=load_general_rules(),
        end_freeze_mode=end_freeze_mode,
    )
    if thrown_prop_id:
        if not any(p.id == thrown_prop_id for p in next_scene.stage_props):
            next_scene.stage_props.append(
                StagePropState(
                    id=thrown_prop_id,
                    grid=[1.05, 1.1],
                    on_floor=False,
                    state="glowing",
                    entry_motion="throw_arc" if explicit_handoff else "place",
                    entry_origin=[1.0, 2.2, 0.8] if explicit_handoff else None,
                )
            )
        prop_app = (getattr(updated_registry[thrown_prop_id], "appearance_summary", "") or "").strip()
        active_ids = {p.id for p in next_scene.stage_props} | {a.holding_prop for a in next_scene.actors if a.holding_prop}
        next_scene.environment_notes = _upsert_prop_appearance_note(
            next_scene.environment_notes,
            thrown_prop_id,
            prop_app,
            active_ids,
        )

    is_rewound = anchor_id < ordered_scenes[-1].scene_id
    # Never mutate immutable cached baseline branches in-place; fork cleanly instead
    is_protected_cached = branch_id == "branch_v1" or branch_id.endswith("_cached")
    if is_rewound or is_protected_cached:
        for ps in prefix_scenes:
            ps.needs_rerender = False
            if ps.reused_from_branch is None:
                ps.reused_from_branch = parent.branch_id
        new_branch_id = _next_branch_id()
        out_branch = BranchState(
            branch_id=new_branch_id,
            branch_label=f"Continue from S{anchor_id}: {parsed.instruction[:24]}",
            parent_branch_id=parent.branch_id,
            forked_from_scene=next_scene_id,
            rewrite_summary=parsed.instruction,
            props_registry=updated_registry,
            scenes=[*prefix_scenes, next_scene],
        )
    else:
        parent.props_registry = updated_registry
        parent.rewrite_summary = parsed.instruction
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
    img_bytes = await image.read() if image else None
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
        out_path.write_bytes(await upload.read())
        return f"/assets/previews/{out_path.name}"
    if png_b64:
        b64_part = png_b64.split(",")[-1]
        out_path = config.PREVIEWS_DIR / f"{branch_id}_s{scene_id}_{cam_id}_start.png"
        out_path.write_bytes(base64.b64decode(b64_part))
        return f"/assets/previews/{out_path.name}"
    return None


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
        # R-15: Enforce hard 95s per-scene timeout with graceful 2.5D preview fallback
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
                ),
                timeout=95.0,
            )
        except asyncio.TimeoutError:
            rendered_media = SceneMedia(
                cam_a_preview_url=target.media.cam_a_preview_url,
                cam_b_preview_url=target.media.cam_b_preview_url,
                fallback_to_preview=True,
            )

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
