"""
Transparent PNG Tight-Crop & Chroma-Green Halo Audit Tool (Owned by P2 / worker-1, W1-T4D).

Feature & Use Cases:
- Audits every PNG asset under `backend/assets/props/` (`crystal_vase.png`,
  `crystal_vase_broken.png`, `star_token.png`, `showcase_*.png`, etc.):
  1. Verifies RGBA mode and real alpha transparency.
  2. Verifies tight bottom-flush cropping (`bottom_opaque_gap_px <= 2`) so
     Three.js sprites anchored at `center=(0.5, 0.0)` rest naturally on tables
     and floors without floating.
  3. Computes exact `width`, `height`, and `width / height` aspect ratios.
  4. Verifies `0` chroma-green spill / halo pixels across opaque and edge pixels.
- Provides `--fix` sanitization mode to trim excess bottom transparent padding
  and despill faint translucent green chroma-key fringe without altering opaque art.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import cv2
import numpy as np
from PIL import Image

from backend import config
from backend.logger import log_function_call


def _compute_green_halo_mask(rgba: np.ndarray) -> np.ndarray:
    """Return boolean mask of chroma-green spill pixels using HSV hue + green dominance."""
    rgb = rgba[:, :, :3]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    green_excess = g - np.maximum(r, b)
    return (
        (hsv[:, :, 0] >= 38)
        & (hsv[:, :, 0] <= 74)
        & (hsv[:, :, 1] >= 85)
        & (hsv[:, :, 2] >= 45)
        & (green_excess > 24)
    )


@log_function_call
def sanitize_prop_png(png_path: Path, max_size: int = 512) -> dict:
    """
    Remove residual translucent chroma-green halo fringe and apply bottom-flush tight
    cropping (`bottom_gap == 0px`) to a transparent prop PNG in place.
    """
    with Image.open(png_path) as im:
        rgba_img = im.convert("RGBA")
        arr = np.array(rgba_img, dtype=np.uint8)

    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    a = arr[:, :, 3].astype(np.uint8)
    green_mask = _compute_green_halo_mask(arr)

    # 1. Clear faint semi-transparent green fringe completely and despill remaining translucent edge pixels.
    clear_fringe = green_mask & (a > 0) & (a < 180)
    a = np.where(clear_fringe, 0, a).astype(np.uint8)

    # Despill any remaining green-dominant translucent or boundary pixels (alpha < 252)
    despill = (a > 0) & (a < 252) & (g > np.maximum(r, b))
    g = np.where(despill, np.maximum(r, b), g)

    # Also suppress any isolated residual bright chroma-green edge specks directly adjacent to transparency
    # AND clear tiny trapped chroma-green cutouts inside chain links / key filigree (area <= 250px).
    if np.any(green_mask & (a >= 180)):
        transparent = (a == 0).astype(np.uint8)
        near_bg = cv2.dilate(transparent, np.ones((7, 7), np.uint8), iterations=1) > 0
        edge_green = green_mask & near_bg
        g = np.where(edge_green, np.maximum(r, b), g)

        # Tiny trapped chroma-green holes (e.g., chain links, scrollwork) not touching outer transparent border
        rem_green = ((g - np.maximum(r, b)) > 18) & (a > 0)
        rem_u8 = (rem_green & green_mask).astype(np.uint8)
        n_lbl, lbls, st, _ = cv2.connectedComponentsWithStats(rem_u8, connectivity=8)
        for lbl in range(1, n_lbl):
            area = int(st[lbl, cv2.CC_STAT_AREA])
            if area <= 250:
                reg = lbls == lbl
                med_ex = float(np.median((g - np.maximum(r, b))[reg]))
                if med_ex >= 50.0:
                    # True trapped bright-green chroma hole -> punch transparent cleanly
                    a = np.where(reg, 0, a).astype(np.uint8)
                else:
                    # Minor metallic green bounce reflection -> neutral despill
                    g = np.where(reg, np.maximum(r, b), g)

    # Zero out invisible sub-threshold alpha dust (< 16) so bounding box is crisp
    a = np.where(a < 16, 0, a).astype(np.uint8)

    cleaned = np.stack(
        [
            np.clip(r, 0, 255).astype(np.uint8),
            np.clip(g, 0, 255).astype(np.uint8),
            np.clip(b, 0, 255).astype(np.uint8),
            a,
        ],
        axis=-1,
    )
    out_img = Image.fromarray(cleaned, mode="RGBA")

    # 2. Tight crop with small side/top breathing room and 0px bottom-flush alignment
    ys, xs = np.where(a >= 16)
    if len(ys) > 0 and len(xs) > 0:
        pad = 6
        x0 = max(0, int(xs.min()) - pad)
        y0 = max(0, int(ys.min()) - pad)
        x1 = min(out_img.width, int(xs.max()) + 1 + pad)
        y1 = int(ys.max()) + 1  # flush bottom: bottom_opaque_gap_px == 0
        out_img = out_img.crop((x0, y0, x1, y1))

    if max(out_img.width, out_img.height) > max_size:
        out_img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        # Re-trim bottom after LANCZOS thumbnail in case anti-aliasing left 1-2 transparent bottom rows
        arr2 = np.array(out_img, dtype=np.uint8)
        ys2 = np.where(arr2[:, :, 3] >= 16)[0]
        if len(ys2) > 0:
            arr2[:, :, 3] = np.where(arr2[:, :, 3] < 16, 0, arr2[:, :, 3])
            out_img = Image.fromarray(arr2[: int(ys2.max()) + 1, :, :], mode="RGBA")

    out_img.save(png_path, format="PNG")
    return audit_single_prop(png_path)


def audit_single_prop(png_path: Path) -> dict:
    """Compute RGBA, tight bottom-flush crop, aspect ratio, and green-halo audit metrics."""
    with Image.open(png_path) as im:
        mode = im.mode
        rgba = np.array(im.convert("RGBA"), dtype=np.uint8)

    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3]
    has_transparency = bool(np.any(alpha < 255))

    ys_any, xs_any = np.where(alpha > 0)
    ys_op, xs_op = np.where(alpha >= 16)

    bottom_any_gap = (h - 1 - int(ys_any.max())) if len(ys_any) else h
    bottom_opaque_gap = (h - 1 - int(ys_op.max())) if len(ys_op) else h
    bbox = (
        [int(xs_any.min()), int(ys_any.min()), int(xs_any.max()) + 1, int(ys_any.max()) + 1]
        if len(ys_any)
        else [0, 0, 0, 0]
    )

    green_mask = _compute_green_halo_mask(rgba)
    opaque_green_px = int(np.sum(green_mask & (alpha >= 128)))
    halo_green_px = int(np.sum(green_mask & (alpha > 0)))

    bottom_flush_ok = bottom_opaque_gap <= 2 and bottom_any_gap <= 2
    halo_free_ok = opaque_green_px == 0 and halo_green_px == 0
    passed = (mode == "RGBA") and has_transparency and bottom_flush_ok and halo_free_ok

    return {
        "file": png_path.name,
        "asset_url": f"/assets/props/{png_path.name}",
        "mode": mode,
        "has_transparency": has_transparency,
        "width": int(w),
        "height": int(h),
        "aspect_ratio": round(w / h, 4) if h else 0.0,
        "alpha_bbox": bbox,
        "bottom_opaque_gap_px": int(bottom_opaque_gap),
        "bottom_any_gap_px": int(bottom_any_gap),
        "bottom_flush_ok": bottom_flush_ok,
        "opaque_green_spill_px": opaque_green_px,
        "halo_green_pixels": halo_green_px,
        "halo_free_ok": halo_free_ok,
        "file_size_bytes": png_path.stat().st_size,
        "passed": passed,
    }


@log_function_call
def audit_all_props(
    props_dir: Optional[Path] = None,
    fix: bool = False,
    output_json: Optional[Path] = None,
) -> dict:
    """Audit (and optionally sanitize) every PNG file in `backend/assets/props/`."""
    target_dir = props_dir or config.PROPS_DIR
    png_files = sorted(target_dir.glob("*.png"))
    results = [sanitize_prop_png(p) if fix else audit_single_prop(p) for p in png_files]

    summary = {
        "props_dir": str(target_dir),
        "total_props": len(results),
        "all_rgba": all(r["mode"] == "RGBA" and r["has_transparency"] for r in results),
        "all_bottom_flush_ok": all(r["bottom_flush_ok"] for r in results),
        "all_zero_opaque_green_spill": all(r["opaque_green_spill_px"] == 0 for r in results),
        "all_zero_green_halo": all(r["halo_green_pixels"] == 0 for r in results),
        "all_passed": all(r["passed"] for r in results),
        "max_bottom_opaque_gap_px": max((r["bottom_opaque_gap_px"] for r in results), default=0),
        "total_green_halo_pixels": sum(r["halo_green_pixels"] for r in results),
        "props": results,
    }

    out_path = output_json or (config.ASSETS_DIR / "eval_props_audit.json")
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit and optionally sanitize StageState prop PNGs.")
    parser.add_argument("--fix", action="store_true", help="Remove translucent green halo and trim bottom padding.")
    args = parser.parse_args()
    report = audit_all_props(fix=args.fix)
    print(json.dumps(report, ensure_ascii=False, indent=2))
