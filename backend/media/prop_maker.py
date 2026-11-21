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
import hashlib
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
    "gemini-3.1-flash-image": "gemini-nano-banana-2.1",
}


def _resolve_image_model() -> str:
    """Resolve `config.MODEL_IMAGE`, mapping unverified placeholder aliases to live model IDs."""
    return _IMAGE_MODEL_ALIASES.get(config.MODEL_IMAGE, config.MODEL_IMAGE)


def _slugify(name: str) -> str:
    """Create a collision-safe, filesystem-safe prop identifier with deterministic hash suffix."""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    if not slug:
        return f"prop_{digest[:6]}"
    return f"{slug[:40]}_{digest[:4]}"


_PROP_HEIGHT_KEYWORDS: list[tuple[tuple[str, ...], float]] = [
    (("key", "ring", "coin", "brooch", "badge", "钥匙", "戒指", "硬币", "胸牌"), 0.08),
    (("heart", "origami", "crane", "watch", "pocket_watch", "pocket watch", "心", "千纸鹤", "怀表"), 0.09),
    (("star", "token", "charm", "星", "信物"), 0.10),
    (("cup", "mug", "can", "cola", "coffee", "apple", "teacup", "杯", "罐", "可乐", "咖啡"), 0.13),
    (("quill", "pen", "feather", "wand", "book", "diary", "letter", "phone", "bottle", "jar", "羽毛笔", "书", "信", "瓶"), 0.22),
    (("lantern", "lamp", "candle", "bouquet", "flower", "rose", "灯", "花束"), 0.28),
    (("vase", "crystal_vase", "花瓶"), 0.32),
    (("plush", "bear", "doll", "box", "gift", "bag", "礼盒", "玩偶"), 0.36),
    (("shield", "guitar", "violin", "cello", "盾", "琴"), 0.65),
    (("umbrella", "parasol", "伞"), 0.86),
    (("sword", "staff", "cane", "剑", "法杖"), 0.95),
]


def estimate_prop_height_m(name: str, description: str = "") -> float:
    """Estimate realistic SI metric height in meters (clamped to 0.08m..1.20m) for a prop."""
    blob = f"{name} {description}".lower()
    for keywords, height_m in _PROP_HEIGHT_KEYWORDS:
        if any(k in blob for k in keywords):
            return height_m
    return 0.22


def _create_ascii_temp_file(suffix: str) -> Path:
    """Create a temporary file at an ASCII-safe path so Windows OpenCV never fails on Unicode paths."""
    tmp_dir = Path(tempfile.gettempdir())
    if not str(tmp_dir).isascii():
        fallback_dir = Path("C:/Windows/Temp") if Path("C:/Windows/Temp").exists() else Path("/tmp")
        if fallback_dir.exists():
            tmp_dir = fallback_dir
    with tempfile.NamedTemporaryFile(suffix=suffix, dir=str(tmp_dir), delete=False) as tmp:
        return Path(tmp.name)


def _frame_sharpness_score(frame: np.ndarray) -> float:
    """Compute Laplacian variance sharpness metric (higher = crisper, less motion blur)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _extract_image_from_bytes(
    raw_bytes: bytes,
    timestamp_sec: Optional[float] = None,
) -> Optional[tuple[bytes, str]]:
    """
    Normalize incoming camera bytes (image or `.webm`/`.mp4` video clip) to `(jpeg_bytes, mime_type)`.

    When `/api/director/fork` forwards a 3s `.webm` clip as `camera_frame_bytes`, seeks to
    `timestamp_sec` (when provided by Hydrogen `prop_frame_timestamp_sec`) or the midpoint
    frame via OpenCV, picking the sharpest frame within a small `±0.20s` window to reject
    handheld motion blur and dark fade-in frames.
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

    # Fallback: treat bytes as a short video clip (e.g. webcam `.webm`) and grab target/midpoint frame.
    tmp_path: Optional[Path] = None
    try:
        tmp_path = _create_ascii_temp_file(".webm")
        tmp_path.write_bytes(raw_bytes)
        cap = cv2.VideoCapture(str(tmp_path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        est_fps = fps if 1.0 <= fps <= 120.0 else 30.0
        frame: Optional[np.ndarray] = None

        # Decode up to 150 frames sequentially when needed (Chrome MediaRecorder .webm often has total <= 0)
        # OR sample a local sharpness window around `timestamp_sec` when random seek works.
        if timestamp_sec is not None and timestamp_sec >= 0.0 and total > 1:
            candidates: list[np.ndarray] = []
            for delta in (0.0, -0.10, 0.10, -0.20, 0.20):
                t_ms = max(0.0, (timestamp_sec + delta) * 1000.0)
                cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
                ok, cand = cap.read()
                if ok and cand is not None and float(np.mean(cand)) > 4.0:
                    candidates.append(cand)
            if not candidates and fps > 1.0:
                target_idx = max(0, min(total - 1, int(round(timestamp_sec * fps))))
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
                ok, cand = cap.read()
                if ok and cand is not None:
                    candidates.append(cand)
            if candidates:
                frame = max(candidates, key=_frame_sharpness_score)
        elif total > 1:
            mid = total // 2
            candidates = []
            for offset in (0, -2, 2, -4, 4):
                idx = max(0, min(total - 1, mid + offset))
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, cand = cap.read()
                if ok and cand is not None and float(np.mean(cand)) > 4.0:
                    candidates.append(cand)
            if candidates:
                frame = max(candidates, key=_frame_sharpness_score)

        # Handle Chrome MediaRecorder .webm clips where CAP_PROP_FRAME_COUNT <= 0 or seek failed.
        if frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            decoded_frames: list[np.ndarray] = []
            while len(decoded_frames) < 150:
                ok, cand = cap.read()
                if not ok or cand is None:
                    break
                decoded_frames.append(cand)
            if decoded_frames:
                if timestamp_sec is not None and timestamp_sec >= 0.0:
                    center_idx = max(0, min(len(decoded_frames) - 1, int(round(timestamp_sec * est_fps))))
                else:
                    center_idx = len(decoded_frames) // 2
                radius = max(1, int(round(0.15 * est_fps)))
                lo = max(0, center_idx - radius)
                hi = min(len(decoded_frames), center_idx + radius + 1)
                window = [f for f in decoded_frames[lo:hi] if float(np.mean(f)) > 4.0] or [decoded_frames[center_idx]]
                frame = max(window, key=_frame_sharpness_score)

        cap.release()
        if frame is not None:
            ok_enc, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if ok_enc:
                return encoded.tobytes(), "image/jpeg"
    except Exception:
        pass
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return None


def _chroma_key_green_to_rgba(raw_image_bytes: bytes, max_size: int = 512) -> Image.Image:
    """
    Key out chroma-green background using border-connected floodfill + spill suppression.

    Unlike global HSV thresholding (which punches holes through green objects like leaves
    or green bottles), border-connected floodfill only removes chroma-green pixels connected
    to the image edges (plus uniform pure-chroma interior loops like handle cutouts).
    """
    arr = np.frombuffer(raw_image_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        with Image.open(io.BytesIO(raw_image_bytes)) as im:
            return im.convert("RGBA")

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(bgr.astype(np.int16))
    green_excess = g - np.maximum(r, b)

    # Identify candidate chroma-green pixels.
    h_chan = hsv[:, :, 0]
    s_chan = hsv[:, :, 1]
    v_chan = hsv[:, :, 2]
    is_green = (
        (h_chan >= 34)
        & (h_chan <= 96)
        & (s_chan >= 45)
        & (v_chan >= 45)
        & (green_excess > 18)
    ).astype(np.uint8)

    # Keep only green components touching the outer border so solid green props stay intact.
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(is_green, connectivity=4)
    border_labels = (
        set(np.unique(labels[0, :]))
        | set(np.unique(labels[-1, :]))
        | set(np.unique(labels[:, 0]))
        | set(np.unique(labels[:, -1]))
    )
    border_labels.discard(0)
    bg_mask = np.isin(labels, list(border_labels))

    # Also clear chroma-green interior cutouts (tiny gaps inside chain links, key bows,
    # filigree, and larger pure-chroma handle loops) while keeping real green props intact.
    for lbl in range(1, num_labels):
        if lbl in border_labels:
            continue
        area = int(stats[lbl, cv2.CC_STAT_AREA])
        region = labels == lbl
        med_excess = float(np.median(green_excess[region]))
        med_sat = float(np.median(s_chan[region]))
        med_val = float(np.median(v_chan[region]))
        med_hue = float(np.median(h_chan[region]))
        is_tiny_filigree_hole = (
            area <= 250
            and med_excess >= 42.0
            and med_sat >= 100.0
            and med_val >= 80.0
            and 42.0 <= med_hue <= 82.0
        )
        is_large_neon_loop = (
            area > 250
            and med_excess > 140.0
            and med_sat > 210.0
            and med_val > 205.0
            and 50.0 <= med_hue <= 70.0
        )
        if is_tiny_filigree_hole or is_large_neon_loop:
            bg_mask |= region

    alpha = np.where(bg_mask, 0.0, 255.0).astype(np.float32)

    # Smooth only along the border-adjacent boundary band to avoid eating interior green objects.
    bg_u8 = bg_mask.astype(np.uint8)
    dilated_bg = cv2.dilate(bg_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
    boundary_band = (dilated_bg > 0) & (~bg_mask)

    alpha_blur = cv2.GaussianBlur(alpha, (3, 3), 0)
    alpha = np.where(boundary_band | bg_mask, alpha_blur, 255.0)

    # Soft ramp on semi-green fringe pixels strictly along the outer boundary band.
    fringe_mask = boundary_band & (green_excess > 10) & (green_excess <= 42) & (h_chan >= 30) & (h_chan <= 100)
    ramp = np.clip(1.0 - (green_excess - 10.0) / 32.0, 0.0, 1.0) * 255.0
    alpha = np.where(fringe_mask, np.minimum(alpha, ramp), alpha)

    # Despill green cast strictly along the outer boundary band (including opaque 2px rim pixels)
    # so zero green halo survives while interior green props remain untouched.
    despill_mask = boundary_band & (alpha > 0) & (green_excess > 8)
    g_despilled = np.where(despill_mask, np.minimum(g, np.maximum(r, b)), g)

    # Clear faint translucent green fringe (< 180 alpha) completely and suppress neon rim pixels.
    clear_fringe = boundary_band & (alpha > 0) & (alpha < 180) & (h_chan >= 34) & (h_chan <= 96) & (green_excess > 14)
    alpha = np.where(clear_fringe, 0.0, alpha)

    dilated_outer = cv2.dilate(bg_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)
    tight_rim = (dilated_outer > 0) & (~bg_mask) & (alpha > 0) & (h_chan >= 34) & (h_chan <= 96) & (s_chan >= 45) & (green_excess > 12)
    g_despilled = np.where(tight_rim, np.minimum(g_despilled, np.maximum(r, b)), g_despilled)

    # Strip invisible alpha dust (< 16) so bounding box & Three.js sprite base are razor-crisp.
    alpha = np.where(alpha < 16.0, 0.0, alpha)

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
        pad = 6
        x0 = max(0, bbox[0] - pad)
        y0 = max(0, bbox[1] - pad)
        x1 = min(pil_img.width, bbox[2] + pad)
        y1 = bbox[3]
        pil_img = pil_img.crop((x0, y0, x1, y1))

    if max(pil_img.width, pil_img.height) > max_size:
        pil_img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

    # Post-thumbnail sanitization: LANCZOS downscaling can introduce 1px translucent bottom rows
    # or faint green anti-aliased edge pixels; enforce 0px bottom gap & zero translucent green spill.
    final_arr = np.array(pil_img, dtype=np.uint8)
    fa = final_arr[:, :, 3]
    fa = np.where(fa < 16, 0, fa).astype(np.uint8)
    fr = final_arr[:, :, 0].astype(np.int16)
    fg = final_arr[:, :, 1].astype(np.int16)
    fb = final_arr[:, :, 2].astype(np.int16)
    translucent_green = (fa > 0) & (fa < 252) & (fg > np.maximum(fr, fb))
    fg = np.where(translucent_green, np.maximum(fr, fb), fg)
    final_arr[:, :, 1] = np.clip(fg, 0, 255).astype(np.uint8)
    final_arr[:, :, 3] = fa
    ys_final = np.where(fa >= 16)[0]
    if len(ys_final) > 0:
        final_arr = final_arr[: int(ys_final.max()) + 1, :, :]
    return Image.fromarray(final_arr, mode="RGBA")


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
        "Use warm rich ivory, gold, brass, crimson, amber, ceramic white, silver, or sapphire tones on the prop "
        "(avoid neon green or emerald reflections on the prop itself unless explicitly requested). "
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
    frame_timestamp_sec: Optional[float] = None,
    timestamp_sec: Optional[float] = None,
) -> tuple[str, PropRegistryItem]:
    """
    Generate a transparent PNG prop from text or webcam frame and save under `backend/assets/props/`.

    Returns `(prop_id, PropRegistryItem)` ready for `props_registry`.
    """
    resolved_ts = frame_timestamp_sec if frame_timestamp_sec is not None else timestamp_sec
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
        camera_still = (
            _extract_image_from_bytes(camera_frame_bytes, timestamp_sec=resolved_ts)
            if camera_frame_bytes
            else None
        )
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
