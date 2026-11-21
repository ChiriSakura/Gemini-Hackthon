"""
FastAPI Entrypoint for StageState.

Feature & Use Cases:
- Exposes unified REST endpoints for Frontend (P3) to:
  1. Fetch cached/current story branches (`GET /api/branches`, `GET /api/branches/{branch_id}`).
  2. Rewrite history from scene k with text or raw audio (`POST /api/rewrite`, `POST /api/rewrite/audio`).
  3. Create live stage props from text or webcam frame (`POST /api/props`).
  4. Trigger concurrent AI video rendering from uploaded dual-camera 3D previews (`POST /api/render`).
- Serves static generated assets under `/assets`.
"""

import base64
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.director.state_tree import get_branch, list_branches, save_branch
from backend.director.neon_director import plan_scenes
from backend.director.hydrogen_judge import (
    check_contradiction,
    extract_vocal_emotion,
    filter_affected_scenes,
    inspect_tail_frame,
)
from backend.logger import log_function_call
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import render_branch_scenes
from backend.schemas import BranchState, CreatePropRequest, RewriteRequest

app = FastAPI(title="StageState Director API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/assets", StaticFiles(directory=str(config.ASSETS_DIR)), name="assets")


@app.get("/api/health")
@log_function_call
def health_check() -> dict:
    """Return service status and active model configuration."""
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
    """List all persisted story branches (including cached Branch 1)."""
    return list_branches()


@app.get("/api/branches/{branch_id}", response_model=BranchState)
@log_function_call
def api_get_branch(branch_id: str) -> BranchState:
    """Fetch a single branch by ID."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")
    return branch


@app.post("/api/props")
@log_function_call
async def api_create_prop(req: CreatePropRequest) -> dict:
    """Generate a stage-styled transparent prop from text or webcam frame and register it."""
    raw_bytes: Optional[bytes] = None
    if req.camera_image_base64:
        b64_part = req.camera_image_base64.split(",")[-1]
        raw_bytes = base64.b64decode(b64_part)

    prop_asset = await create_prop(name_or_prompt=req.name, camera_frame_bytes=raw_bytes)
    branch = get_branch(req.branch_id)
    if branch:
        branch.props_registry[prop_asset.prop_id] = prop_asset
        save_branch(branch)
    return {"prop": prop_asset}


@app.post("/api/rewrite", response_model=BranchState)
@log_function_call
async def api_rewrite_branch(req: RewriteRequest) -> BranchState:
    """Check contradictions, re-plan scenes k..N via Neon, and filter affected scenes via judge()."""
    parent = get_branch(req.parent_branch_id)
    if not parent:
        raise HTTPException(status_code=404, detail=f"Parent branch {req.parent_branch_id} not found")

    prior_scenes = [s for s in parent.scenes if s.scene_id < req.forked_from_scene]
    if not req.force_ignore_contradiction and prior_scenes:
        has_conflict, reason = await check_contradiction(req.instruction, prior_scenes)
        if has_conflict:
            return BranchState(
                branch_id=f"{req.parent_branch_id}_conflict",
                parent_branch_id=req.parent_branch_id,
                forked_from_scene=req.forked_from_scene,
                contradiction_warning=reason,
                props_registry=parent.props_registry,
                actors_registry=parent.actors_registry,
                scenes=parent.scenes,
            )

    new_scenes = await plan_scenes(
        instruction=req.instruction,
        base_scenes=parent.scenes,
        forked_from_scene=req.forked_from_scene,
        props_registry=parent.props_registry,
    )
    affected_ids = await filter_affected_scenes(req.instruction, new_scenes, req.forked_from_scene)
    for scene in new_scenes:
        scene.was_regenerated = scene.scene_id >= req.forked_from_scene and scene.scene_id in affected_ids

    new_branch_id = f"branch_v{len(list_branches()) + 1}"
    new_branch = BranchState(
        branch_id=new_branch_id,
        parent_branch_id=parent.branch_id,
        forked_from_scene=req.forked_from_scene,
        props_registry=parent.props_registry,
        actors_registry=parent.actors_registry,
        scenes=new_scenes,
    )
    save_branch(new_branch)
    return new_branch


@app.post("/api/rewrite/audio", response_model=BranchState)
@log_function_call
async def api_rewrite_audio(
    parent_branch_id: str = Form("branch_v1"),
    forked_from_scene: int = Form(2),
    force_ignore_contradiction: bool = Form(False),
    audio: UploadFile = File(...),
) -> BranchState:
    """Ingest raw voice audio, extract vocal emotion + transcript via Hydrogen, and rewrite."""
    audio_bytes = await audio.read()
    vocal_info = await extract_vocal_emotion(audio_bytes, mime_type=audio.content_type or "audio/webm")
    req = RewriteRequest(
        parent_branch_id=parent_branch_id,
        forked_from_scene=forked_from_scene,
        instruction=vocal_info["transcript"],
        force_ignore_contradiction=force_ignore_contradiction,
    )
    branch = await api_rewrite_branch(req)
    for scene in branch.scenes:
        if scene.scene_id == forked_from_scene:
            scene.vocal_emotion = vocal_info.get("vocal_emotion", scene.vocal_emotion)
            scene.lighting = vocal_info.get("lighting", scene.lighting)
            scene.camera_shake = vocal_info.get("camera_shake", scene.camera_shake)
    save_branch(branch)
    return branch


@app.post("/api/render/{branch_id}", response_model=BranchState)
@log_function_call
async def api_render_branch(branch_id: str) -> BranchState:
    """Concurrently render all `was_regenerated=True` scenes via Omni Flash + judge() QA."""
    branch = get_branch(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch {branch_id} not found")

    updated_branch = await render_branch_scenes(branch, tail_inspector=inspect_tail_frame)
    save_branch(updated_branch)
    return updated_branch
