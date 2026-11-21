"""
W1-V2-03 Stage Room Reference & Isolated Prop Asset Generator (Owned by P2).

Generates:
1. `backend/assets/stages/conservatory_cam_a.png`: Empty nighttime glass conservatory room
   (wide front-center view, empty 0.75m central round wooden table, arched glass walls,
   warm wall lanterns, tiled herringbone floor, zero characters, zero vase).
2. `backend/assets/stages/conservatory_cam_b.png`: Generated conditioned on
   `conservatory_cam_a.png` from a level 45-degree front-right diagonal corner angle
   matching the exact same architecture, windows, table, and lanterns.
3. Isolated bottom-flush zero-halo RGBA props under `backend/assets/props/`:
   - `red_umbrella_closed.png`
   - `red_umbrella_open.png`
   - `wooden_chair.png`
"""

import asyncio
import base64
from pathlib import Path

from google import genai
from PIL import Image

from backend import config
from backend.media.audit_props import sanitize_prop_png
from backend.media.prop_maker import _chroma_key_green_to_rgba, _resolve_image_model, create_prop


def _generate_room_image_sync(prompt: str, ref_image: Path | None = None, out_path: Path | None = None) -> Path:
    """Generate a 16:9 empty conservatory stage background via Nano Banana 2.1."""
    client = genai.Client()
    model_id = _resolve_image_model()
    if ref_image is not None and ref_image.is_file():
        raw_bytes = ref_image.read_bytes()
        input_payload: list[dict] | str = [
            {
                "type": "image",
                "data": base64.b64encode(raw_bytes).decode("ascii"),
                "mime_type": "image/png",
            },
            {"type": "text", "text": prompt},
        ]
    else:
        input_payload = prompt

    interaction = client.interactions.create(
        model=model_id,
        input=input_payload,
        response_format={"type": "image", "aspect_ratio": "16:9"},
    )
    img_bytes = None
    out_img = getattr(interaction, "output_image", None)
    if out_img and getattr(out_img, "data", None):
        img_bytes = base64.b64decode(out_img.data)
    else:
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", None) == "model_output":
                for block in getattr(step, "content", []) or []:
                    if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                        img_bytes = base64.b64decode(block.data)
                        break
            if img_bytes:
                break
    if not img_bytes:
        raise RuntimeError(f"Failed generating stage room ref: {out_path}")

    assert out_path is not None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(__import__("io").BytesIO(img_bytes)) as im:
        im.convert("RGB").save(out_path, format="PNG")
    return out_path


async def generate_all_v2_stage_and_prop_assets() -> dict:
    """Generate fixed empty room refs + isolated umbrella/chair props."""
    stages_dir = config.ASSETS_DIR / "stages"
    stages_dir.mkdir(parents=True, exist_ok=True)

    cam_a_path = stages_dir / "conservatory_cam_a.png"
    cam_b_path = stages_dir / "conservatory_cam_b.png"

    prompt_a = (
        "Crisp 2D anime visual-novel background illustration of an empty Victorian rooftop glass conservatory at night. "
        "Wide symmetrical front-center establishing shot filling the entire 16:9 frame edge to edge (NO black borders). "
        "Dark navy-indigo rainy night sky outside tall arched dark-iron glass conservatory windows, glowing warm amber wall lanterns, "
        "herringbone wooden parquet and slate-tiled floor, potted green corner ferns, and ONE small empty circular dark-wood cafe table "
        "(0.75m waist height, 0.76m diameter) squarely in the center of the floor. "
        "COMPLETELY EMPTY ROOM: NO people, NO girl, NO cat, NO crystal vase, NO rose, NO umbrellas, NO extra chairs, NO text."
    )
    await asyncio.to_thread(_generate_room_image_sync, prompt_a, None, cam_a_path)

    prompt_b = (
        "Same exact 2D anime Victorian glass conservatory room shown in the reference image, empty of characters and tabletop items, "
        "now viewed from a camera standing at the front-right corner rotated 45 degrees horizontally around the vertical axis looking diagonally across the central round cafe table. "
        "The room architecture, dark arched glass windows, warm amber wall lanterns, floor pattern, and small round wooden cafe table MUST match the reference image identically. "
        "Horizon and floor stay completely horizontal and level, vertical pillars stay strictly vertical (ZERO roll, ZERO tilted Dutch angle, NO black borders). "
        "COMPLETELY EMPTY ROOM: NO people, NO girl, NO cat, NO crystal vase, NO umbrellas, NO text."
    )

    prop_specs = [
        (
            "red_umbrella_closed",
            "Closed Crimson Umbrella",
            "A single furled closed crimson red long-handle umbrella with curved wooden handle, tip pointing down vertically",
        ),
        (
            "red_umbrella_open",
            "Open Crimson Umbrella",
            "A single open crimson red dome umbrella canopy with wooden shaft seen cleanly from the front 3/4 view",
        ),
        (
            "wooden_chair",
            "Conservatory Wooden Chair",
            "A single classic dark walnut spindle-back Victorian wooden conservatory chair resting evenly on four legs",
        ),
    ]

    _, *prop_results = await asyncio.gather(
        asyncio.to_thread(_generate_room_image_sync, prompt_b, cam_a_path, cam_b_path),
        *(create_prop(name=n, description=d, prop_id=pid) for pid, n, d in prop_specs),
    )

    audits = []
    for pid, _ in prop_results:
        p = config.PROPS_DIR / f"{pid}.png"
        audits.append(sanitize_prop_png(p))

    return {
        "cam_a_ref": str(cam_a_path),
        "cam_b_ref": str(cam_b_path),
        "props_sanitized": audits,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(asyncio.run(generate_all_v2_stage_and_prop_assets()), ensure_ascii=False, indent=2))
