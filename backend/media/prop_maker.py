"""
Live Prop & Actor Portrait Generator (Owned by P2).

Feature & Use Cases:
- Converts either a text prompt ("give her a red umbrella") or a live webcam
  frame (e.g., judge holding a soda can or sketch) into a stage-consistent 2D
  prop image on chroma green background, strips the green screen to transparent
  PNG, and saves it to `backend/assets/props/`.
"""

import re
from typing import Optional

from PIL import Image

from backend import config
from backend.logger import log_function_call, log_genai_call
from backend.schemas import PropAsset


def _slugify(name: str) -> str:
    """Create a filesystem-safe prop identifier."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "custom_prop"


@log_function_call
async def create_prop(
    name_or_prompt: str,
    camera_frame_bytes: Optional[bytes] = None,
    style_hint: str = "anime 2.5D stage prop",
) -> PropAsset:
    """
    Generate a transparent PNG prop from text or a raw webcam image.

    Saves the output under `backend/assets/props/<prop_id>.png` and returns
    a `PropAsset` ready to be added to `props_registry`.
    """
    prop_id = _slugify(name_or_prompt)
    out_path = config.PROPS_DIR / f"{prop_id}.png"
    source_type = "camera" if camera_frame_bytes else "text"

    if config.DEMO_MODE or not config.GEMINI_API_KEY or not out_path.exists():
        # Placeholder transparent colored badge so 3D stage never breaks during T0/T1
        img = Image.new("RGBA", (256, 256), (233, 30, 99, 220))
        img.save(out_path, format="PNG")
        log_genai_call(
            config.MODEL_IMAGE,
            {"prompt": name_or_prompt, "style": style_hint, "has_camera_frame": bool(camera_frame_bytes)},
            {"placeholder": True},
            {"saved_path": str(out_path)},
        )

    # TODO (P2 - T1): Call config.MODEL_IMAGE with chroma-green prompt (+ camera_frame_bytes if present),
    # then key out green background into RGBA PNG and save to `out_path`.
    return PropAsset(
        prop_id=prop_id,
        name=name_or_prompt,
        image_url=f"/assets/props/{prop_id}.png",
        state="intact",
        source_type=source_type,
    )
