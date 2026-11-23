"""
3D-first stage frames.

The world state is the single source of truth. This module projects it through the two fixed stage viewpoints
(the same numbers `ThreeStageManager.ts` uses) and composites the character and prop cut-outs onto a fixed,
pre-painted picture of the empty room. Who stands where, how many of each thing exist, what is held and what lies
on the table or the floor are therefore computed, never guessed by a generative model.

The video model only has to animate from (and optionally to) these frames.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from backend import config
from backend.schemas import ActorState, Scene

# One definition of the two viewpoints, shared with the browser stage (shared/viewpoints.json).
_VIEWPOINTS_FILE = config.SHARED_DIR / "viewpoints.json"


def _load_viewpoints() -> dict[str, dict]:
    raw = json.loads(_VIEWPOINTS_FILE.read_text(encoding="utf-8"))
    return {
        cam: {"fov": float(raw[cam]["fov"]), "pos": tuple(raw[cam]["pos"]), "look_at": tuple(raw[cam]["look_at"])}
        for cam in ("cam_a", "cam_b")
    }


VIEWPOINTS: dict[str, dict] = _load_viewpoints()

TABLE_TOP_M = 0.75
TABLE_RADIUS_M = 0.56
LIA_HEIGHT_M = 1.62
NYX_HEIGHT_M = 0.42
VASE_HEIGHT_M = 0.38

PLATE_PATHS = {
    "cam_a": config.ASSETS_DIR / "stages" / "plate_cam_a.png",
    "cam_b": config.ASSETS_DIR / "stages" / "plate_cam_b.png",
}


class StageCamera:
    """Right-handed perspective camera matching Three.js PerspectiveCamera, at any output size."""

    def __init__(self, cam_id: str, width: int = 1280, height: int = 720) -> None:
        spec = VIEWPOINTS[cam_id]
        self.cam_id = cam_id
        self.width = width
        self.height = height
        self.pos = np.array(spec["pos"], dtype=np.float64)
        forward = np.array(spec["look_at"], dtype=np.float64) - self.pos
        self.forward = forward / np.linalg.norm(forward)
        right = np.cross(self.forward, np.array([0.0, 1.0, 0.0]))
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, self.forward)
        self.f = (height / 2.0) / math.tan(math.radians(spec["fov"]) / 2.0)

    def project(self, pt: tuple[float, float, float]) -> Optional[tuple[float, float, float]]:
        rel = np.array(pt, dtype=np.float64) - self.pos
        depth = float(np.dot(rel, self.forward))
        if depth <= 0.15:
            return None
        sx = (self.width / 2.0) + (float(np.dot(rel, self.right)) / depth) * self.f
        sy = (self.height / 2.0) - (float(np.dot(rel, self.up)) / depth) * self.f
        return sx, sy, depth


def grid_to_world(grid: list[float]) -> tuple[float, float]:
    col = float(grid[0]) if len(grid) >= 1 else 1.0
    row = float(grid[1]) if len(grid) >= 2 else 1.0
    return (col - 1.0) * 2.1, (row - 1.0) * 1.7


def actor_on_table(actor: ActorState) -> bool:
    """True when the world state puts this actor on top of the round table."""
    return getattr(actor, "elevation", "floor") == "table"


def actor_world_position(actor: ActorState) -> tuple[float, float, float]:
    """Feet position of an actor in metres. An actor on the table sits on the table top, left of its centre."""
    if actor_on_table(actor):
        return -TABLE_RADIUS_M * 0.5, TABLE_TOP_M, 0.04
    wx, wz = grid_to_world(actor.grid)
    return wx, 0.0, wz


@functools.lru_cache(maxsize=64)
def _load_rgba_cached(path_str: str, mtime_ns: int) -> Optional[Image.Image]:
    with Image.open(path_str) as im:
        out = im.convert("RGBA")
    bbox = out.getbbox()
    return out.crop(bbox) if bbox else out


def _load_rgba(path: Path) -> Optional[Image.Image]:
    """Load a cut-out, trimmed to its visible pixels. Cached per file version; callers never modify the result."""
    if not path.is_file():
        return None
    return _load_rgba_cached(str(path), path.stat().st_mtime_ns)


def _actor_sprite(actor: ActorState) -> Optional[Image.Image]:
    if actor.id == "lia":
        name = "lia.png" if actor.holding_prop == "red_umbrella" else "lia_no_umbrella.png"
        return _load_rgba(config.SHARED_ASSETS_DIR / name) or _load_rgba(config.SHARED_ASSETS_DIR / "lia.png")
    if actor.id == "nyx":
        return _load_rgba(config.SHARED_ASSETS_DIR / "nyx.png")
    return None


def _prop_sprite(prop_id: str, state: str = "intact", on_floor: bool = False) -> Optional[Image.Image]:
    if prop_id == "crystal_vase":
        if state == "broken":
            return _load_rgba(config.PROPS_DIR / "crystal_vase_broken.png")
        return _load_rgba(config.PROPS_DIR / "crystal_vase.png") or _load_rgba(config.SHARED_ASSETS_DIR / "crystal_vase.png")
    if prop_id == "red_umbrella":
        return None  # drawn as geometry so it lies flat on the floor
    return _load_rgba(config.PROPS_DIR / f"{prop_id}.png")


def prop_height_m(prop_id: str, state: str = "intact", heights: Optional[dict[str, float]] = None) -> float:
    if prop_id == "crystal_vase":
        return 0.24 if state == "broken" else VASE_HEIGHT_M
    if heights and prop_id in heights:
        return max(0.06, float(heights[prop_id]))
    return 0.22


def _paste_sprite(
    canvas: Image.Image,
    cam: StageCamera,
    sprite: Image.Image,
    world_pos: tuple[float, float, float],
    height_m: float,
    flip_x: bool = False,
    shadow: bool = True,
) -> None:
    base = cam.project(world_pos)
    top = cam.project((world_pos[0], world_pos[1] + height_m, world_pos[2]))
    if base is None or top is None:
        return
    px_h = max(10, int(round(abs(base[1] - top[1]))))
    px_w = max(8, int(round(px_h * sprite.width / max(1, sprite.height))))
    img = sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip_x else sprite
    resized = img.resize((px_w, px_h), Image.Resampling.LANCZOS)
    if shadow:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        rx = max(6, int(px_w * 0.36))
        ry = max(3, int(rx * 0.24))
        ImageDraw.Draw(layer).ellipse(
            [(base[0] - rx, base[1] - ry), (base[0] + rx, base[1] + ry)], fill=(0, 0, 0, 120)
        )
        canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius=max(2, ry // 2))))
    canvas.alpha_composite(resized, dest=(int(round(base[0] - px_w / 2.0)), int(round(base[1] - px_h))))


def _draw_floor_umbrella(canvas: Image.Image, cam: StageCamera, wx: float, wz: float) -> None:
    """A closed red umbrella lying flat on the floor, drawn from the painted closed-umbrella cut-out."""
    length = 0.9
    left, right = cam.project((wx - length / 2.0, 0.0, wz)), cam.project((wx + length / 2.0, 0.0, wz))
    centre = cam.project((wx, 0.0, wz))
    if not (left and right and centre):
        return
    sprite = _load_rgba(config.PROPS_DIR / "red_umbrella_closed.png")
    px_len = max(24, int(round(math.hypot(right[0] - left[0], right[1] - left[1]))))
    if sprite is None:
        ImageDraw.Draw(canvas, "RGBA").line(
            [(left[0], left[1]), (right[0], right[1])], fill=(176, 24, 40, 255), width=max(4, px_len // 14)
        )
        return
    lying = sprite.rotate(90, expand=True)  # handle to one side, tip to the other
    thick = max(6, int(round(px_len * lying.height / lying.width)))
    lying = lying.resize((px_len, thick), Image.Resampling.LANCZOS)
    angle = math.degrees(math.atan2(-(right[1] - left[1]), right[0] - left[0]))
    lying = lying.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        [(centre[0] - px_len * 0.5, centre[1] - thick * 0.25), (centre[0] + px_len * 0.5, centre[1] + thick * 0.45)],
        fill=(0, 0, 0, 90),
    )
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius=max(2, thick // 5))))
    canvas.alpha_composite(
        lying, dest=(int(round(centre[0] - lying.width / 2.0)), int(round(centre[1] - lying.height * 0.6)))
    )


def hand_anchor(actor: ActorState) -> tuple[float, float, float]:
    """Where a held object sits: in front of the chest, on the side that faces the table."""
    wx, wy, wz = actor_world_position(actor)
    toward_table = 1.0 if wx < 0 else -1.0
    lift = 1.18 if actor.action in ("hold_up", "drink", "eat") else 0.92
    return wx + 0.2 * toward_table, wy + lift, wz + 0.1


def render_empty_room_geometry(cam_id: str, out_path: Path, width: int = 1280, height: int = 720) -> Path:
    """Plain grey render of the empty room and the round table. Used once, to paint the fixed room plates from."""
    cam = StageCamera(cam_id, width, height)
    canvas = Image.new("RGBA", (width, height), (18, 22, 34, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")

    def poly(pts, fill, outline=None, w=1):
        pr = [cam.project(p) for p in pts]
        if all(pr):
            draw.polygon([(p[0], p[1]) for p in pr], fill=fill, outline=outline)

    def line(a, b, fill, w=2):
        pa, pb = cam.project(a), cam.project(b)
        if pa and pb:
            draw.line([(pa[0], pa[1]), (pb[0], pb[1])], fill=fill, width=w)

    wall, frame = (58, 66, 88, 255), (30, 30, 36, 255)
    poly([(-3.5, 0, -3.2), (3.5, 0, -3.2), (3.5, 3.6, -3.2), (-3.5, 3.6, -3.2)], wall, frame)
    poly([(-3.5, 0, 3.2), (-3.5, 0, -3.2), (-3.5, 3.6, -3.2), (-3.5, 3.6, 3.2)], wall, frame)
    poly([(3.5, 0, -3.2), (3.5, 0, 3.2), (3.5, 3.6, 3.2), (3.5, 3.6, -3.2)], wall, frame)
    for x in np.linspace(-3.5, 3.5, 8):
        line((float(x), 0, -3.2), (float(x), 3.6, -3.2), frame, 4)
    for z in np.linspace(-3.2, 3.2, 6):
        line((-3.5, 0, float(z)), (-3.5, 3.6, float(z)), frame, 3)
        line((3.5, 0, float(z)), (3.5, 3.6, float(z)), frame, 3)
    for y in (0.75, 2.6):
        line((-3.5, y, -3.2), (3.5, y, -3.2), frame, 4)
        line((-3.5, y, -3.2), (-3.5, y, 3.2), frame, 3)
        line((3.5, y, -3.2), (3.5, y, 3.2), frame, 3)
    poly([(-3.5, 0, -3.2), (3.5, 0, -3.2), (3.5, 0, 3.2), (-3.5, 0, 3.2)], (120, 86, 58, 255))
    n = 40
    poly([(TABLE_RADIUS_M * 1.1 * math.cos(2 * math.pi * i / n), 0.01, TABLE_RADIUS_M * 1.1 * math.sin(2 * math.pi * i / n)) for i in range(n)], (60, 44, 34, 255))
    for ang in (30, 150, 270):
        r = math.radians(ang)
        line((0, 0.22, 0), (0.36 * math.cos(r), 0.0, 0.36 * math.sin(r)), (70, 44, 30, 255), 9)
    line((0, 0.08, 0), (0, TABLE_TOP_M, 0), (70, 44, 30, 255), 12)
    poly([(TABLE_RADIUS_M * math.cos(2 * math.pi * i / n), TABLE_TOP_M, TABLE_RADIUS_M * math.sin(2 * math.pi * i / n)) for i in range(n)], (150, 100, 64, 255), (90, 58, 36, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, format="PNG")
    return out_path


def plates_ready() -> bool:
    return all(p.is_file() for p in PLATE_PATHS.values())


@functools.lru_cache(maxsize=8)
def _plate_cached(path_str: str, mtime_ns: int, width: int, height: int) -> Image.Image:
    with Image.open(path_str) as im:
        return im.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)


def _plate(cam_id: str, width: int, height: int) -> Image.Image:
    path = PLATE_PATHS[cam_id]
    return _plate_cached(str(path), path.stat().st_mtime_ns, width, height).copy()


def compose_stage_frame(
    scene: Scene,
    cam_id: str,
    out_path: Path,
    width: int = 1280,
    height: int = 720,
    prop_heights: Optional[dict[str, float]] = None,
    actor_overrides: Optional[dict[str, tuple[float, float, float]]] = None,
) -> Path:
    """Write `compose_stage_image` to a PNG file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    compose_stage_image(scene, cam_id, width, height, prop_heights, actor_overrides).convert("RGB").save(
        out_path, format="PNG"
    )
    return out_path


def compose_stage_image(
    scene: Scene,
    cam_id: str,
    width: int = 1280,
    height: int = 720,
    prop_heights: Optional[dict[str, float]] = None,
    actor_overrides: Optional[dict[str, tuple[float, float, float]]] = None,
) -> Image.Image:
    """
    Draw `scene`'s world state from one viewpoint: the fixed room plate plus every actor and prop where the state
    puts it. `actor_overrides` (actor id -> feet position in metres) lets the caller draw in-between positions.
    """
    cam = StageCamera(cam_id, width, height)
    canvas = _plate(cam_id, width, height)
    drawables: list[tuple[float, callable]] = []

    def add(pos: tuple[float, float, float], fn, bias: float = 0.0) -> None:
        pr = cam.project(pos)
        if pr is not None:
            drawables.append((pr[2] + bias, fn))

    held_ids = {a.holding_prop for a in scene.actors if a.holding_prop}

    for actor in scene.actors:
        sprite = _actor_sprite(actor)
        if sprite is None:
            continue
        pos = (actor_overrides or {}).get(actor.id) or actor_world_position(actor)
        if actor.id == "lia":
            h_m = 1.18 if actor.action in ("kneel", "crouch") else LIA_HEIGHT_M
            flip = actor.facing == "left"
        else:
            h_m = NYX_HEIGHT_M
            flip = actor.facing == "right"
        add(pos, lambda s=sprite, p=pos, h=h_m, f=flip: _paste_sprite(canvas, cam, s, p, h, flip_x=f))

        held = actor.holding_prop
        if held and held != "red_umbrella":
            hs = _prop_sprite(held)
            if hs is not None:
                moved = actor.model_copy(update={"grid": actor.grid})
                ax, ay, az = hand_anchor(moved)
                if actor_overrides and actor.id in actor_overrides:
                    ox, oy, oz = actor_overrides[actor.id]
                    bx, by, bz = actor_world_position(actor)
                    ax, ay, az = ax + ox - bx, ay + oy - by, az + oz - bz
                hh = prop_height_m(held, heights=prop_heights)
                add(
                    (ax, ay, az),
                    lambda s=hs, p=(ax, ay - hh / 2.0, az), h=hh: _paste_sprite(canvas, cam, s, p, h, shadow=False),
                    bias=-0.4,
                )

    for prop in scene.stage_props:
        if prop.id in held_ids:
            continue  # one of everything: a held object is never also drawn on the table or the floor
        wx, wz = grid_to_world(prop.grid)
        if prop.id == "red_umbrella":
            if prop.on_floor:
                add((wx, 0.04, wz), lambda x=wx, z=wz: _draw_floor_umbrella(canvas, cam, x, z), bias=0.3)
            continue
        sprite = _prop_sprite(prop.id, prop.state, prop.on_floor)
        if sprite is None:
            continue
        if not prop.on_floor:
            r = math.hypot(wx, wz)
            limit = TABLE_RADIUS_M * 0.7
            if r > limit and r > 1e-6:
                wx, wz = wx / r * limit, wz / r * limit
        wy = 0.0 if prop.on_floor else TABLE_TOP_M
        hh = prop_height_m(prop.id, prop.state, prop_heights)
        add((wx, wy, wz), lambda s=sprite, p=(wx, wy, wz), h=hh: _paste_sprite(canvas, cam, s, p, h))

    drawables.sort(key=lambda item: item[0], reverse=True)
    for _, fn in drawables:
        fn()
    return canvas


def project_actor_feet(scene: Scene, cam_id: str, width: int = 1280, height: int = 720) -> dict[str, tuple[float, float]]:
    """Screen position of each actor's feet. Used by tests that compare this renderer with the browser stage."""
    cam = StageCamera(cam_id, width, height)
    out: dict[str, tuple[float, float]] = {}
    for actor in scene.actors:
        pr = cam.project(actor_world_position(actor))
        if pr is not None:
            out[actor.id] = (round(pr[0], 2), round(pr[1], 2))
    return out


# --------------------------------------------------------------------------------------------------------------------
# Split-screen frames and the 3D blocking clip
# --------------------------------------------------------------------------------------------------------------------

SPLIT_W, SPLIT_H = 1280, 720
PANEL_W, PANEL_H = 640, 360
PANEL_Y = (SPLIT_H - PANEL_H) // 2


def compose_split_stage_image(
    scene: Scene,
    prop_heights: Optional[dict[str, float]] = None,
    actor_overrides: Optional[dict[str, tuple[float, float, float]]] = None,
    supersample: int = 2,
) -> Image.Image:
    """Both viewpoints of `scene` side by side (front view left, 45 degree view right), black bars above and below."""
    canvas = Image.new("RGB", (SPLIT_W, SPLIT_H), (0, 0, 0))
    for idx, cam_id in enumerate(("cam_a", "cam_b")):
        panel = compose_stage_image(
            scene, cam_id, PANEL_W * supersample, PANEL_H * supersample, prop_heights, actor_overrides
        )
        if supersample != 1:
            panel = panel.resize((PANEL_W, PANEL_H), Image.Resampling.LANCZOS)
        canvas.paste(panel.convert("RGB"), (idx * PANEL_W, PANEL_Y))
    return canvas


def compose_split_stage_frame(
    scene: Scene,
    out_path: Path,
    prop_heights: Optional[dict[str, float]] = None,
    actor_overrides: Optional[dict[str, tuple[float, float, float]]] = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    compose_split_stage_image(scene, prop_heights, actor_overrides).save(out_path, format="PNG")
    return out_path


def _ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def blocking_frames(start_state: Scene, end_state: Scene, steps: int) -> list[tuple[Scene, dict[str, tuple[float, float, float]]]]:
    """
    The in-between world states from `start_state` to `end_state`: actors glide (eased) between their positions,
    and what they hold and what lies on the stage switches halfway. Returns (state to draw, actor positions) pairs.
    """
    start_pos = {a.id: actor_world_position(a) for a in start_state.actors}
    end_pos = {a.id: actor_world_position(a) for a in end_state.actors}
    frames: list[tuple[Scene, dict[str, tuple[float, float, float]]]] = []
    for i in range(steps):
        t = _ease(i / max(1, steps - 1))
        base = start_state if t < 0.5 else end_state
        overrides: dict[str, tuple[float, float, float]] = {}
        for actor in base.actors:
            p0 = start_pos.get(actor.id, end_pos.get(actor.id))
            p1 = end_pos.get(actor.id, p0)
            if p0 is None or p1 is None:
                continue
            hop = 0.0
            if abs(p1[1] - p0[1]) > 0.05:  # getting onto or off the table: a small arc
                hop = 0.35 * math.sin(math.pi * t)
            overrides[actor.id] = (
                p0[0] + (p1[0] - p0[0]) * t,
                p0[1] + (p1[1] - p0[1]) * t + hop,
                p0[2] + (p1[2] - p0[2]) * t,
            )
        frames.append((base, overrides))
    return frames


def render_blocking_clip(
    start_state: Scene,
    end_state: Scene,
    out_path: Path,
    seconds: float = 4.0,
    fps: int = 12,
    prop_heights: Optional[dict[str, float]] = None,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """
    A split-screen clip of the 3D stage going from `start_state` to `end_state`: the blocking pass.

    It is shown at once while the final clip renders, it is the fallback when no final clip can be made, and it is
    the motion reference the video model repaints in the `stage3d_video` render base.
    """
    import subprocess

    steps = max(2, int(round(seconds * fps)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe, "-nostdin", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SPLIT_W}x{SPLIT_H}", "-framerate", str(fps), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for state, overrides in blocking_frames(start_state, end_state, steps):
            frame = compose_split_stage_image(state, prop_heights, overrides, supersample=1)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        if proc.wait(timeout=120) != 0:
            raise RuntimeError("ffmpeg could not encode the blocking clip")
    finally:
        if proc.poll() is None:
            proc.kill()
    return out_path
