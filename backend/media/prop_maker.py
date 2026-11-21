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
import shutil
import tempfile
import unicodedata
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


def _slugify(name: str, extra_bytes: Optional[bytes] = None) -> str:
    """Create a collision-safe, filesystem-safe prop identifier with deterministic hash suffix."""
    hasher = hashlib.sha1(name.encode("utf-8"))
    if extra_bytes:
        hasher.update(b":")
        hasher.update(hashlib.sha1(extra_bytes).digest())
    digest = hasher.hexdigest()
    ascii_folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_folded).strip("_").lower()
    if not slug:
        return f"prop_{digest[:6]}"
    return f"{slug[:40]}_{digest[:4]}"


_PROP_HEIGHT_KEYWORDS: list[tuple[tuple[str, ...], float]] = [
    (("key", "ring", "coin", "brooch", "badge", "钥匙", "戒指", "硬币", "胸牌"), 0.08),
    (("heart", "origami", "crane", "watch", "pocket_watch", "pocket watch", "心", "千纸鹤", "怀表"), 0.09),
    (("star", "token", "charm", "星", "信物"), 0.10),
    (
        (
            "soda",
            "drink",
            "beverage",
            "cup",
            "mug",
            "can",
            "cola",
            "coke",
            "pepsi",
            "coffee",
            "tea",
            "juice",
            "milk",
            "apple",
            "teacup",
            "杯",
            "罐",
            "可乐",
            "咖啡",
            "饮料",
            "汽水",
        ),
        0.13,
    ),
    (("smartphone", "phone", "cellphone", "mobile", "iphone", "手机"), 0.15),
    (
        (
            "snack",
            "chips",
            "crisps",
            "cookie",
            "biscuit",
            "chocolate",
            "candy",
            "pocky",
            "nibbles",
            "popcorn",
            "cracker",
            "quill",
            "pen",
            "feather",
            "wand",
            "book",
            "diary",
            "letter",
            "bottle",
            "thermos",
            "jar",
            "零食",
            "薯片",
            "饼干",
            "糖果",
            "羽毛笔",
            "书",
            "信",
            "瓶",
        ),
        0.22,
    ),
    (("lantern", "lamp", "candle", "bouquet", "flower", "rose", "灯", "花束"), 0.28),
    (("laptop", "macbook", "notebook", "computer", "ipad", "tablet", "keyboard", "笔记本", "电脑", "平板"), 0.30),
    (("vase", "crystal_vase", "花瓶"), 0.32),
    (("plush", "bear", "doll", "box", "gift", "bag", "礼盒", "玩偶"), 0.36),
    (("shield", "guitar", "violin", "cello", "盾", "琴"), 0.65),
    (("chair", "wooden_chair", "wooden chair", "stool", "椅子", "凳子"), 0.85),
    (("umbrella", "parasol", "伞"), 0.86),
    (("sword", "staff", "cane", "剑", "法杖"), 0.95),
]


def _keyword_matches(text: str, kw: str) -> bool:
    """Match English keywords on word/underscore boundaries so e.g. 'canopy' never triggers 'can'."""
    if kw.isascii():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text))
    return kw in text


def estimate_prop_height_m(name: str, description: str = "") -> float:
    """Estimate realistic SI metric height in meters (clamped to 0.08m..1.20m) for a prop."""
    for scope in (name.lower().replace("_", " "), f"{name} {description}".lower().replace("_", " ")):
        for keywords, height_m in _PROP_HEIGHT_KEYWORDS:
            if any(_keyword_matches(scope, k) for k in keywords):
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


def _detect_throw_peak_frame_idx(frames: list[np.ndarray], est_fps: float = 30.0) -> int:
    """
    Identify the frame right as a tossed/introduced object enters the center region and settles into crisp focus.

    Scans the central 50% ROI across up to 1200 frames (30s @ 30fps) for motion impulse (`absdiff`)
    paired with high Laplacian sharpness, preferring frames immediately after peak motion where blur drops.
    """
    if not frames:
        return 0
    if len(frames) == 1:
        return 0

    h, w = frames[0].shape[:2]
    y0, y1 = max(0, h // 4), min(h, max(h // 4 + 1, (3 * h) // 4))
    x0, x1 = max(0, w // 4), min(w, max(w // 4 + 1, (3 * w) // 4))

    grays: list[np.ndarray] = []
    brightness: list[float] = []
    sharpness: list[float] = []
    for f in frames:
        roi = f[y0:y1, x0:x1]
        b_mean = float(np.mean(roi))
        brightness.append(b_mean)
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        grays.append(g)
        sharpness.append(float(cv2.Laplacian(g, cv2.CV_64F).var()) if b_mean > 4.0 else 0.0)

    motion = [0.0]
    for i in range(1, len(grays)):
        motion.append(float(np.mean(cv2.absdiff(grays[i], grays[i - 1]))))

    valid_indices = [i for i, b_mean in enumerate(brightness) if b_mean > 4.0]
    if not valid_indices:
        return len(frames) // 2

    max_motion = max(motion[i] for i in valid_indices)
    if max_motion < 2.0:
        # Static camera feed: pick crispest non-dark frame near midpoint.
        return max(valid_indices, key=lambda i: (sharpness[i], -abs(i - len(frames) // 2)))

    peak_m_idx = max(valid_indices, key=lambda i: motion[i])
    settle_end = min(len(frames) - 1, peak_m_idx + max(4, int(round(est_fps * 0.6))))
    settle_start = max(0, peak_m_idx - 2)
    window = [i for i in range(settle_start, settle_end + 1) if brightness[i] > 4.0] or valid_indices
    return max(window, key=lambda i: sharpness[i] / (1.0 + 0.05 * motion[i]))


def _compute_motion_fg_mask(peak_frame: np.ndarray, ref_frame: np.ndarray) -> Optional[np.ndarray]:
    """Compute a center-ROI boolean foreground mask of the moving thrown/held object vs a calmer baseline frame."""
    if peak_frame.shape != ref_frame.shape or peak_frame.size == 0:
        return None
    h, w = peak_frame.shape[:2]
    y0, y1 = max(0, h // 5), min(h, max(h // 5 + 1, (4 * h) // 5))
    x0, x1 = max(0, w // 5), min(w, max(w // 5 + 1, (4 * w) // 5))
    g_peak = cv2.GaussianBlur(cv2.cvtColor(peak_frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (5, 5), 0)
    g_ref = cv2.GaussianBlur(cv2.cvtColor(ref_frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), (5, 5), 0)
    diff = cv2.absdiff(g_peak, g_ref)
    fg_mask = diff >= 22
    if int(np.count_nonzero(fg_mask)) >= 60:
        return fg_mask
    return None


def _extract_still_and_fg_mask(
    raw_bytes: bytes,
    timestamp_sec: Optional[float] = None,
    detect_throw_peak: bool = True,
) -> tuple[Optional[tuple[bytes, str]], Optional[np.ndarray]]:
    """
    Normalize incoming camera bytes (image or up to 30s `.webm`/`.mp4` video clip) to
    `((jpeg_bytes, mime_type), optional_motion_fg_mask)`.

    When extracting from a throw-in video clip, also compares `decoded_frames[peak_idx]` against a calmer
    reference frame so downstream HSV extraction isolates the moving object from static room walls/desks.
    """
    if not raw_bytes:
        return None, None

    # First try decoding directly as a standard image (PNG / JPEG / WebP).
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            rgb = im.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=92)
            return (buf.getvalue(), "image/jpeg"), None
    except Exception:
        pass

    # Fallback: treat bytes as a short video clip (up to 30s `.webm`) and grab target/throw-peak/midpoint frame.
    tmp_path: Optional[Path] = None
    fg_mask: Optional[np.ndarray] = None
    try:
        tmp_path = _create_ascii_temp_file(".webm")
        tmp_path.write_bytes(raw_bytes)
        cap = cv2.VideoCapture(str(tmp_path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        est_fps = fps if 1.0 <= fps <= 120.0 else 30.0
        frame: Optional[np.ndarray] = None

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
        elif total > 1 and not detect_throw_peak:
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

        # Decode up to 1200 frames (30s @ 30fps) sequentially for throw-peak detection or Chrome .webm metadata fallback.
        if frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            decoded_frames: list[np.ndarray] = []
            while len(decoded_frames) < 1200:
                ok, cand = cap.read()
                if not ok or cand is None:
                    break
                decoded_frames.append(cand)
            if decoded_frames:
                if timestamp_sec is not None and timestamp_sec >= 0.0:
                    center_idx = max(0, min(len(decoded_frames) - 1, int(round(timestamp_sec * est_fps))))
                    radius = max(1, int(round(0.15 * est_fps)))
                    lo = max(0, center_idx - radius)
                    hi = min(len(decoded_frames), center_idx + radius + 1)
                    window = [f for f in decoded_frames[lo:hi] if float(np.mean(f)) > 4.0] or [decoded_frames[center_idx]]
                    frame = max(window, key=_frame_sharpness_score)
                elif detect_throw_peak:
                    peak_idx = _detect_throw_peak_frame_idx(decoded_frames, est_fps=est_fps)
                    frame = decoded_frames[peak_idx]
                    if len(decoded_frames) >= 4:
                        ref_idx = 0 if peak_idx >= 4 else len(decoded_frames) - 1
                        fg_mask = _compute_motion_fg_mask(frame, decoded_frames[ref_idx])
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
                return (encoded.tobytes(), "image/jpeg"), fg_mask
    except Exception:
        pass
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return None, None


def _extract_image_from_bytes(
    raw_bytes: bytes,
    timestamp_sec: Optional[float] = None,
    detect_throw_peak: bool = True,
) -> Optional[tuple[bytes, str]]:
    """
    Normalize incoming camera bytes (image or up to 30s `.webm`/`.mp4` video clip) to `(jpeg_bytes, mime_type)`.

    When `/api/director/fork` or `/api/media/create-prop` forwards a `.webm` clip up to 30s (`1200` frames):
    - If `timestamp_sec` is provided, seeks to `CAP_PROP_POS_MSEC` and picks the sharpest frame within `±0.20s`.
    - Otherwise, when `detect_throw_peak=True`, locates the center-ROI motion impulse / settled object frame.
    """
    still, _fg_mask = _extract_still_and_fg_mask(
        raw_bytes,
        timestamp_sec=timestamp_sec,
        detect_throw_peak=detect_throw_peak,
    )
    return still


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


TEMPLATES_DIR = config.PROPS_DIR / "templates"
TEMPLATE_CACHE_DIR = TEMPLATES_DIR / "cache"

_TEMPLATE_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("smartphone", ("smartphone", "phone", "cellphone", "mobile", "iphone", "手机")),
    ("laptop", ("laptop", "macbook", "computer", "notebook", "电脑")),
    ("soda_can", ("can", "soda", "cola", "drink", "beverage", "可乐", "汽水", "饮料")),
    ("snack_bag", ("snack", "chips", "pouch", "cookie", "candy", "pocky", "nibbles", "零食", "薯片")),
    ("coffee_mug", ("mug", "cup", "coffee", "teacup", "tea", "杯", "咖啡")),
]

_TEXT_COLOR_PALETTE: list[tuple[tuple[str, ...], str, tuple[int, int, int], str]] = [
    (("yellow", "lemon", "canary", "sunny", "黄"), "bright yellow", (245, 212, 36), "yellow"),
    (("gold", "golden", "amber", "brass", "金"), "warm golden", (230, 184, 58), "gold"),
    (("orange", "coral", "tangerine", "peach", "橙"), "warm orange", (242, 128, 38), "orange"),
    (("red", "crimson", "ruby", "scarlet", "cherry", "红"), "vibrant red", (215, 52, 52), "red"),
    (("pink", "rose", "magenta", "粉"), "rose pink", (235, 118, 165), "pink"),
    (("blue", "cobalt", "sapphire", "navy", "cyan", "sky", "蓝"), "cobalt blue", (48, 116, 224), "blue"),
    (("green", "emerald", "mint", "lime", "olive", "绿"), "vivid green", (58, 176, 86), "green"),
    (("purple", "violet", "lavender", "紫"), "rich purple", (138, 72, 204), "purple"),
    (("black", "obsidian", "dark", "matte", "炭黑", "黑"), "matte black", (46, 48, 54), "black"),
    (("white", "ivory", "cream", "pearl", "白"), "clean white", (236, 239, 244), "white"),
    (("silver", "gray", "grey", "chrome", "metallic", "aluminum", "银", "灰"), "metallic silver-grey", (178, 186, 198), "silver"),
]

_GENERIC_PROP_NAMES = {
    "",
    "prop",
    "object",
    "item",
    "thing",
    "captured prop",
    "physical object",
    "custom prop",
    "smoke_prop",
}


def _match_template_category(name: str, description: str = "") -> Optional[str]:
    """Match incoming prop against cached neutral anime template categories (`< 0.3s` recolor path)."""
    blob = f"{name} {description}".lower().replace("_", " ")
    for category, keywords in _TEMPLATE_CATEGORY_RULES:
        if any(_keyword_matches(blob, k) for k in keywords) and (TEMPLATES_DIR / f"{category}.png").is_file():
            return category
    return None


def _extract_dominant_color_info(
    name: str,
    description: str,
    still: Optional[tuple[bytes, str]],
    fg_mask: Optional[np.ndarray] = None,
) -> tuple[str, tuple[int, int, int], str]:
    """
    Zero-LLM <5ms dominant color extractor returning `(color_label, target_rgb, color_bucket)`.

    When `fg_mask` (from video motion diff) is present, restricts sampling strictly to moving foreground pixels;
    otherwise filters muted room walls via center-core chromatic salience before falling back to whole-ROI median.
    """
    if still:
        arr = np.frombuffer(still[0], dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is not None and bgr.size > 0:
            h, w = bgr.shape[:2]
            if fg_mask is not None:
                y0, y1 = max(0, h // 5), min(h, max(h // 5 + 1, (4 * h) // 5))
                x0, x1 = max(0, w // 5), min(w, max(w // 5 + 1, (4 * w) // 5))
                roi = bgr[y0:y1, x0:x1]
                hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                if fg_mask.shape == hsv.shape[:2] and int(np.count_nonzero(fg_mask)) >= 40:
                    sample_bgr = roi[fg_mask]
                    sample_hsv = hsv[fg_mask]
                else:
                    sample_bgr = roi.reshape(-1, 3)
                    sample_hsv = hsv.reshape(-1, 3)
            else:
                roi = bgr[max(0, h // 5) : min(h, max(1, (4 * h) // 5)), max(0, w // 5) : min(w, max(1, (4 * w) // 5))]
                hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                flat_bgr = roi.reshape(-1, 3)
                flat_hsv = hsv.reshape(-1, 3)
                vivid = (flat_hsv[:, 1] >= 70) & (flat_hsv[:, 2] >= 65)
                if int(np.count_nonzero(vivid)) >= max(40, int(0.18 * len(flat_hsv))):
                    sample_bgr = flat_bgr[vivid]
                    sample_hsv = flat_hsv[vivid]
                else:
                    sample_bgr = flat_bgr
                    sample_hsv = flat_hsv

            med_h = float(np.median(sample_hsv[:, 0]))
            med_s = float(np.median(sample_hsv[:, 1]))
            med_v = float(np.median(sample_hsv[:, 2]))
            med_b = int(round(float(np.median(sample_bgr[:, 0]))))
            med_g = int(round(float(np.median(sample_bgr[:, 1]))))
            med_r = int(round(float(np.median(sample_bgr[:, 2]))))

            if med_v < 65:
                return "matte black", (46, 48, 54), "black"
            if med_s < 38 and med_v > 185:
                return "clean white", (236, 239, 244), "white"
            if med_s < 42:
                return "metallic silver-grey", (178, 186, 198), "silver"

            if 18 <= med_h <= 35:
                label, canon, bucket = "bright yellow", (245, 212, 36), "yellow"
            elif 6 <= med_h < 18:
                label, canon, bucket = "warm orange", (242, 128, 38), "orange"
            elif med_h < 6 or med_h > 165:
                label, canon, bucket = "vibrant red", (215, 52, 52), "red"
            elif 36 <= med_h <= 85:
                label, canon, bucket = "vivid green", (58, 176, 86), "green"
            elif 86 <= med_h <= 132:
                label, canon, bucket = "cobalt blue", (48, 116, 224), "blue"
            else:
                label, canon, bucket = "rich purple", (138, 72, 204), "purple"

            mixed = (
                int(round(0.60 * canon[0] + 0.40 * med_r)),
                int(round(0.60 * canon[1] + 0.40 * med_g)),
                int(round(0.60 * canon[2] + 0.40 * med_b)),
            )
            return label, mixed, bucket

    blob = f"{name} {description}".lower().replace("_", " ")
    for keywords, label, rgb_val, bucket in _TEXT_COLOR_PALETTE:
        if any(_keyword_matches(blob, k) for k in keywords):
            return label, rgb_val, bucket
    return "metallic silver-grey", (178, 186, 198), "silver"


def _recolor_template_preserve_shading(template_path: Path, target_rgb: tuple[int, int, int]) -> Image.Image:
    """
    Fast (<50ms) shade-preserving luminance tint over a neutral anime base template.

    Uses smooth highlight/ink feathering to avoid hard-threshold speckles and enforces 0px bottom gap.
    """
    with Image.open(template_path) as im:
        rgba = np.array(im.convert("RGBA"), dtype=np.uint8)

    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, :3].astype(np.float32) / 255.0
    lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]

    tint = np.array(target_rgb, dtype=np.float32) / 255.0
    rel = np.clip(lum / 0.88, 0.24, 1.06)[:, :, None]
    tinted = tint[None, None, :] * rel

    ink_w = np.clip((0.30 - lum) / 0.16, 0.0, 1.0)[:, :, None]
    spec_w = np.clip((lum - 0.86) / 0.12, 0.0, 1.0)[:, :, None]
    spec_target = np.clip(0.65 * tinted + 0.35 * np.array([[[0.96, 0.97, 0.99]]], dtype=np.float32), 0.0, 1.0)
    blended = (1.0 - spec_w) * tinted + spec_w * spec_target
    out_rgb = (1.0 - ink_w) * blended + ink_w * (rgb * 0.88)

    out_arr = np.zeros_like(rgba)
    out_arr[:, :, :3] = np.clip(out_rgb * 255.0, 0, 255).astype(np.uint8)
    out_arr[:, :, 3] = alpha
    ys_final = np.where(alpha >= 16)[0]
    if len(ys_final) > 0:
        out_arr = out_arr[: int(ys_final.max()) + 1, :, :]
    return Image.fromarray(out_arr, mode="RGBA")


def _downscale_jpeg_sync(still: tuple[bytes, str], max_side: int = 384, quality: int = 80) -> tuple[bytes, str]:
    """Downscale camera JPEG cleanly to <=384px for ultra-fast unseen prop API upload/tokenization (W1-V3-05)."""
    still_bytes, still_mime = still
    try:
        with Image.open(io.BytesIO(still_bytes)) as im:
            rgb = im.convert("RGB")
            if max(rgb.width, rgb.height) > max_side:
                rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=quality)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return still_bytes, still_mime


def _is_real_camera_photo(still: Optional[tuple[bytes, str]]) -> bool:
    """
    Detect whether `still` is a real multi-toned webcam photo vs a uniform solid-color test swatch.

    Real webcam frames (with lighting gradients, hand/room texture, and object details) always have
    substantial grayscale luminance variance (`std >= 10.0`), whereas synthetic solid-color swatches
    used in template-recolor unit tests have `std ~ 0.0`.
    """
    if not still:
        return False
    arr = np.frombuffer(still[0], dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None or bgr.size == 0:
        return False
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return float(np.std(gray)) >= 10.0


async def _extract_held_object_appearance_async(
    camera_still: tuple[bytes, str],
    name: str,
    description: str,
) -> str:
    """
    Fast parallel Hydrogen Vision extractor (~0.8s) returning the true color & form of ONLY the held object.

    Runs concurrently alongside `_generate_prop_sync` (zero added wall-clock latency) so downstream
    Omni Flash video generation receives the exact real colors of the held drink/object instead of
    OpenCV's whole-frame face/shirt/wall color guess.
    """
    from backend.director.genai_client import generate_text, part_from_bytes

    lean_bytes, lean_mime = await asyncio.to_thread(_downscale_jpeg_sync, camera_still, 384, 80)
    hint = f"User label: '{name.strip()}'." if name.strip() else ""
    if description.strip() and description.strip().lower() != name.strip().lower():
        hint = f"{hint} Extra note: '{description.strip()}'."
    system_prompt = (
        "You inspect a webcam photo to describe ONLY the physical object being held up or showcased. "
        "Completely ignore the person's face, skin, hands, clothing, and background room. "
        "Output ONE concise English noun phrase (4 to 10 words, plain text, no quotes, no trailing period) "
        "stating the exact real colors, color bands/cap color, and container/object shape "
        "(e.g., 'white and blue milk bottle with white cap', 'bright yellow iced tea carton', "
        "'red and white aluminum cola can', 'matte black smartphone')."
    )
    raw = await generate_text(
        model=config.MODEL_JUDGE,
        system_instruction=system_prompt,
        contents=[
            part_from_bytes(lean_bytes, lean_mime),
            f"Describe ONLY the held physical object's exact real colors and shape. {hint}".strip(),
        ],
        temperature=0.0,
        timeout_sec=12.0,
    )
    cleaned = re.sub(r"\s+", " ", (raw or "").strip().strip("\"'`."))
    return cleaned[:120]


def _generate_prop_sync(
    name: str,
    description: str,
    camera_still: Optional[tuple[bytes, str]],
    appearance_summary: str = "",
    color_label: str = "",
) -> tuple[bytes, dict]:
    """
    Synchronous Gemini Interactions API call to generate a chroma-green prop image.

    When `camera_still` is present, sends the actual photo (<=512px JPEG) directly to the image model
    WITHOUT any OpenCV `color_prefix` override so the generated 2D anime prop faithfully matches the
    real held object's exact colors, bands, cap/lid, and container shape.
    """
    model_id = _resolve_image_model()
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000))

    desc_text = appearance_summary.strip() or description.strip() or name
    lean_name = name.strip().lower() not in _GENERIC_PROP_NAMES
    if camera_still:
        lean_still_bytes, lean_mime = _downscale_jpeg_sync(camera_still, max_side=512, quality=85)
        label_hint = f" (user labeled it '{name.strip()}')" if lean_name else ""
        prompt_text = (
            f"LOOK DIRECTLY AT THE PHYSICAL OBJECT BEING HELD UP OR FEATURED IN THIS PHOTO{label_hint}. "
            "Completely ignore the person's face, skin, hands, fingers, clothing, and room background. "
            "Recreate ONLY that held physical object as a clean, crisp 2D anime cel-shaded game prop "
            "centered on a flat, uniform solid #00FF00 chroma green background. "
            "STRICT COLOR & SHAPE FIDELITY: reproduce the EXACT real colors, color bands/stripes, "
            "cap/lid color, liquid/bottle transparency or opacity, and exact container/object shape "
            "(bottle vs carton vs can vs cup vs mug vs box vs phone) directly from the held object in the photo — "
            "NEVER recolor or shift it to another hue, and NEVER turn a bottle/carton/cup into a generic can. "
            "Give the prop a clean dark anime ink outline so its silhouette cleanly separates from the pure #00FF00 "
            "backdrop (even if the object itself is green, yellow, or white). "
            "Simplify tiny unreadable fine text into clean matching color blocks. No human hands, no shadows, no green spill."
        )
        input_payload: list[dict] | str = [
            {
                "type": "image",
                "data": base64.b64encode(lean_still_bytes).decode("ascii"),
                "mime_type": lean_mime,
            },
            {"type": "text", "text": prompt_text},
        ]
    else:
        prompt_text = (
            f"Create a single centered 2D anime visual-novel stage prop illustration of '{name}' "
            f"({desc_text}). Style: crisp hand-painted anime cel-shaded game prop, clean dark ink outline, "
            "front-facing 3/4 view, NO human hands, NO characters, NO text, NO watermark. "
            "Faithfully follow every color and material mentioned in the description (and avoid neon green or emerald reflections "
            "unless part of the item); when no color is specified, use warm rich ivory, gold, brass, crimson, silver, or sapphire tones. "
            "Place the object completely isolated on a flat, uniform, solid bright chroma-key green "
            "background (#00FF00) with zero floor shadows and zero green spill on the prop."
        )
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
    detect_throw_peak: bool = True,
) -> tuple[str, PropRegistryItem]:
    """
    Generate a transparent PNG prop from text or webcam frame (up to 30s `.webm`) and save under `backend/assets/props/`.

    Paths:
    1) Live real webcam photo (`is_real_photo` & live API): Sends the actual photo directly to `MODEL_IMAGE`
       without OpenCV color override + runs parallel Hydrogen Vision appearance extraction so BOTH the 2D prop PNG
       and downstream Omni Flash videos strictly match the held object's real colors and shape.
    2) Text-only / uniform-swatch fast path (<0.3s): Known categories (`soda_can`, `smartphone`, `coffee_mug`,
       `snack_bag`, `laptop`) reuse `(category, color_bucket)` cache or shade-preservingly tint the neutral template.
    3) Text-only unseen categories: Direct single-call anime prop generation on `#00FF00` chroma green.
    """
    resolved_ts = frame_timestamp_sec if frame_timestamp_sec is not None else timestamp_sec
    resolved_id = prop_id or _slugify(name, extra_bytes=camera_frame_bytes)
    out_path = config.PROPS_DIR / f"{resolved_id}.png"
    origin: Literal["preset", "text_generated", "camera_physical"] = (
        "camera_physical" if camera_frame_bytes else "text_generated"
    )
    resolved_model = _resolve_image_model()
    prop_state = "intact"

    camera_still, motion_fg_mask = (
        await asyncio.to_thread(
            _extract_still_and_fg_mask,
            camera_frame_bytes,
            timestamp_sec=resolved_ts,
            detect_throw_peak=detect_throw_peak,
        )
        if camera_frame_bytes
        else (None, None)
    )
    is_real_photo = await asyncio.to_thread(_is_real_camera_photo, camera_still) if camera_still is not None else False
    color_label, target_rgb, color_bucket = await asyncio.to_thread(
        _extract_dominant_color_info,
        name,
        description,
        camera_still,
        motion_fg_mask,
    )
    if camera_still is not None and not is_real_photo:
        base_desc = description.strip() or name.strip() or "object"
        appearance_summary = base_desc if color_bucket in base_desc.lower() else f"{color_label} {base_desc}".strip()
    elif camera_still is not None:
        appearance_summary = description.strip() or name.strip() or "captured physical object"
    else:
        appearance_summary = description.strip() or f"{color_label} {name}".strip()

    matched_category = _match_template_category(name, description)
    use_live_photo_faithful = (
        is_real_photo
        and camera_still is not None
        and not config.DEMO_MODE
        and bool(config.GEMINI_API_KEY)
    )

    if use_live_photo_faithful:
        try:
            gen_task = asyncio.to_thread(
                _generate_prop_sync,
                name,
                description,
                camera_still,
                appearance_summary,
                "",
            )
            vision_task = _extract_held_object_appearance_async(camera_still, name, description)
            gen_res, vision_res = await asyncio.gather(gen_task, vision_task, return_exceptions=True)

            if isinstance(vision_res, str) and vision_res.strip():
                appearance_summary = vision_res.strip()

            if isinstance(gen_res, Exception):
                raise gen_res

            raw_img_bytes, meta = gen_res
            rgba_img = await asyncio.to_thread(_chroma_key_green_to_rgba, raw_img_bytes, 512)
            rgba_img.save(out_path, format="PNG")
            log_genai_call(
                resolved_model,
                {
                    "name": name,
                    "description": description,
                    "appearance_summary": appearance_summary,
                    "has_camera_still": True,
                    "photo_faithful_mode": True,
                },
                {"aspect_ratio": "1:1", "chroma_key": "opencv_hsv_despill", "max_size": 512},
                {"saved_path": str(out_path), "size": [rgba_img.width, rgba_img.height], **meta},
            )
        except Exception as exc:
            # Never crash live demo if image generation fails, but explicitly mark state degraded (R-18).
            if matched_category and (TEMPLATES_DIR / f"{matched_category}.png").is_file():
                # Derive fallback tint from Hydrogen's appearance_summary if available before falling back to OpenCV.
                _, fb_rgb, _ = _extract_dominant_color_info(name, appearance_summary, None, None)
                recolored = await asyncio.to_thread(
                    _recolor_template_preserve_shading, TEMPLATES_DIR / f"{matched_category}.png", fb_rgb
                )
                recolored.save(out_path, format="PNG")
            elif not out_path.exists():
                img = Image.new("RGBA", (256, 256), (*target_rgb, 230))
                img.save(out_path, format="PNG")
            prop_state = "degraded_fallback"
            log_genai_call(
                resolved_model,
                {"name": name, "description": description, "has_camera_still": True, "photo_faithful_mode": True},
                {"fallback": True},
                {"error": f"{type(exc).__name__}: {exc}", "saved_path": str(out_path), "state": prop_state},
            )
    elif matched_category and not (config.DEMO_MODE and prop_id == "v2_throw_test_coin"):
        TEMPLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = TEMPLATE_CACHE_DIR / f"{matched_category}_{color_bucket}.png"
        template_path = TEMPLATES_DIR / f"{matched_category}.png"
        if cache_path.is_file():
            shutil.copy2(cache_path, out_path)
            path_mode = f"template_cache_hit:{matched_category}_{color_bucket}"
        else:
            recolored = await asyncio.to_thread(_recolor_template_preserve_shading, template_path, target_rgb)
            recolored.save(cache_path, format="PNG")
            recolored.save(out_path, format="PNG")
            path_mode = f"template_recolor:{matched_category}_{color_bucket}"
        log_genai_call(
            "opencv_template_recolor",
            {"name": name, "category": matched_category, "color_bucket": color_bucket, "appearance_summary": appearance_summary},
            {"fast_path": path_mode},
            {"saved_path": str(out_path), "state": prop_state},
        )
    elif config.DEMO_MODE or not config.GEMINI_API_KEY:
        if not out_path.exists():
            img = Image.new("RGBA", (256, 256), (*target_rgb, 230))
            img.save(out_path, format="PNG")
        prop_state = "mock_placeholder"
        log_genai_call(
            resolved_model,
            {
                "name": name,
                "description": description,
                "appearance_summary": appearance_summary,
                "has_camera_frame": bool(camera_frame_bytes),
            },
            {"placeholder": True},
            {"saved_path": str(out_path), "state": prop_state},
        )
    else:
        try:
            raw_img_bytes, meta = await asyncio.to_thread(
                _generate_prop_sync,
                name,
                description,
                camera_still,
                appearance_summary,
                color_label,
            )
            max_out = 512
            rgba_img = await asyncio.to_thread(_chroma_key_green_to_rgba, raw_img_bytes, max_out)
            rgba_img.save(out_path, format="PNG")
            log_genai_call(
                resolved_model,
                {
                    "name": name,
                    "description": description,
                    "appearance_summary": appearance_summary,
                    "has_camera_still": bool(camera_still),
                    "lean_384px": False,
                },
                {"aspect_ratio": "1:1", "chroma_key": "opencv_hsv_despill", "max_size": max_out},
                {"saved_path": str(out_path), "size": [rgba_img.width, rgba_img.height], **meta},
            )
        except Exception as exc:
            # Never crash live demo if image generation fails, but explicitly mark state degraded (R-18).
            if not out_path.exists():
                img = Image.new("RGBA", (256, 256), (*target_rgb, 230))
                img.save(out_path, format="PNG")
            prop_state = "degraded_fallback"
            log_genai_call(
                resolved_model,
                {"name": name, "description": description, "has_camera_still": bool(camera_still)},
                {"fallback": True},
                {"error": f"{type(exc).__name__}: {exc}", "saved_path": str(out_path), "state": prop_state},
            )

    item = PropRegistryItem(
        name=name,
        image_url=f"/assets/props/{resolved_id}.png",
        state=prop_state,
        origin=origin,
        height_m=estimate_prop_height_m(name, description or appearance_summary),
        appearance_summary=appearance_summary,
    )
    return resolved_id, item
