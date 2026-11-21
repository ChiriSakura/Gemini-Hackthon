"""
Neutral-White 2D Anime Category Template Generator (Owned by P2 / worker-1, W1-V3-04).

Feature & Use Cases:
- Pre-generates 5 clean neutral-white / light-gray cel-shaded 2D anime base prop templates
  under `backend/assets/props/templates/` so `prop_maker.py` can recolor familiar everyday
  categories (`soda_can`, `smartphone`, `coffee_mug`, `snack_bag`, `laptop`) to ANY captured
  real-world color in < 0.2s without a 15s diffusion round-trip:
  1. `soda_can.png`
  2. `smartphone.png`
  3. `coffee_mug.png`
  4. `snack_bag.png`
  5. `laptop.png`
- Chroma-keys each template via `_chroma_key_green_to_rgba()` and sanitizes with
  `sanitize_prop_png()` to guarantee RGBA transparency, 0px bottom-flush alignment, and
  0 green halo pixels.
"""

import asyncio
import base64
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env", override=False)

from google import genai
from google.genai import types

from backend import config
from backend.logger import log_function_call, log_genai_call
from backend.media.audit_props import audit_single_prop, sanitize_prop_png
from backend.media.prop_maker import _chroma_key_green_to_rgba, _resolve_image_model, create_prop

import cv2
import json
import numpy as np

TEMPLATES_DIR = config.PROPS_DIR / "templates"

TEMPLATE_SPECS: list[tuple[str, str]] = [
    (
        "soda_can",
        (
            "Simple clean 2D anime cel-shaded game icon of a single blank neutral-white matte cylindrical "
            "aluminum soda beverage can standing upright with silver pull-tab top rim, subtle clean soft gray "
            "cel-shading, crisp dark outline, ZERO text, ZERO logos, ZERO graphics, ZERO stripes, NO hands, "
            "NO floor shadow. Completely isolated on a flat solid bright chroma-key green (#00FF00) background."
        ),
    ),
    (
        "smartphone",
        (
            "Simple clean 2D anime cel-shaded game icon of a single blank neutral-light-gray modern rectangular "
            "smartphone standing upright in clean front 3/4 view with slim bezel, clean glossy dark-slate screen "
            "surface and light-gray outer casing body, subtle cel-shading, crisp dark outline, ZERO text, ZERO icons, "
            "ZERO logos, NO hands, NO floor shadow. Completely isolated on a flat solid bright chroma-key green (#00FF00) background."
        ),
    ),
    (
        "coffee_mug",
        (
            "Simple clean 2D anime cel-shaded game icon of a single blank neutral-white ceramic coffee mug "
            "with sturdy side C-handle, front 3/4 view, smooth clean white body with soft gray cel-shading, "
            "crisp dark outline, ZERO text, ZERO logos, ZERO steam, NO hands, NO floor shadow. "
            "Completely isolated on a flat solid bright chroma-key green (#00FF00) background."
        ),
    ),
    (
        "snack_bag",
        (
            "Simple clean 2D anime cel-shaded game icon of a single blank neutral-white puffy foil snack chips "
            "pillow bag standing upright with neat crimped top and bottom edges, soft light-gray cel-shaded folds, "
            "crisp dark outline, ZERO text, ZERO logos, ZERO pictures on the bag, NO hands, NO floor shadow. "
            "Completely isolated on a flat solid bright chroma-key green (#00FF00) background."
        ),
    ),
    (
        "laptop",
        (
            "Simple clean 2D anime cel-shaded game icon of a single blank neutral-light-silver sleek open laptop "
            "computer in clean front 3/4 view resting flat on its base, light-gray metallic body with simple "
            "dark-slate screen and minimal keyboard outline, crisp dark outline, ZERO text, ZERO logos, "
            "NO hands, NO floor shadow. Completely isolated on a flat solid bright chroma-key green (#00FF00) background."
        ),
    ),
]


def _generate_one_template_sync(prompt: str) -> tuple[bytes, dict]:
    """Call `gemini-nano-banana-2.1` to generate a neutral chroma-green base template."""
    model_id = _resolve_image_model()
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000))
    t0 = time.perf_counter()
    interaction = client.interactions.create(
        model=model_id,
        input=prompt,
        response_format={"type": "image", "aspect_ratio": "1:1"},
    )
    output_image = getattr(interaction, "output_image", None)
    raw = None
    if output_image and getattr(output_image, "data", None):
        raw = base64.b64decode(output_image.data)
    else:
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", None) == "model_output":
                for block in getattr(step, "content", []) or []:
                    if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                        raw = base64.b64decode(block.data)
                        break
    if not raw:
        raise RuntimeError(f"Template image model '{model_id}' returned no image content.")
    return raw, {"model": model_id, "interaction_id": getattr(interaction, "id", None), "elapsed_sec": round(time.perf_counter() - t0, 2)}


@log_function_call
async def build_all_templates(force: bool = False) -> dict:
    """Generate and audit all 5 neutral 2D anime category templates concurrently."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    async def _build_one(cat: str, prompt: str) -> tuple[str, dict]:
        out_path = TEMPLATES_DIR / f"{cat}.png"
        if force or not out_path.is_file():
            raw, meta = await asyncio.to_thread(_generate_one_template_sync, prompt)
            rgba = await asyncio.to_thread(_chroma_key_green_to_rgba, raw, 384)
            rgba.save(out_path, format="PNG")
            audit = sanitize_prop_png(out_path, max_size=384)
            log_genai_call(meta["model"], {"template": cat}, {"aspect_ratio": "1:1"}, {**meta, "passed": audit["passed"]})
            return cat, {"meta": meta, "audit": audit}
        audit = audit_single_prop(out_path)
        if not audit["passed"]:
            audit = sanitize_prop_png(out_path, max_size=384)
        return cat, {"cached": True, "audit": audit}

    res = await asyncio.gather(*(_build_one(c, p) for c, p in TEMPLATE_SPECS))
    return dict(res)


@log_function_call
async def build_everyday_prop_kit() -> dict:
    """
    W1-V2-B3 + W1-V3-04: Generate the 4 user-requested everyday showcase props via `<0.2s`
    dominant-color template tinting, measure cold tint vs 0ms disk-cache reuse latencies,
    and verify 0 green halo & 0px bottom-flush via `audit_single_prop()`.
    """
    specs = [
        ("showcase_everyday_yellow_soda_can", "Yellow Soda Can", "Bright yellow cylindrical aluminum soda beverage can", (30, 220, 250)),
        ("showcase_everyday_chips_snack_bag", "Chips Snack Bag", "Vibrant orange crunchy potato chips foil snack bag", (35, 130, 245)),
        ("showcase_everyday_black_smartphone", "Black Smartphone", "Matte black modern rectangular mobile phone", (26, 28, 34)),
        ("showcase_everyday_silver_laptop", "Silver Laptop", "Brushed silver sleek open aluminum laptop computer", (200, 195, 190)),
    ]
    items_out: list[dict] = []
    for pid, name, desc, bgr in specs:
        frame = np.full((360, 480, 3), (45, 50, 60), dtype=np.uint8)
        frame[80:280, 120:360] = bgr
        ok, buf = cv2.imencode(".jpg", frame)
        assert ok
        t0 = time.perf_counter()
        out_id, item = await create_prop(
            name=name,
            description=desc,
            camera_frame_bytes=buf.tobytes(),
            prop_id=pid,
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        png_path = config.PROPS_DIR / f"{out_id}.png"
        aud = audit_single_prop(png_path)
        items_out.append(
            {
                "prop_id": out_id,
                "elapsed_ms": elapsed_ms,
                "item": item.model_dump(),
                "audit": aud,
            }
        )

    # Measure second-hit exact-match `(category, color_bucket)` disk reuse latency
    yellow_jpg = cv2.imencode(".jpg", np.full((240, 320, 3), (30, 220, 250), dtype=np.uint8))[1].tobytes()
    t_cache = time.perf_counter()
    cid, citem = await create_prop(name="Soda Can", description="", camera_frame_bytes=yellow_jpg)
    cache_hit_ms = round((time.perf_counter() - t_cache) * 1000.0, 2)
    (config.PROPS_DIR / f"{cid}.png").unlink(missing_ok=True)

    summary = {
        "task_id": "W1-V2-B3+W1-V3-04",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "all_under_300ms": all(x["elapsed_ms"] < 300.0 for x in items_out) and cache_hit_ms < 100.0,
        "all_passed_audit": all(x["audit"]["passed"] for x in items_out),
        "avg_tint_ms": round(sum(x["elapsed_ms"] for x in items_out) / len(items_out), 2),
        "cache_hit_ms": cache_hit_ms,
        "cache_hit_appearance_summary": citem.appearance_summary,
        "items": items_out,
    }
    out_json = config.ASSETS_DIR / "eval_v3_everyday_props.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    t0 = time.perf_counter()
    results = asyncio.run(build_all_templates(force="--force" in sys.argv))
    print(f"Built {len(results)} templates in {time.perf_counter() - t0:.2f}s:")
    for cat, info in results.items():
        a = info["audit"]
        print(f"  {cat}.png: {a['width']}x{a['height']} RGBA={a['mode']} gap={a['bottom_opaque_gap_px']}px halo={a['halo_green_pixels']} passed={a['passed']}")
    if "--kit" in sys.argv:
        kit = asyncio.run(build_everyday_prop_kit())
        print(f"Everyday Prop Kit avg={kit['avg_tint_ms']}ms cache_hit={kit['cache_hit_ms']}ms all_passed={kit['all_passed_audit']}:")
        for row in kit["items"]:
            it = row["item"]
            au = row["audit"]
            print(f"  {row['prop_id']}: {row['elapsed_ms']}ms h={it['height_m']}m app={it['appearance_summary']!r} halo={au['halo_green_pixels']} gap={au['bottom_opaque_gap_px']}px passed={au['passed']}")
