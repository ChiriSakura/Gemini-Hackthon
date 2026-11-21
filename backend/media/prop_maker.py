"""
Live Prop Generator (Owned by P2 / worker-1).

Feature & Use Cases:
- Converts either a text description or a raw webcam snapshot / `.webm` clip
  into a stage-styled 2D anime prop on a solid chroma-green background via
  `config.MODEL_IMAGE`.
- Performs OpenCV + NumPy green-screen chroma keying with edge spill suppression
  to produce a clean transparent RGBA PNG saved under `backend/assets/props/`.
- Returns `(prop_id, PropRegistryItem)` matching `shared/schema.json` and
  `backend/schemas.py`.
"""

import asyncio
import base64
import io
import re
import tempfile
from pathlib import Path
from typing import Literal, Optional

import cv2
import numpy as np
from google import genai
from google.genai import types
from PIL import Image

from backend import config
from backend.logger import log_function_call, log_genai_call
from backend.schemas import PropRegistryItem

_IMAGE_MODEL_ALIASES = {
    "gemini-3.1-flash-image-preview": "gemini-nano-banana-2.1",
}


def _resolve_image_model() -> str:
    """Resolve `config.MODEL_IMAGE`, mapping unverified placeholder aliases to live model IDs."""
    return _IMAGE_MODEL_ALIASES.get(config.MODEL_IMAGE, config.MODEL_IMAGE)


def _slugify(name: str) -> str:
    """Create a filesystem-safe prop identifier."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "custom_prop"


def _extract_image_from_bytes(raw_bytes: bytes) -> Optional[tuple[bytes, str]]:
    """
    Normalize incoming camera bytes (image or `.webm`/`.mp4` video clip) to `(jpeg_bytes, mime_type)`.

    When `/api/director/fork` forwards a 3s `.webm` clip as `camera_frame_bytes`, this
    extracts the midpoint frame via OpenCV so the image model receives a clean RGB still.
    """
    if not raw_bytes:
        return None

    # First try decoding directly as a standard image (PNG / JPEG / WebP).
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            rgb = im.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=92)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        pass

    # Fallback: treat bytes as a short video clip (e.g. webcam `.webm`) and grab midpoint frame.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        cap = cv2.VideoCapture(tmp_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            ok_enc, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if ok_enc:
                return encoded.tobytes(), "image/jpeg"
    except Exception:
        pass
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
    return None


def _chroma_key_green_to_rgba(raw_image_bytes: bytes, max_size: int = 512) -> Image.Image:
    """
    Key out chroma-green background from generated prop image bytes and return an RGBA PIL Image.

    Combines HSV green-hue detection with green-channel dominance (`G - max(R, B)`)
    plus edge alpha feathering, green-spill removal, and tight transparent cropping.
    """
    arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        with Image.open(io.BytesIO(raw_image_bytes)) as im:
            return im.convert("RGBA")

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(bgr.astype(np.int16))
    green_excess = g - np.maximum(r, b)

    # Identify chroma-green pixels (hue in [35, 95], saturated, bright enough, green-dominant).
    h_chan = hsv[:, :, 0]
    s_chan = hsv[:, :, 1]
    v_chan = hsv[:, :, 2]
    is_green = (
        (h_chan >= 34)
        & (h_chan <= 96)
        & (s_chan >= 45)
        & (v_chan >= 45)
        & (green_excess > 18)
    )

    alpha = np.where(is_green, 0.0, 255.0).astype(np.float32)

    # Smooth edges slightly to remove jagged stair-stepping.
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)

    # Soft ramp on semi-green fringe pixels based on green excess.
    fringe_mask = (green_excess > 10) & (green_excess <= 42) & (h_chan >= 30) & (h_chan <= 100)
    ramp = np.clip(1.0 - (green_excess - 10.0) / 32.0, 0.0, 1.0) * 255.0
    alpha = np.where(fringe_mask, np.minimum(alpha, ramp), alpha)

    # Despill green cast along translucent boundary pixels.
    despill_mask = (alpha > 0) & (alpha < 250) & (green_excess > 0)
    g_despilled = np.where(despill_mask, np.minimum(g, np.maximum(r, b) + 6), g)

    rgba_bgr = np.stack(
        [
            np.clip(b, 0, 255).astype(np.uint8),
            np.clip(g_despilled, 0, 255).astype(np.uint8),
            np.clip(r, 0, 255).astype(np.uint8),
            np.clip(alpha, 0, 255).astype(np.uint8),
        ],
        axis=-1,
    )
    rgba = cv2.cvtColor(rgba_bgr, cv2.COLOR_BGRA2RGBA)
    pil_img = Image.fromarray(rgba, mode="RGBA")

    # Tightly crop to non-transparent bounds: pad left/top/right slightly, flush bottom (0px)
    # so Three.js sprites with center=(0.5, 0.0) rest directly on surfaces without hovering.
    bbox = pil_img.getbbox()
    if bbox:
        pad = 10
        x0 = max(0, bbox[0] - pad)
        y0 = max(0, bbox[1] - pad)
        x1 = min(pil_img.width, bbox[2] + pad)
        y1 = bbox[3]
        pil_img = pil_img.crop((x0, y0, x1, y1))

    if max(pil_img.width, pil_img.height) > max_size:
        pil_img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return pil_img


def _generate_prop_sync(
    name: str,
    description: str,
    camera_still: Optional[tuple[bytes, str]],
) -> tuple[bytes, dict]:
    """Synchronous Gemini Interactions API call to generate a chroma-green prop image."""
    model_id = _resolve_image_model()
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000))

    desc_text = description.strip() or name
    prompt_text = (
        f"Create a single centered 2D anime visual-novel stage prop illustration of '{name}' "
        f"({desc_text}). Style: crisp hand-painted anime cel-shaded game prop, clean silhouette, "
        "front-facing 3/4 view, NO human hands, NO characters, NO text, NO watermark. "
        "Place the object completely isolated on a flat, uniform, solid bright chroma-key green "
        "background (#00FF00) with zero floor shadows and zero green spill on the prop."
    )

    if camera_still:
        still_bytes, still_mime = camera_still
        input_payload: list[dict] | str = [
            {
                "type": "image",
                "data": base64.b64encode(still_bytes).decode("ascii"),
                "mime_type": still_mime,
            },
            {
                "type": "text",
                "text": (
                    "Convert the physical object shown in this photo into "
                    + prompt_text
                ),
            },
        ]
    else:
        input_payload = prompt_text

    interaction = client.interactions.create(
        model=model_id,
        input=input_payload,
        response_format={"type": "image", "aspect_ratio": "1:1"},
    )

    output_image = getattr(interaction, "output_image", None)
    if output_image and getattr(output_image, "data", None):
        raw_bytes = base64.b64decode(output_image.data)
        return raw_bytes, {"model": model_id, "interaction_id": getattr(interaction, "id", None)}

    for step in getattr(interaction, "steps", []) or []:
        if getattr(step, "type", None) == "model_output":
            for block in getattr(step, "content", []) or []:
                if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                    raw_bytes = base64.b64decode(block.data)
                    return raw_bytes, {"model": model_id, "interaction_id": getattr(interaction, "id", None)}

    raise RuntimeError(f"Image model '{model_id}' returned no image content.")


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
    resolved_model = _resolve_image_model()

    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        if not out_path.exists():
            img = Image.new("RGBA", (256, 256), (255, 215, 64, 230))
            img.save(out_path, format="PNG")
        log_genai_call(
            resolved_model,
            {"name": name, "description": description, "has_camera_frame": bool(camera_frame_bytes)},
            {"placeholder": True},
            {"saved_path": str(out_path)},
        )
    else:
        camera_still = _extract_image_from_bytes(camera_frame_bytes) if camera_frame_bytes else None
        try:
            raw_img_bytes, meta = await asyncio.to_thread(
                _generate_prop_sync,
                name,
                description,
                camera_still,
            )
            rgba_img = await asyncio.to_thread(_chroma_key_green_to_rgba, raw_img_bytes)
            rgba_img.save(out_path, format="PNG")
            log_genai_call(
                resolved_model,
                {
                    "name": name,
                    "description": description,
                    "has_camera_still": bool(camera_still),
                },
                {"aspect_ratio": "1:1", "chroma_key": "opencv_hsv_despill"},
                {"saved_path": str(out_path), "size": [rgba_img.width, rgba_img.height], **meta},
            )
        except Exception as exc:
            # Never crash live demo if image generation fails: fall back to a visible token sprite.
            if not out_path.exists():
                img = Image.new("RGBA", (256, 256), (255, 215, 64, 230))
                img.save(out_path, format="PNG")
            log_genai_call(
                resolved_model,
                {"name": name, "description": description, "has_camera_still": bool(camera_still)},
                {"fallback": True},
                {"error": f"{type(exc).__name__}: {exc}", "saved_path": str(out_path)},
            )

    item = PropRegistryItem(
        name=name,
        image_url=f"/assets/props/{resolved_id}.png",
        state="intact",
        origin=origin,
    )
    return resolved_id, item
