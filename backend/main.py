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
from backend.director.neon_director import plan_scenes
from backend.director.rule_memory import add_rule, load_rules
from backend.director.state_tree import get_branch, list_branches, save_branch
from backend.logger import log_function_call
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import get_render_progress, render_branch_scenes, render_scene_media
from backend.schemas import BranchState, Scene, SceneMedia, StagePropState

_ALLOWED_PREVIEW_EXTS: set[str] = {".webm", ".mp4", ".png", ".jpg", ".jpeg"}

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
        "live": live,
        "has_api_key": has_key,
        "demo_mode": config.DEMO_MODE,
        "models": {
            "director": config.MODEL_DIRECTOR,
            "judge": config.MODEL_JUDGE,
            "decision": config.MODEL_DECISION,
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
):
    """Call `create_prop` while safely supporting optional `frame_timestamp_sec` across P2 updates."""
    sig = inspect.signature(create_prop)
    kwargs: dict = {
        "name": name,
        "description": description,
        "camera_frame_bytes": camera_frame_bytes,
    }
    if "frame_timestamp_sec" in sig.parameters:
        kwargs["frame_timestamp_sec"] = frame_timestamp_sec
    return await create_prop(**kwargs)


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
        learned_rules=load_rules(),
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
            # R-03: Clear stale parent media & QA so unrendered scenes never show parent videos
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


@app.post("/api/media/create-prop")
@log_function_call
async def api_media_create_prop(
    name: str = Form(...),
    description: str = Form(""),
    branch_id: str = Form("branch_v1"),
    scene_id: int = Form(1),
    placement: str = Form("table"),
    frame_timestamp_sec: Optional[float] = Form(None),
    image: Optional[UploadFile] = File(None),
) -> dict:
    """Create a transparent 2D stage prop from text or a camera image snapshot and sync placement to `branch.scenes`."""
    img_bytes = await image.read() if image else None
    prop_id, item = await _invoke_create_prop(
        name=name,
        description=description,
        camera_frame_bytes=img_bytes,
        frame_timestamp_sec=frame_timestamp_sec,
    )
    norm_placement = placement if placement in ("table", "held", "floor") else "table"
    on_floor = norm_placement == "floor"
    prop_state = "held" if norm_placement == "held" else "glowing"
    prop_grid = [1.08, 1.08] if norm_placement == "table" else ([1.15, 1.48] if on_floor else [0.92, 1.15])
    item.state = prop_state

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
                    else:
                        s.stage_props.append(
                            StagePropState(id=prop_id, grid=prop_grid, on_floor=on_floor, state=prop_state)
                        )
                s.needs_rerender = True
        save_branch(branch)
    return {"prop_id": prop_id, "image_url": item.image_url, "item": item, "placement": norm_placement}


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
        # R-15: Enforce hard 95s per-scene timeout with graceful 2.5D preview fallback
        try:
            target.media = await asyncio.wait_for(
                render_scene_media(
                    target,
                    refs,
                    cam_a_preview_path=target.media.cam_a_preview_url,
                    cam_b_preview_path=target.media.cam_b_preview_url,
                    tail_inspector=inspect_tail_frame,
                ),
                timeout=95.0,
            )
        except asyncio.TimeoutError:
            target.media = SceneMedia(
                cam_a_preview_url=target.media.cam_a_preview_url,
                cam_b_preview_url=target.media.cam_b_preview_url,
                fallback_to_preview=True,
            )
        save_branch(branch)
        return {"scene_id": target.scene_id, "media": target.media, "environment_notes": target.environment_notes}

    updated = await render_branch_scenes(branch, tail_inspector=inspect_tail_frame)
    save_branch(updated)
    return json.loads(updated.model_dump_json())
