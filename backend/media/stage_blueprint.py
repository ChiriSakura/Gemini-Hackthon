"""
Pure-Backend 2.5D Geometric Stage Blueprint Renderer & Layer-3 Anchoring A/B Benchmark
(Owned by P2 / worker-1, Zero-Conflict Optimization).

Feature & Use Cases:
- Projects the exact 3x3 StageState glass conservatory world (`gridToWorld` and SI
  metric heights: 1.62m Lia, 0.34m Nyx, 0.75m round table, 0.32m crystal vase)
  through the exact same dual-camera perspective math as `ThreeStageManager.ts`:
  - Camera A (front wide): FOV 44°, pos `(0, 2.75, 6.2)`, lookAt `(0, 0.9, 0)`
  - Camera B (45° oblique close-up): FOV 36°, pos `(3.25, 1.95, 3.55)`, lookAt `(0, 0.9, 0)`
- Composites transparent PNG cutouts (`lia.png` / `nyx.png` / `crystal_vase.png` /
  `crystal_vase_broken.png` / `star_token.png`) with ground contact shadows, arched
  nighttime conservatory windows, and weather atmosphere (`warm_dim`, `cold_blue` rain,
  `warm_transition`, `warm_gold_snow`) — with ZERO text overlays or coordinate axes.
- Generates reusable `640x360` fallback start-frame blueprints under
  `backend/assets/previews/blueprint_*.png` whenever frontend WebGL previews are absent.
- Optional `--ab-bench` runs a live Omni Flash + Decisions API A/B experiment comparing
  Text+Refs Only vs Blueprint First-Frame Anchoring on dual-camera spatial/angle lock.
"""

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env", override=False)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from backend import config
from backend.director.genai_client import build_decision_input, create_decision
from backend.director.state_tree import get_branch
from backend.logger import log_function_call
from backend.media.prop_maker import estimate_prop_height_m
from backend.media.video_renderer import render_scene_media
from backend.schemas import Scene

WIDTH = 640
HEIGHT = 360

LIGHTING_THEMES = {
    "warm_dim": {
        "sky_top": (10, 15, 32),
        "sky_bot": (22, 31, 56),
        "floor_a": (28, 36, 54),
        "floor_b": (36, 46, 68),
        "mullion": (48, 42, 46),
        "lantern": (255, 200, 108),
        "weather": "rain_light",
        "tint": (255, 232, 195, 18),
    },
    "cold_blue": {
        "sky_top": (6, 14, 34),
        "sky_bot": (15, 34, 68),
        "floor_a": (18, 32, 56),
        "floor_b": (24, 43, 72),
        "mullion": (26, 40, 62),
        "lantern": (120, 185, 255),
        "weather": "rain_heavy",
        "tint": (75, 150, 245, 28),
    },
    "warm_transition": {
        "sky_top": (12, 18, 38),
        "sky_bot": (34, 36, 58),
        "floor_a": (35, 40, 56),
        "floor_b": (45, 50, 68),
        "mullion": (56, 46, 44),
        "lantern": (255, 208, 120),
        "weather": "snow_light",
        "tint": (255, 214, 130, 22),
    },
    "warm_gold_snow": {
        "sky_top": (13, 17, 36),
        "sky_bot": (38, 36, 52),
        "floor_a": (38, 40, 54),
        "floor_b": (52, 52, 66),
        "mullion": (62, 50, 44),
        "lantern": (255, 224, 130),
        "weather": "snow_gold",
        "tint": (255, 218, 120, 32),
    },
}


class PerspectiveCamera:
    """Minimal right-handed 3D perspective camera matching Three.js PerspectiveCamera."""

    def __init__(self, fov_deg: float, pos: tuple[float, float, float], look_at: tuple[float, float, float]) -> None:
        self.pos = np.array(pos, dtype=np.float64)
        target = np.array(look_at, dtype=np.float64)
        forward = target - self.pos
        self.forward = forward / np.linalg.norm(forward)
        world_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        right = np.cross(self.forward, world_up)
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, self.forward)
        self.f = (HEIGHT / 2.0) / math.tan(math.radians(fov_deg) / 2.0)

    def project(self, pt: tuple[float, float, float]) -> Optional[tuple[float, float, float]]:
        """Project a 3D world point `(x, y, z)` to `(screen_x, screen_y, depth)`."""
        rel = np.array(pt, dtype=np.float64) - self.pos
        depth = float(np.dot(rel, self.forward))
        if depth <= 0.15:
            return None
        cx = float(np.dot(rel, self.right))
        cy = float(np.dot(rel, self.up))
        sx = (WIDTH / 2.0) + (cx / depth) * self.f
        sy = (HEIGHT / 2.0) - (cy / depth) * self.f
        return sx, sy, depth


def grid_to_world(grid: list[float]) -> tuple[float, float]:
    """Mirror `ThreeStageManager.ts` `gridToWorld([col, row])` exactly."""
    col = float(grid[0]) if len(grid) >= 1 else 1.0
    row = float(grid[1]) if len(grid) >= 2 else 1.0
    return (col - 1.0) * 2.1, (row - 1.0) * 1.7


def _load_rgba_sprite(path: Path) -> Optional[Image.Image]:
    """Load a local PNG sprite as RGBA."""
    if not path.is_file():
        return None
    with Image.open(path) as im:
        return im.convert("RGBA")


def _resolve_actor_sprite(actor_id: str, holding_prop: Optional[str]) -> Optional[Image.Image]:
    """Pick clean umbrella-free Lia ref when Lia is NOT holding the red umbrella (border-floodfill white removal)."""
    if actor_id == "lia":
        if holding_prop != "red_umbrella":
            for candidate in (
                config.ACTORS_DIR / "lia_clean_ref.png",
                config.BASE_DIR.parent / "StageState" / "backend" / "assets" / "actors" / "lia_clean_ref.png",
            ):
                if candidate.is_file():
                    with Image.open(candidate) as im:
                        rgba = np.array(im.convert("RGBA"), dtype=np.uint8)
                    # Border-connected floodfill only so Lia's silver hair & white eye highlights stay opaque.
                    is_white = (
                        (rgba[:, :, 0] > 232) & (rgba[:, :, 1] > 232) & (rgba[:, :, 2] > 232)
                    ).astype(np.uint8)
                    _, labels = cv2.connectedComponents(is_white, connectivity=4)
                    border_lbls = (
                        set(np.unique(labels[0, :]))
                        | set(np.unique(labels[-1, :]))
                        | set(np.unique(labels[:, 0]))
                        | set(np.unique(labels[:, -1]))
                    )
                    border_lbls.discard(0)
                    bg_mask = np.isin(labels, list(border_lbls))
                    rgba[bg_mask, 3] = 0
                    out = Image.fromarray(rgba, mode="RGBA")
                    bbox = out.getbbox()
                    return out.crop(bbox) if bbox else out
        return _load_rgba_sprite(config.SHARED_ASSETS_DIR / "lia.png")
    if actor_id == "nyx":
        return _load_rgba_sprite(config.SHARED_ASSETS_DIR / "nyx.png")
    return None


def _resolve_prop_sprite(prop_id: str, state: str, on_floor: bool) -> Optional[Image.Image]:
    """Resolve local transparent PNG cutout for a stage prop."""
    if prop_id == "crystal_vase":
        if state == "broken" or on_floor:
            return _load_rgba_sprite(config.PROPS_DIR / "crystal_vase_broken.png")
        return _load_rgba_sprite(config.PROPS_DIR / "crystal_vase.png") or _load_rgba_sprite(
            config.SHARED_ASSETS_DIR / "crystal_vase.png"
        )
    if prop_id == "star_token":
        return _load_rgba_sprite(config.PROPS_DIR / "star_token.png")
    candidate = config.PROPS_DIR / f"{prop_id}.png"
    return _load_rgba_sprite(candidate)


def _draw_poly(draw: ImageDraw.ImageDraw, cam: PerspectiveCamera, pts3d: list[tuple[float, float, float]], fill, outline=None) -> None:
    """Project a 3D polygon and draw it if all vertices sit in front of the camera."""
    projected = [cam.project(p) for p in pts3d]
    if any(p is None for p in projected):
        return
    xy = [(p[0], p[1]) for p in projected if p is not None]
    draw.polygon(xy, fill=fill, outline=outline)


def _draw_line3d(draw: ImageDraw.ImageDraw, cam: PerspectiveCamera, a: tuple[float, float, float], b: tuple[float, float, float], fill, width: int = 2) -> None:
    """Project a 3D line segment onto the 2D blueprint canvas."""
    pa = cam.project(a)
    pb = cam.project(b)
    if pa is None or pb is None:
        return
    draw.line([(pa[0], pa[1]), (pb[0], pb[1])], fill=fill, width=width)


def _draw_conservatory_backdrop(canvas: Image.Image, cam: PerspectiveCamera, theme: dict) -> None:
    """Render nighttime glasshouse gradient sky, tiled 3x3 conservatory floor, walls, and lanterns."""
    draw = ImageDraw.Draw(canvas, "RGBA")

    # 1. Night sky vertical gradient
    top = np.array(theme["sky_top"], dtype=np.float32)
    bot = np.array(theme["sky_bot"], dtype=np.float32)
    t = np.linspace(0.0, 1.0, HEIGHT)[:, None, None]
    grad = np.clip((1.0 - t) * top + t * bot, 0, 255).astype(np.uint8)
    grad_img = np.repeat(grad, WIDTH, axis=1)
    canvas.paste(Image.fromarray(grad_img, mode="RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")

    # 2. Conservatory back & side glass walls (mullions at z=-3.2, x=+-3.5)
    mullion = (*theme["mullion"], 235)
    glass_tint = (32, 48, 78, 52)
    _draw_poly(draw, cam, [(-3.5, 0.0, -3.2), (3.5, 0.0, -3.2), (3.5, 3.6, -3.2), (-3.5, 3.6, -3.2)], glass_tint, mullion)
    _draw_poly(draw, cam, [(-3.5, 0.0, 3.2), (-3.5, 0.0, -3.2), (-3.5, 3.6, -3.2), (-3.5, 3.6, 3.2)], glass_tint, mullion)
    _draw_poly(draw, cam, [(3.5, 0.0, -3.2), (3.5, 0.0, 3.2), (3.5, 3.6, 3.2), (3.5, 3.6, -3.2)], glass_tint, mullion)

    # Vertical window frames + waist rail
    for x in np.linspace(-3.5, 3.5, 6):
        _draw_line3d(draw, cam, (float(x), 0.0, -3.2), (float(x), 3.6, -3.2), mullion, 3)
    for z in np.linspace(-3.2, 3.2, 5):
        _draw_line3d(draw, cam, (-3.5, 0.0, float(z)), (-3.5, 3.6, float(z)), mullion, 2)
        _draw_line3d(draw, cam, (3.5, 0.0, float(z)), (3.5, 3.6, float(z)), mullion, 2)
    for y in (0.75, 2.55, 3.55):
        _draw_line3d(draw, cam, (-3.5, y, -3.2), (3.5, y, -3.2), mullion, 3)
        _draw_line3d(draw, cam, (-3.5, y, -3.2), (-3.5, y, 3.2), mullion, 2)
        _draw_line3d(draw, cam, (3.5, y, -3.2), (3.5, y, 3.2), mullion, 2)

    # Warm wall lanterns along back glass wall
    lr, lg, lb = theme["lantern"]
    for lx in (-2.1, 2.1):
        lp = cam.project((lx, 2.15, -3.1))
        if lp is not None:
            rad = max(8, int(90 / lp[2]))
            draw.ellipse([(lp[0] - rad * 2, lp[1] - rad * 2), (lp[0] + rad * 2, lp[1] + rad * 2)], fill=(lr, lg, lb, 45))
            draw.ellipse([(lp[0] - rad // 2, lp[1] - rad // 2), (lp[0] + rad // 2, lp[1] + rad // 2)], fill=(lr, lg, lb, 220))

    # 3. 3x3 tiled glasshouse floor (x in [-3.15..3.15], z in [-2.55..2.55])
    xs = np.linspace(-3.15, 3.15, 7)
    zs = np.linspace(-2.55, 2.55, 7)
    for ix in range(len(xs) - 1):
        for iz in range(len(zs) - 1):
            col = theme["floor_a"] if (ix + iz) % 2 == 0 else theme["floor_b"]
            _draw_poly(
                draw,
                cam,
                [
                    (float(xs[ix]), 0.0, float(zs[iz])),
                    (float(xs[ix + 1]), 0.0, float(zs[iz])),
                    (float(xs[ix + 1]), 0.0, float(zs[iz + 1])),
                    (float(xs[ix]), 0.0, float(zs[iz + 1])),
                ],
                (*col, 245),
                (70, 88, 118, 120),
            )


def _draw_center_table(canvas: Image.Image, cam: PerspectiveCamera) -> None:
    """Draw the central 0.75m-high round glasshouse table at world origin `(0, 0, 0)`."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    r_top = 0.56
    h_top = 0.75
    n = 28

    # Soft floor shadow under table
    shadow_pts = [(r_top * 1.15 * math.cos(2 * math.pi * i / n), 0.01, r_top * 1.15 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    _draw_poly(draw, cam, shadow_pts, (4, 8, 16, 130))

    # Central pedestal & curved legs
    for ang_deg in (30, 150, 270):
        rad = math.radians(ang_deg)
        _draw_line3d(draw, cam, (0.0, 0.25, 0.0), (0.38 * math.cos(rad), 0.0, 0.38 * math.sin(rad)), (68, 56, 52, 255), 4)
    _draw_line3d(draw, cam, (0.0, 0.08, 0.0), (0.0, h_top, 0.0), (74, 62, 58, 255), 6)

    # Round glass tabletop
    rim_pts = [(r_top * math.cos(2 * math.pi * i / n), h_top, r_top * math.sin(2 * math.pi * i / n)) for i in range(n)]
    _draw_poly(draw, cam, rim_pts, (130, 168, 196, 165), (180, 150, 112, 245))


def _paste_billboard(
    canvas: Image.Image,
    cam: PerspectiveCamera,
    sprite: Image.Image,
    world_pos: tuple[float, float, float],
    world_height_m: float,
    flip_x: bool = False,
    glow_rgb: Optional[tuple[int, int, int]] = None,
) -> None:
    """Composite an RGBA sprite anchored bottom-center at `world_pos` with perspective SI height."""
    base = cam.project(world_pos)
    top = cam.project((world_pos[0], world_pos[1] + world_height_m, world_pos[2]))
    if base is None or top is None:
        return

    px_h = max(14, int(round(abs(base[1] - top[1]))))
    ar = sprite.width / max(1, sprite.height)
    px_w = max(12, int(round(px_h * ar)))

    img = sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip_x else sprite
    resized = img.resize((px_w, px_h), Image.Resampling.LANCZOS)

    # Contact shadow directly underneath base point
    draw = ImageDraw.Draw(canvas, "RGBA")
    sh_rx = max(8, int(px_w * 0.34))
    sh_ry = max(3, int(sh_rx * 0.26))
    draw.ellipse([(base[0] - sh_rx, base[1] - sh_ry), (base[0] + sh_rx, base[1] + sh_ry)], fill=(4, 8, 18, 135))

    if glow_rgb:
        gr = max(px_w, px_h)
        glow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer, "RGBA")
        cx, cy = int(base[0]), int(base[1] - px_h * 0.5)
        gdraw.ellipse([(cx - gr, cy - gr), (cx + gr, cy + gr)], fill=(*glow_rgb, 95))
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=max(6, gr // 5)))
        canvas.alpha_composite(glow_layer)

    paste_x = int(round(base[0] - px_w / 2.0))
    paste_y = int(round(base[1] - px_h))
    canvas.alpha_composite(resized, dest=(paste_x, paste_y))


def _draw_closed_red_umbrella_on_floor(canvas: Image.Image, cam: PerspectiveCamera, wx: float, wz: float) -> None:
    """Draw a simple closed red umbrella resting flat on the conservatory floor."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_line3d(draw, cam, (wx - 0.38, 0.04, wz), (wx + 0.38, 0.04, wz + 0.08), (190, 28, 42, 255), 8)
    _draw_line3d(draw, cam, (wx + 0.38, 0.04, wz + 0.08), (wx + 0.48, 0.06, wz + 0.12), (96, 56, 36, 255), 4)


def _draw_weather_overlay(canvas: Image.Image, theme: dict, seed: int) -> None:
    """Add rain streaks or warm golden snowflakes matching the scene's lighting preset."""
    rng = np.random.default_rng(seed)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    mode = theme["weather"]
    if mode in ("rain_light", "rain_heavy"):
        count = 130 if mode == "rain_heavy" else 55
        for _ in range(count):
            x = int(rng.integers(0, WIDTH))
            y = int(rng.integers(0, HEIGHT))
            length = int(rng.integers(14, 28))
            draw.line([(x, y), (x - 4, y + length)], fill=(140, 210, 255, 95 if mode == "rain_heavy" else 55), width=1)
    elif mode in ("snow_light", "snow_gold"):
        count = 110 if mode == "snow_gold" else 50
        color = (255, 232, 145, 185) if mode == "snow_gold" else (235, 245, 255, 145)
        for _ in range(count):
            x = int(rng.integers(0, WIDTH))
            y = int(rng.integers(0, HEIGHT))
            r = int(rng.integers(1, 4))
            draw.ellipse([(x - r, y - r), (x + r, y + r)], fill=color)
    # Subtle atmospheric tint
    tr, tg, tb, ta = theme["tint"]
    draw.rectangle([(0, 0), (WIDTH, HEIGHT)], fill=(tr, tg, tb, ta))
    canvas.alpha_composite(overlay)


@log_function_call
def render_scene_blueprint(scene: Scene, cam_id: str, output_path: Path) -> Path:
    """Render a 640x360 2.5D geometric blueprint PNG matching Three.js Cam A or Cam B."""
    cam = (
        PerspectiveCamera(44.0, (0.0, 2.75, 6.2), (0.0, 0.9, 0.0))
        if cam_id == "cam_a"
        else PerspectiveCamera(36.0, (3.25, 1.95, 3.55), (0.0, 0.9, 0.0))
    )
    theme = LIGHTING_THEMES.get(scene.lighting, LIGHTING_THEMES["warm_dim"])
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (9, 13, 24, 255))

    _draw_conservatory_backdrop(canvas, cam, theme)

    # Painter's algorithm: collect table, actors, and props sorted far-to-near by camera depth
    drawables: list[tuple[float, callable]] = []

    table_proj = cam.project((0.0, 0.35, 0.0))
    if table_proj is not None:
        drawables.append((table_proj[2], lambda: _draw_center_table(canvas, cam)))

    for actor in scene.actors:
        wx, wz = grid_to_world(actor.grid)
        sprite = _resolve_actor_sprite(actor.id, actor.holding_prop)
        if sprite is None:
            continue
        if actor.id == "lia":
            h_m = 1.15 if actor.action == "kneel" else 1.62
            flip = actor.facing == "left"
        else:
            # Strict SI scale: realistic seated black cat = 0.30m (below Lia's knee).
            h_m = 0.30
            flip = actor.facing == "right"
        proj = cam.project((wx, 0.0, wz))
        if proj is not None:
            drawables.append(
                (
                    proj[2],
                    lambda s=sprite, pos=(wx, 0.0, wz), hm=h_m, fl=flip: _paste_billboard(canvas, cam, s, pos, hm, flip_x=fl),
                )
            )

    for prop in scene.stage_props:
        wx, wz = grid_to_world(prop.grid)
        if prop.id == "red_umbrella" and prop.on_floor:
            proj = cam.project((wx, 0.04, wz))
            if proj is not None:
                drawables.append((proj[2], lambda x=wx, z=wz: _draw_closed_red_umbrella_on_floor(canvas, cam, x, z)))
            continue

        sprite = _resolve_prop_sprite(prop.id, prop.state, prop.on_floor)
        if sprite is None:
            continue
        wy = 0.02 if prop.on_floor else 0.75
        if prop.id == "crystal_vase":
            hm = 0.16 if prop.state == "broken" or prop.on_floor else 0.32
            glow = (96, 175, 255) if prop.state != "broken" else None
        elif prop.id == "star_token":
            hm = 0.10
            glow = (255, 215, 70)
        else:
            hm = estimate_prop_height_m(prop.id)
            glow = None
        proj = cam.project((wx, wy, wz))
        if proj is not None:
            drawables.append(
                (
                    proj[2],
                    lambda s=sprite, pos=(wx, wy, wz), h=hm, g=glow: _paste_billboard(canvas, cam, s, pos, h, glow_rgb=g),
                )
            )

    drawables.sort(key=lambda item: item[0], reverse=True)
    for _, fn in drawables:
        fn()

    _draw_weather_overlay(canvas, theme, seed=scene.scene_id * 101 + (1 if cam_id == "cam_a" else 2))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG")
    return output_path


@log_function_call
def render_all_cached_blueprints() -> dict:
    """Pre-render all 26 dual-camera 2.5D blueprint frames across all 4 cached branches (W1-T7A)."""
    t0 = time.perf_counter()
    branch_specs = [
        ("branch_v1", "branch_v1", 1),
        ("branch_v2_cached", "branch_v2", 2),
        ("branch_v3_starlight_cached", "branch_v3", 2),
        ("branch_v4_umbrella_shield_cached", "branch_v4", 2),
    ]

    generated: list[dict] = []
    branches_covered: list[str] = []
    for branch_id, prefix, min_scene_id in branch_specs:
        branch = get_branch(branch_id)
        if branch is None:
            continue
        branches_covered.append(branch_id)
        for scene in branch.scenes:
            if scene.scene_id < min_scene_id:
                continue
            for cam_id in ("cam_a", "cam_b"):
                out = config.PREVIEWS_DIR / f"blueprint_{prefix}_s{scene.scene_id}_{cam_id}.png"
                render_scene_blueprint(scene, cam_id, out)
                generated.append(
                    {
                        "branch_id": branch_id,
                        "branch": prefix,
                        "scene_id": scene.scene_id,
                        "cam_id": cam_id,
                        "file": out.name,
                        "asset_url": f"/assets/previews/{out.name}",
                        "bytes": out.stat().st_size,
                    }
                )

    bp_summary = {
        "total_blueprints": len(generated),
        "branches_covered": branches_covered,
        "si_metric_scales": {"lia_m": 1.62, "nyx_m": 0.30, "table_m": 0.75, "crystal_vase_m": 0.32, "star_token_m": 0.10},
        "resolution": f"{WIDTH}x{HEIGHT}",
        "elapsed_sec": round(time.perf_counter() - t0, 2),
        "files": generated,
    }

    # Refresh eval_blueprint_anchoring.json preserving existing live A/B numbers while attaching 4-branch manifest
    anchor_json = config.ASSETS_DIR / "eval_blueprint_anchoring.json"
    existing = {}
    if anchor_json.is_file():
        try:
            existing = json.loads(anchor_json.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing["blueprints_manifest"] = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_blueprints": bp_summary["total_blueprints"],
        "branches_covered": bp_summary["branches_covered"],
        "si_metric_scales": bp_summary["si_metric_scales"],
        "resolution": bp_summary["resolution"],
    }
    anchor_json.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return bp_summary


@log_function_call
async def run_blueprint_anchoring_ab_benchmark() -> dict:
    """
    Live A/B experiment on `branch_v1` Scene 4: compare existing unanchored Cam A/B clips vs
    2.5D Blueprint-Anchored Cam A/B renders on dual-camera spatial & 45-degree angle fidelity
    using `models/gemini-decision-1`.
    """
    b1 = get_branch("branch_v1")
    assert b1 is not None
    s4 = next(s for s in b1.scenes if s.scene_id == 4)

    bp_a = config.PREVIEWS_DIR / "blueprint_branch_v1_s4_cam_a.png"
    bp_b = config.PREVIEWS_DIR / "blueprint_branch_v1_s4_cam_b.png"
    if not bp_a.is_file():
        render_scene_blueprint(s4, "cam_a", bp_a)
    if not bp_b.is_file():
        render_scene_blueprint(s4, "cam_b", bp_b)

    refs = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.PROPS_DIR / "crystal_vase_broken.png"),
    ]

    t0 = time.perf_counter()
    anchored_media = await render_scene_media(
        s4.model_copy(deep=True),
        refs,
        cam_a_preview_path=str(bp_a),
        cam_b_preview_path=str(bp_b),
        overwrite_canonical=False,
    )
    render_sec = round(time.perf_counter() - t0, 2)

    q_map = {
        "angle_separation_45deg": {
            "type": "noul",
            "instructions": (
                "Is Image 1 clearly a wider front-center establishing shot and Image 2 clearly a distinct "
                "diagonal (~45-degree) closer side angle of the same room?"
            ),
        },
        "spatial_consistency": {
            "type": "noul",
            "instructions": (
                "Do Image 1 and Image 2 keep the girl, center round table, and broken vase shards on the floor "
                "in consistent relative spatial positions?"
            ),
        },
        "lighting_consistency": {
            "type": "noul",
            "instructions": "Do both Camera A and Camera B share matching cold rainy nighttime conservatory lighting?",
        },
        "physical_scale": {
            "type": "noul",
            "instructions": "Are character proportions, table height, and resting objects physically grounded without floating?",
        },
    }

    async def _eval_pair(label: str, tail_a: Path, tail_b: Path) -> dict:
        t_ev = time.perf_counter()
        payload = build_decision_input(
            f"Evaluate dual-camera consistency for {label}. Image 1 = Cam A, Image 2 = Cam B.",
            [tail_a, tail_b],
        )
        res = await create_decision(payload, q_map)
        answers = getattr(res, "answers", None) or {}
        checks = {}
        for k in q_map:
            ans = answers.get(k)
            p_true = round(float(getattr(ans, "noul", 0.5)), 3) if ans is not None else 0.5
            checks[k] = {"p_true": p_true, "passed": p_true >= 0.5}
        avg_p = round(sum(c["p_true"] for c in checks.values()) / len(checks), 3)
        pass_rate = round(sum(1 for c in checks.values() if c["passed"]) / len(checks), 3)
        return {
            "label": label,
            "avg_p_true": avg_p,
            "pass_rate": pass_rate,
            "elapsed_sec": round(time.perf_counter() - t_ev, 2),
            "checks": checks,
        }

    base_a = config.ASSETS_DIR / (s4.media.cam_a_tail_frame_url or "").removeprefix("/assets/")
    base_b = config.ASSETS_DIR / (s4.media.cam_b_tail_frame_url or "").removeprefix("/assets/")
    anch_a = config.ASSETS_DIR / (anchored_media.cam_a_tail_frame_url or "").removeprefix("/assets/")
    anch_b = config.ASSETS_DIR / (anchored_media.cam_b_tail_frame_url or "").removeprefix("/assets/")

    baseline_eval, anchored_eval = await asyncio.gather(
        _eval_pair("unanchored_text_plus_refs_only", base_a, base_b),
        _eval_pair("blueprint_2_5d_first_frame_anchored", anch_a, anch_b),
    )

    # Clean up temporary rendered MP4/tail files from the A/B test to keep repo lean
    for url in (
        anchored_media.cam_a_final_url,
        anchored_media.cam_b_final_url,
        anchored_media.cam_a_tail_frame_url,
        anchored_media.cam_b_tail_frame_url,
    ):
        if url and url.startswith("/assets/"):
            (config.ASSETS_DIR / url.removeprefix("/assets/")).unlink(missing_ok=True)

    summary = {
        "benchmark": "Layer-3 2.5D Blueprint Start-Frame Anchoring A/B Test",
        "scene_tested": "branch_v1 Scene 4 (Cam A + Cam B)",
        "model_video": config.MODEL_VIDEO,
        "model_decision": config.MODEL_DECISION,
        "anchored_dual_cam_render_sec": render_sec,
        "anchored_qa_score": anchored_media.qa_score,
        "baseline_unanchored": baseline_eval,
        "blueprint_anchored": anchored_eval,
        "delta_avg_p_true": round(anchored_eval["avg_p_true"] - baseline_eval["avg_p_true"], 3),
        "delta_pass_rate": round(anchored_eval["pass_rate"] - baseline_eval["pass_rate"], 3),
    }
    out_path = config.ASSETS_DIR / "eval_blueprint_anchoring.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render pure-backend 2.5D stage blueprints and optional A/B bench.")
    parser.add_argument("--ab-bench", action="store_true", help="Run live Omni Flash Layer-3 anchoring A/B test.")
    args = parser.parse_args()

    bp_report = render_all_cached_blueprints()
    out: dict = {"blueprints": bp_report}
    if args.ab_bench:
        out["ab_benchmark"] = asyncio.run(run_blueprint_anchoring_ab_benchmark())
    print(json.dumps(out, ensure_ascii=False, indent=2))
