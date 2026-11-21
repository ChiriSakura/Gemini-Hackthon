"""
Live Prop Generator (Owned by P2 / worker-1).

Feature & Use Cases:
- Converts either a text description or a raw webcam snapshot into a stage-styled
  2D prop on solid chroma-green background via `config.MODEL_IMAGE`, removes the
  green screen into a transparent RGBA PNG, and returns a `PropRegistryItem`.
"""

import re
from typing import Literal, Optional

from PIL import Image

from backend import config
from backend.logger import log_function_call, log_genai_call
from backend.schemas import PropRegistryItem


def _slugify(name: str) -> str:
    """Create a filesystem-safe prop identifier."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "custom_prop"


@log_function_call
async def create_prop(
    name: str,
    description: str = "",
    camera_frame_bytes: Optional[bytes] = None,
    prop_id: Optional[str] = None,
) -> tuple[str, PropRegistryItem]:
    """
    Generate a transparent PNG prop from text or webcam frame and save under `backend/assets/props/`.

    Returns `(prop_id, PropRegistryItem)` ready for `props_registry`.
    """
    resolved_id = prop_id or _slugify(name)
    out_path = config.PROPS_DIR / f"{resolved_id}.png"
    origin: Literal["preset", "text_generated", "camera_physical"] = (
        "camera_physical" if camera_frame_bytes else "text_generated"
    )

    if config.DEMO_MODE or not config.GEMINI_API_KEY or not out_path.exists():
        img = Image.new("RGBA", (256, 256), (255, 215, 64, 230))
        img.save(out_path, format="PNG")
        log_genai_call(
            config.MODEL_IMAGE,
            {"name": name, "description": description, "has_camera_frame": bool(camera_frame_bytes)},
            {"placeholder": True},
            {"saved_path": str(out_path)},
        )

    # TODO (P2 - T1): Call config.MODEL_IMAGE with chroma-green prompt (+ camera_frame_bytes),
    # then key out green background into transparent RGBA PNG at `out_path`.
    item = PropRegistryItem(
        name=name,
        image_url=f"/assets/props/{resolved_id}.png",
        state="intact",
        origin=origin,
    )
    return resolved_id, item
