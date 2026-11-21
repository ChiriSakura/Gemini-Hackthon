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

import json
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.director.hydrogen_judge import (
    check_contradiction,
    filter_affected_scenes,
    inspect_tail_frame,
    parse_director_input,
)
from backend.director.neon_director import plan_scenes
from backend.director.state_tree import get_branch, list_branches, save_branch
from backend.logger import log_function_call
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import render_branch_scenes, render_scene_media
from backend.schemas import BranchState, Scene

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
    """Return service health and centralized Gemini 4 model registry."""
    return {
        "status": "ok",
        "demo_mode": config.DEMO_MODE,
        "models": {
            "director": config.MODEL_DIRECTOR,
            "judge": config.MODEL_JUDGE,
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


@app.post("/api/director/fork", response_model=BranchState)
@log_function_call
async def api_director_fork(
    parent_branch_id: str = Form("branch_v1"),
    target_scene_id: int = Form(2),
    text: Optional[str] = Form(None),
    force_ignore_contradiction: bool = Form(False),
    clip: Optional[UploadFile] = File(None),
) -> BranchState:
    """Fork a story branch from `target_scene_id` using 3s `.webm` gesture+voice clip or text fallback."""
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

    updated_registry = dict(parent.props_registry)
    if parsed.has_physical_prop and parsed.prop_name:
        prop_id, prop_item = await create_prop(
            name=parsed.prop_name,
            description=parsed.prop_description or parsed.prop_name,
            camera_frame_bytes=clip_bytes,
        )
        updated_registry[prop_id] = prop_item

    prior_scenes = [s for s in parent.scenes if s.scene_id < parsed.target_scene_id]
    if not force_ignore_contradiction and prior_scenes:
        has_conflict, reason = await check_contradiction(parsed.instruction, prior_scenes)
        if has_conflict:
            return BranchState(
                branch_id=f"{parent_branch_id}_conflict",
                branch_label=f"冲突提示：{parsed.instruction[:18]}",
                parent_branch_id=parent_branch_id,
                forked_from_scene=parsed.target_scene_id,
                rewrite_summary=parsed.instruction,
                contradiction_warning=reason,
                props_registry=updated_registry,
                scenes=parent.scenes,
            )

    new_scenes = await plan_scenes(
        parsed_input=parsed,
        base_scenes=parent.scenes,
        parent_branch_id=parent.branch_id,
        props_registry=updated_registry,
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
            final_scenes.append(scene)
        else:
            reused = old_s.model_copy(deep=True)
            reused.needs_rerender = False
            reused.reused_from_branch = parent.branch_id
            final_scenes.append(reused)

    new_branch_id = f"branch_v{len(list_branches()) + 1}"
    new_branch = BranchState(
        branch_id=new_branch_id,
        branch_label=f"改写第 {parsed.target_scene_id} 幕：{parsed.instruction[:16]}",
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
    image: Optional[UploadFile] = File(None),
) -> dict:
    """Create a transparent 2D stage prop from text or a camera image snapshot."""
    img_bytes = await image.read() if image else None
    prop_id, item = await create_prop(name=name, description=description, camera_frame_bytes=img_bytes)
    branch = get_branch(branch_id)
    if branch:
        branch.props_registry[prop_id] = item
        save_branch(branch)
    return {"prop_id": prop_id, "image_url": item.image_url, "item": item}


@app.post("/api/media/render-scene")
@log_function_call
async def api_media_render_scene(
    branch_id: str = Form("branch_v1"),
    scene_id: Optional[int] = Form(None),
    scene_json: Optional[str] = Form(None),
) -> dict:
    """Render either a specific `scene_id` or all `needs_rerender=True` scenes in `branch_id`."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")

    if scene_id is not None:
        target = next((s for s in branch.scenes if s.scene_id == scene_id), None)
        if not target and scene_json:
            target = Scene.model_validate_json(scene_json)
        if not target:
            raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")
        refs = [str(config.SHARED_ASSETS_DIR / "lia.png"), str(config.SHARED_ASSETS_DIR / "nyx.png")]
        target.media = await render_scene_media(target, refs, tail_inspector=inspect_tail_frame)
        save_branch(branch)
        return {"scene_id": target.scene_id, "media": target.media, "environment_notes": target.environment_notes}

    updated = await render_branch_scenes(branch, tail_inspector=inspect_tail_frame)
    save_branch(updated)
    return json.loads(updated.model_dump_json())
