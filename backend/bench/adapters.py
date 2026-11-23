"""
Adapters: one per way of making the clips of a scenario. Each returns, for every scene from 2 on, the two
clips (front view, 45 degree view) as local files. Adding a video model = adding one adapter.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Protocol

from backend import config
from backend.bench.scenario import Scenario
from backend.media import stage3d
from backend.media import video_renderer as vr
from backend.schemas import SceneMedia


class Adapter(Protocol):
    name: str
    needs_model: bool

    async def render(self, scenario: Scenario, out_dir: Path) -> dict[int, tuple[Path, Path]]: ...


class Blocking3DAdapter:
    """The 3D stage itself, with no generative model. It is exact by construction: the reference row."""

    name = "stage3d_blocking"
    needs_model = False

    async def render(self, scenario: Scenario, out_dir: Path) -> dict[int, tuple[Path, Path]]:
        out: dict[int, tuple[Path, Path]] = {}
        ffmpeg = vr._resolve_ffmpeg_exe()
        for prev, step in zip(scenario.steps, scenario.steps[1:]):
            split = out_dir / f"s{step.index}_split.mp4"
            await asyncio.to_thread(
                stage3d.render_blocking_clip, prev.scene, step.scene, split, 3.0, 8, scenario.prop_heights, ffmpeg
            )
            a, b = out_dir / f"s{step.index}_a.mp4", out_dir / f"s{step.index}_b.mp4"
            await asyncio.to_thread(vr._crop_split_panel_sync, split, False, a)
            await asyncio.to_thread(vr._crop_split_panel_sync, split, True, b)
            split.unlink(missing_ok=True)
            out[step.index] = (a, b)
        return out


class StageGenesisAdapter:
    """StageGenesis with the world state: item ledger in the prompt, and one of the render bases."""

    needs_model = True

    def __init__(self, render_base: str = "stage3d_first_last") -> None:
        self.render_base = render_base
        self.name = f"with_state_{render_base}"

    async def render(self, scenario: Scenario, out_dir: Path) -> dict[int, tuple[Path, Path]]:
        out: dict[int, tuple[Path, Path]] = {}
        old = config.RENDER_BASE
        config.RENDER_BASE = self.render_base
        try:
            opening = scenario.steps[0].scene
            tag = uuid.uuid4().hex[:6]
            tail_a = stage3d.compose_stage_frame(opening, "cam_a", config.TAILS_DIR / f"bench_{tag}_open_a.png", prop_heights=scenario.prop_heights)
            tail_b = stage3d.compose_stage_frame(opening, "cam_b", config.TAILS_DIR / f"bench_{tag}_open_b.png", prop_heights=scenario.prop_heights)
            prev_tails = (f"/assets/tails/{tail_a.name}", f"/assets/tails/{tail_b.name}")
            for prev, step in zip(scenario.steps, scenario.steps[1:]):
                step.scene.media = SceneMedia()
                media = await vr.render_scene_media(
                    step.scene, [], prev_tail_a=prev_tails[0], prev_tail_b=prev_tails[1], fast_live_mode=True,
                    start_state=prev.scene, prop_heights=scenario.prop_heights,
                    prop_descriptions={pid: name for pid, name in __import__("backend.bench.scenario", fromlist=["OBJECT_NAMES"]).OBJECT_NAMES.items()},
                )
                a, b = vr._resolve_asset_to_local_path(media.cam_a_final_url), vr._resolve_asset_to_local_path(media.cam_b_final_url)
                if not a or not b:
                    raise RuntimeError(f"{self.name}: no clip for scene {step.index}")
                out[step.index] = (a, b)
                prev_tails = (media.cam_a_tail_frame_url, media.cam_b_tail_frame_url)
        finally:
            config.RENDER_BASE = old
        return out


class StatelessTextAdapter:
    """
    The baseline without a world state: each clip continues from the previous clip's last frames and is told only
    this step's sentence. Nothing tells the model what exists, where it is, or what happened before.
    """

    name = "stateless_text_chain"
    needs_model = True

    async def render(self, scenario: Scenario, out_dir: Path) -> dict[int, tuple[Path, Path]]:
        out: dict[int, tuple[Path, Path]] = {}
        opening = scenario.steps[0].scene
        frame_a = stage3d.compose_stage_frame(opening, "cam_a", out_dir / "open_a.png", prop_heights=scenario.prop_heights)
        frame_b = stage3d.compose_stage_frame(opening, "cam_b", out_dir / "open_b.png", prop_heights=scenario.prop_heights)
        for step in scenario.steps[1:]:
            split_frame = await asyncio.to_thread(vr._compose_split_frame_sync, frame_a, frame_b, out_dir / f"s{step.index}_start.png")
            uploaded = await vr._get_or_upload_uri(split_frame, use_cache=False)
            prompt = (
                "Split-screen video. Two panels side by side with black bars above and below, exactly as in Image 1: "
                "LEFT = front view, RIGHT = 45 degree side view of the same room at the same moment. Both views stay "
                "fixed. Keep the black bars and the split where they are. Anime cel-shaded style. "
                f"Action: {step.scene.narration}"
            )
            split_video = out_dir / f"s{step.index}_split.mp4"
            await asyncio.to_thread(
                vr._generate_omni_take_sync, vr._resolve_video_model(),
                [{"type": "image", "uri": uploaded[0], "mime_type": uploaded[1]}, {"type": "text", "text": prompt}],
                4, "720p", config.VIDEO_ASPECT_RATIO, split_video,
            )
            a, b = out_dir / f"s{step.index}_a.mp4", out_dir / f"s{step.index}_b.mp4"
            await asyncio.to_thread(vr._crop_split_panel_sync, split_video, False, a)
            await asyncio.to_thread(vr._crop_split_panel_sync, split_video, True, b)
            frame_a = await asyncio.to_thread(vr._extract_tail_frame_sync, a, out_dir / f"s{step.index}_a_tail.png")
            frame_b = await asyncio.to_thread(vr._extract_tail_frame_sync, b, out_dir / f"s{step.index}_b_tail.png")
            out[step.index] = (a, b)
        return out


def make_adapter(name: str) -> Adapter:
    if name == "stage3d_blocking":
        return Blocking3DAdapter()
    if name == "stateless_text_chain":
        return StatelessTextAdapter()
    if name.startswith("with_state_"):
        return StageGenesisAdapter(name[len("with_state_"):])
    raise ValueError(f"unknown adapter {name}")
