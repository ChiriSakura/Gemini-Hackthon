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
import time
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


def _slugify(name: str) -> str:
    """Create a collision-safe, filesystem-safe prop identifier with deterministic hash suffix."""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
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
            "popcorn",
            "cracker",
            "nibbles",
            "零食",
            "薯片",
            "饼干",
            "糖果",
        ),
        0.20,
    ),
    (("quill", "pen", "feather", "wand", "book", "diary", "letter", "bottle", "thermos", "jar", "羽毛笔", "书", "信", "瓶"), 0.22),
    (("lantern", "lamp", "candle", "bouquet", "flower", "rose", "laptop", "macbook", "computer", "notebook", "ipad", "tablet", "keyboard", "灯", "花束", "电脑", "笔记本", "平板"), 0.28),
    (("vase", "crystal_vase", "花瓶"), 0.32),
    (("plush", "bear", "doll", "box", "gift", "bag", "礼盒", "玩偶"), 0.36),
    (("shield", "guitar", "violin", "cello", "盾", "琴"), 0.65),
    (("chair", "wooden_chair", "wooden chair", "stool", "椅子", "凳子"), 0.85),
    (("umbrella", "parasol", "伞"), 0.86),
    (("sword", "staff", "cane", "剑", "法杖"), 0.95),
]


def estimate_prop_height_m(name: str, description: str = "") -> float:
    """Estimate realistic SI metric height in meters (clamped to 0.08m..1.20m) for a prop."""
    def _matches(text: str, kw: str) -> bool:
        if kw.isascii():
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text))
        return kw in text

    for scope in (name.lower().replace("_", " "), f"{name} {description}".lower().replace("_", " ")):
        for keywords, height_m in _PROP_HEIGHT_KEYWORDS:
            if any(_matches(scope, k) for k in keywords):
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

    # Fallback: treat bytes as a short video clip (up to 30s `.webm`) and grab target/throw-peak/midpoint frame.
    tmp_path: Optional[Path] = None
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


TEMPLATES_DIR = config.PROPS_DIR / "templates"
TEMPLATE_CACHE_DIR = config.PROPS_DIR / "cache"

_COLOR_BUCKETS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "yellow": ("bright yellow", (245, 210, 32)),
    "gold": ("warm gold", (228, 182, 54)),
    "orange": ("vibrant orange", (240, 128, 32)),
    "red": ("ruby red", (215, 44, 52)),
    "pink": ("rose pink", (236, 108, 166)),
    "purple": ("royal purple", (142, 76, 212)),
    "blue": ("cobalt blue", (46, 122, 228)),
    "cyan": ("sky cyan", (52, 192, 216)),
    "green": ("emerald green", (44, 168, 92)),
    "brown": ("warm brown", (148, 92, 52)),
    "black": ("matte black", (34, 38, 46)),
    "gray": ("charcoal gray", (102, 110, 122)),
    "silver": ("brushed silver", (192, 200, 212)),
    "white": ("clean white", (242, 244, 248)),
}

_TEXT_COLOR_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("yellow", "lemon", "canary", "黄", "黄色"), "yellow"),
    (("gold", "golden", "amber", "金", "金色"), "gold"),
    (("orange", "tangerine", "橙", "橘", "橙色"), "orange"),
    (("red", "crimson", "scarlet", "ruby", "maroon", "红", "红色"), "red"),
    (("pink", "magenta", "rose", "粉", "粉色"), "pink"),
    (("purple", "violet", "lavender", "紫", "紫色"), "purple"),
    (("cyan", "teal", "turquoise", "aqua", "青", "青色"), "cyan"),
    (("blue", "cobalt", "navy", "sapphire", "sky blue", "蓝", "蓝色"), "blue"),
    (("green", "emerald", "mint", "olive", "lime", "绿", "绿色"), "green"),
    (("brown", "bronze", "tan", "coffee", "chocolate", "棕", "棕色", "褐色"), "brown"),
    (("black", "obsidian", "jet", "ebony", "matte black", "黑", "黑色"), "black"),
    (("silver", "metallic", "aluminum", "aluminium", "chrome", "steel", "银", "银色"), "silver"),
    (("gray", "grey", "charcoal", "slate", "灰", "灰色"), "gray"),
    (("white", "ivory", "cream", "pearl", "白", "白色"), "white"),
]

_TEMPLATE_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("smartphone", ("smartphone", "phone", "cellphone", "mobile", "iphone", "android", "手机")),
    ("laptop", ("laptop", "macbook", "notebook computer", "computer", "ultrabook", "笔记本电脑", "电脑")),
    ("coffee_mug", ("coffee mug", "mug", "coffee cup", "teacup", "cup", "马克杯", "咖啡杯", "茶杯")),
    ("snack_bag", ("snack bag", "snack", "chips", "crisps", "potato chips", "popcorn", "candy bag", "cookie bag", "零食", "薯片")),
    ("soda_can", ("soda can", "drink can", "beverage can", "soda", "cola", "coke", "pepsi", "drink", "beverage", "can", "易拉罐", "汽水", "可乐", "饮料")),
]


def _keyword_matches(text: str, kw: str) -> bool:
    """Match ASCII keywords on word boundaries so 'can' never matches 'canopy' and 'pen' never matches 'open'."""
    if kw.isascii():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text))
    return kw in text


def _match_prop_template_category(name: str, description: str = "") -> Optional[str]:
    """Map a familiar everyday object to one of the 5 pre-generated neutral 2D anime templates (W1-V3-04)."""
    for scope in (name.lower().replace("_", " "), f"{name} {description}".lower().replace("_", " ")):
        for cat, keywords in _TEMPLATE_CATEGORY_KEYWORDS:
            if any(_keyword_matches(scope, kw) for kw in keywords):
                return cat
    return None


def _extract_dominant_color_info(
    image_bytes: Optional[bytes],
    name: str = "",
    description: str = "",
) -> tuple[str, str, tuple[int, int, int]]:
    """
    Extract exact primary object color in <5ms via OpenCV center-ROI HSV (or text color keywords)
    with ZERO extra LLM calls (W1-V3-01, W1-V3-04).

    Returns `(color_label, color_bucket, target_rgb)`.
    """
    if image_bytes:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is not None and bgr.size > 0:
            h, w = bgr.shape[:2]
            y0, y1 = int(h * 0.275), max(int(h * 0.275) + 1, int(h * 0.725))
            x0, x1 = int(w * 0.275), max(int(w * 0.275) + 1, int(w * 0.725))
            roi = cv2.resize(bgr[y0:y1, x0:x1], (64, 64), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
            h_ch = hsv[:, :, 0]
            s_ch = hsv[:, :, 1]
            v_ch = hsv[:, :, 2]

            chrom_mask = (s_ch >= 52) & (v_ch >= 42)
            chrom_ratio = float(np.mean(chrom_mask))
            if chrom_ratio >= 0.22:
                # 36-bin hue mode isolates the primary object hue cleanly against skin/background edges.
                chrom_h = h_ch[chrom_mask]
                hist, _ = np.histogram(chrom_h, bins=36, range=(0, 180))
                peak_bin = int(np.argmax(hist))
                peak_h = peak_bin * 5.0 + 2.5
                dh = np.abs(h_ch.astype(np.float32) - peak_h)
                dh = np.minimum(dh, 180.0 - dh)
                cluster = chrom_mask & (dh <= 12.0)
                if not np.any(cluster):
                    cluster = chrom_mask

                med_h = float(np.median(h_ch[cluster]))
                med_s = float(np.median(s_ch[cluster]))
                med_v = float(np.median(v_ch[cluster]))
                med_rgb = np.median(rgb[cluster].reshape(-1, 3), axis=0)

                if med_h < 8.0 or med_h >= 168.0:
                    bucket = "pink" if (med_s < 135.0 and med_v > 160.0) else "red"
                elif med_h < 19.0:
                    bucket = "brown" if med_v < 135.0 else "orange"
                elif med_h < 36.0:
                    bucket = "yellow"
                elif med_h < 84.0:
                    bucket = "green"
                elif med_h < 102.0:
                    bucket = "cyan"
                elif med_h < 132.0:
                    bucket = "blue"
                elif med_h < 156.0:
                    bucket = "purple"
                else:
                    bucket = "pink"

                label, canonical_rgb = _COLOR_BUCKETS[bucket]
                blended = tuple(
                    int(np.clip(round(0.65 * float(med_rgb[i]) + 0.35 * float(canonical_rgb[i])), 16, 250))
                    for i in range(3)
                )
                return label, bucket, (blended[0], blended[1], blended[2])

            med_v = float(np.median(v_ch))
            if med_v < 78.0:
                bucket = "black"
            elif med_v < 148.0:
                bucket = "gray"
            elif med_v < 212.0:
                bucket = "silver"
            else:
                bucket = "white"
            label, canonical_rgb = _COLOR_BUCKETS[bucket]
            return label, bucket, canonical_rgb

    # Text fallback when no camera bytes are present
    for scope in (name.lower().replace("_", " "), f"{name} {description}".lower().replace("_", " ")):
        for keywords, bucket in _TEXT_COLOR_KEYWORDS:
            if any(_keyword_matches(scope, kw) for kw in keywords):
                label, canonical_rgb = _COLOR_BUCKETS[bucket]
                return label, bucket, canonical_rgb

    return "", "neutral", (224, 228, 236)


def _build_appearance_summary(name: str, description: str, color_label: str, color_bucket: str) -> str:
    """Construct concise faithful appearance summary without redundant duplicate color words."""
    clean_name = name.strip()
    if color_label:
        # Avoid 'bright yellow yellow soda can' if the user already named the color.
        if color_bucket and _keyword_matches(clean_name.lower(), color_bucket):
            # Replace bare bucket word with richer color label once if distinct
            return re.sub(rf"\b{re.escape(color_bucket)}\b", color_label, clean_name, count=1, flags=re.IGNORECASE).strip()
        return f"{color_label} {clean_name}".strip()
    clean_desc = description.strip()
    if clean_desc and len(clean_desc) <= 72:
        return clean_desc
    return clean_name


def _downscale_jpeg_sync(jpeg_bytes: bytes, max_dim: int = 384, quality: int = 80) -> bytes:
    """Downscale captured still to <=384px JPEG q=80 to minimize Gemini image upload/tokenization latency (W1-V3-05)."""
    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as im:
            rgb = im.convert("RGB")
            if max(rgb.width, rgb.height) > max_dim:
                rgb.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()
    except Exception:
        return jpeg_bytes


def _recolor_template_rgba(
    template_path: Path,
    category: str,
    target_rgb: tuple[int, int, int],
    color_bucket: str,
    max_size: int = 384,
) -> Image.Image:
    """
    Shade-preservingly recolor a neutral-white/light-gray 2D anime template PNG to `target_rgb`
    in <50ms while preserving dark cel-ink outlines, specular shine, dark device screens,
    RGBA alpha transparency, 0px bottom-flush alignment, and 0 green halo pixels (W1-V3-04).
    """
    with Image.open(template_path) as im:
        rgba = np.array(im.convert("RGBA"), dtype=np.uint8)

    h, w = rgba.shape[:2]
    rgb_f = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3]
    lum = (0.299 * rgb_f[:, :, 0] + 0.587 * rgb_f[:, :, 1] + 0.114 * rgb_f[:, :, 2]) / 255.0
    out_rgb = rgb_f.copy()

    opaque = alpha > 0
    # Preserve crisp dark cel-shaded outlines (<0.18 lum) verbatim.
    body_mask = opaque & (lum >= 0.18)

    if category in {"smartphone", "laptop"}:
        # Preserve glossy dark-slate screen (0.18 <= lum < 0.56); recolor outer chassis/bezel (lum >= 0.56).
        chassis_mask = opaque & (lum >= 0.56)
        screen_mask = opaque & (lum >= 0.18) & (lum < 0.56)
        if color_bucket == "black":
            # Sleek matte-black chassis + deep glossy obsidian screen preserving diagonal specular reflections.
            shade_c = np.clip((lum - 0.50) / 0.50, 0.0, 1.0)
            for c_idx, base_c in enumerate((30.0, 34.0, 42.0)):
                out_rgb[:, :, c_idx] = np.where(chassis_mask, base_c + shade_c * 34.0, out_rgb[:, :, c_idx])
            out_rgb[:, :, 0] = np.where(screen_mask, rgb_f[:, :, 0] * 0.45, out_rgb[:, :, 0])
            out_rgb[:, :, 1] = np.where(screen_mask, rgb_f[:, :, 1] * 0.46, out_rgb[:, :, 1])
            out_rgb[:, :, 2] = np.where(screen_mask, rgb_f[:, :, 2] * 0.50, out_rgb[:, :, 2])
        elif color_bucket not in {"silver", "white", "neutral"}:
            shade_c = np.clip(lum / 0.88, 0.30, 1.08)
            spec = np.clip((lum - 0.92) / 0.08, 0.0, 1.0)
            for c_idx in range(3):
                tinted = shade_c * float(target_rgb[c_idx])
                tinted = tinted * (1.0 - 0.30 * spec) + 255.0 * (0.30 * spec)
                out_rgb[:, :, c_idx] = np.where(chassis_mask, tinted, out_rgb[:, :, c_idx])
        elif color_bucket == "silver":
            # Keep clean cool brushed aluminum tones
            for c_idx, mult in enumerate((0.95, 0.98, 1.03)):
                out_rgb[:, :, c_idx] = np.where(chassis_mask, rgb_f[:, :, c_idx] * mult, out_rgb[:, :, c_idx])
    else:
        # soda_can, coffee_mug, snack_bag
        tint_weight = np.ones((h, w), dtype=np.float32)
        if category == "soda_can":
            # Smoothly transition from metallic silver pull-tab rim (top 14%) into full body tint (below 19%).
            r0 = int(round(h * 0.14))
            r1 = max(r0 + 1, int(round(h * 0.19)))
            tint_weight[:r0, :] = 0.08
            ramp = np.linspace(0.08, 1.0, r1 - r0, dtype=np.float32)[:, None]
            tint_weight[r0:r1, :] = ramp

        if color_bucket == "black":
            shade = np.clip((lum - 0.18) / 0.82, 0.0, 1.0)
            spec = np.clip((lum - 0.90) / 0.10, 0.0, 1.0)
            for c_idx, base_c in enumerate((26.0, 30.0, 38.0)):
                dark_val = base_c + shade * 46.0 + spec * 52.0
                blended = rgb_f[:, :, c_idx] * (1.0 - tint_weight) + dark_val * tint_weight
                out_rgb[:, :, c_idx] = np.where(body_mask, blended, out_rgb[:, :, c_idx])
        elif color_bucket in {"silver", "white", "neutral"}:
            target_scale = np.array(target_rgb, dtype=np.float32) / 235.0
            for c_idx in range(3):
                val = rgb_f[:, :, c_idx] * target_scale[c_idx]
                blended = rgb_f[:, :, c_idx] * (1.0 - tint_weight) + val * tint_weight
                out_rgb[:, :, c_idx] = np.where(body_mask, blended, out_rgb[:, :, c_idx])
        else:
            shade = np.clip(lum / 0.88, 0.25, 1.08)
            spec = np.clip((lum - 0.93) / 0.07, 0.0, 1.0)
            for c_idx in range(3):
                tinted = shade * float(target_rgb[c_idx])
                tinted = tinted * (1.0 - 0.35 * spec) + 255.0 * (0.35 * spec)
                blended = rgb_f[:, :, c_idx] * (1.0 - tint_weight) + tinted * tint_weight
                out_rgb[:, :, c_idx] = np.where(body_mask, blended, out_rgb[:, :, c_idx])

    out_rgba = np.concatenate([np.clip(out_rgb, 0, 255).astype(np.uint8), alpha[:, :, None]], axis=-1)
    # Zero out invisible alpha dust and guarantee 0px bottom-flush crop.
    out_rgba[:, :, 3] = np.where(out_rgba[:, :, 3] < 16, 0, out_rgba[:, :, 3])
    ys = np.where(out_rgba[:, :, 3] >= 16)[0]
    if len(ys) > 0:
        out_rgba = out_rgba[: int(ys.max()) + 1, :, :]
    pil_out = Image.fromarray(out_rgba, mode="RGBA")
    if max(pil_out.width, pil_out.height) > max_size:
        pil_out.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return pil_out


def _generate_prop_sync(
    name: str,
    description: str,
    camera_still: Optional[tuple[bytes, str]],
    color_label: str = "",
    appearance_summary: str = "",
) -> tuple[bytes, dict]:
    """
    Synchronous Gemini Interactions API call to generate a chroma-green prop image.

    W1-V3-01 & W1-V3-05:
    - Never overrides real captured colors with a hardcoded ivory/gold palette.
    - Downscales `camera_still` to <=384px JPEG q=80 and uses an ultra-lean shape + primary-color
      prompt to minimize generation latency for unseen object categories.
    """
    model_id = _resolve_image_model()
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000))

    subject_desc = appearance_summary.strip() or (f"{color_label} {name}".strip() if color_label else name.strip())
    color_clause = (
        f"Preserve ONLY what the object is and its EXACT primary color ({color_label}) — NEVER recolor to gold or ivory; "
        if color_label
        else "Preserve the exact real-world color and silhouette of the object; "
    )
    prompt_text = (
        f"Simple clean 2D anime cel-shaded game icon of a single '{subject_desc}': "
        f"{color_clause}"
        "omit fine labels, small text, tiny logos, or complex lighting details. "
        "Clean front-facing 3/4 view, crisp silhouette, NO human hands, NO characters, NO watermark, NO floor shadow. "
        "Completely isolated on a flat, uniform, solid bright chroma-key green (#00FF00) background."
    )

    if camera_still:
        still_bytes, _ = camera_still
        compact_jpeg = _downscale_jpeg_sync(still_bytes, max_dim=384, quality=80)
        input_payload: list[dict] | str = [
            {
                "type": "image",
                "data": base64.b64encode(compact_jpeg).decode("ascii"),
                "mime_type": "image/jpeg",
            },
            {
                "type": "text",
                "text": (
                    "Convert the physical object shown in this photo into a "
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
        return raw_bytes, {"model": model_id, "interaction_id": getattr(interaction, "id", None), "lean_mode": True}

    for step in getattr(interaction, "steps", []) or []:
        if getattr(step, "type", None) == "model_output":
            for block in getattr(step, "content", []) or []:
                if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                    raw_bytes = base64.b64decode(block.data)
                    return raw_bytes, {"model": model_id, "interaction_id": getattr(interaction, "id", None), "lean_mode": True}

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

    Fast Paths & Fidelity (W1-V3-01, W1-V3-04, W1-V3-05):
    - Extracts exact center-ROI dominant HSV/RGB color in <5ms (zero extra LLM calls) and builds
      a faithful `appearance_summary` + collision-free photo-hash `prop_id`.
    - For familiar everyday categories (`soda_can`, `smartphone`, `coffee_mug`, `snack_bag`, `laptop`),
      recolors the pre-generated neutral 2D anime template (with `(category, color_bucket)` 0ms disk reuse)
      in <0.2s — skipping the 15s diffusion round-trip entirely!
    - For brand-new unseen categories, uses Ultra-Fast Lean Mode (`<=384px` JPEG payload + simplified
      shape/primary-color prompt + `max_size=384` cutout).
    - Flags placeholder/mock fallbacks explicitly in `PropRegistryItem.state` (`degraded_fallback` / `mock_placeholder`, R-18).
    """
    t_start = time.perf_counter()
    resolved_ts = frame_timestamp_sec if frame_timestamp_sec is not None else timestamp_sec
    origin: Literal["preset", "text_generated", "camera_physical"] = (
        "camera_physical" if camera_frame_bytes else "text_generated"
    )
    resolved_model = _resolve_image_model()
    prop_state = "intact"

    # Extract still frame + dominant color (<5ms) FIRST so both `prop_id` and `appearance_summary`
    # reflect the exact real-world captured object even in DEMO_MODE or fast template paths.
    camera_still = (
        _extract_image_from_bytes(
            camera_frame_bytes,
            timestamp_sec=resolved_ts,
            detect_throw_peak=detect_throw_peak,
        )
        if camera_frame_bytes
        else None
    )
    color_label, color_bucket, target_rgb = _extract_dominant_color_info(
        camera_still[0] if camera_still else None,
        name=name,
        description=description,
    )
    appearance_summary = _build_appearance_summary(name, description, color_label, color_bucket)

    if prop_id:
        resolved_id = prop_id
    else:
        base_slug = _slugify(name)
        if camera_frame_bytes:
            frame_digest = hashlib.sha1(camera_still[0] if camera_still else camera_frame_bytes).hexdigest()[:4]
            resolved_id = f"{base_slug}_{color_bucket}_{frame_digest}"
        else:
            resolved_id = base_slug

    out_path = config.PROPS_DIR / f"{resolved_id}.png"
    template_cat = _match_prop_template_category(name, description)
    template_path = (TEMPLATES_DIR / f"{template_cat}.png") if template_cat else None

    # W1-V3-04: Ultra-fast (<0.2s) category-template + dominant-color recolor with exact-match disk reuse!
    if template_cat and template_path is not None and template_path.is_file():
        TEMPLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = TEMPLATE_CACHE_DIR / f"tpl_{template_cat}_{color_bucket}.png"
        cache_hit = cache_path.is_file() and cache_path.stat().st_mtime >= template_path.stat().st_mtime
        if cache_hit:
            shutil.copy2(cache_path, out_path)
        else:
            recolored = await asyncio.to_thread(
                _recolor_template_rgba,
                template_path,
                template_cat,
                target_rgb,
                color_bucket,
                384,
            )
            recolored.save(cache_path, format="PNG")
            shutil.copy2(cache_path, out_path)
        elapsed_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
        log_genai_call(
            f"template_tint:{template_cat}",
            {
                "name": name,
                "appearance_summary": appearance_summary,
                "color_bucket": color_bucket,
                "has_camera_frame": bool(camera_frame_bytes),
            },
            {"fast_template_tint": True, "cache_hit": cache_hit},
            {"saved_path": str(out_path), "elapsed_ms": elapsed_ms, "state": prop_state},
        )
    elif config.DEMO_MODE or not config.GEMINI_API_KEY:
        if not out_path.exists():
            img = Image.new("RGBA", (256, 256), (255, 215, 64, 230))
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
                color_label,
                appearance_summary,
            )
            rgba_img = await asyncio.to_thread(_chroma_key_green_to_rgba, raw_img_bytes, 384)
            rgba_img.save(out_path, format="PNG")
            log_genai_call(
                resolved_model,
                {
                    "name": name,
                    "description": description,
                    "appearance_summary": appearance_summary,
                    "has_camera_still": bool(camera_still),
                },
                {"aspect_ratio": "1:1", "chroma_key": "opencv_hsv_despill", "max_size": 384},
                {"saved_path": str(out_path), "size": [rgba_img.width, rgba_img.height], **meta},
            )
        except Exception as exc:
            # Never crash live demo if image generation fails, but explicitly mark state degraded (R-18).
            if not out_path.exists():
                img = Image.new("RGBA", (256, 256), (255, 215, 64, 230))
                img.save(out_path, format="PNG")
            prop_state = "degraded_fallback"
            log_genai_call(
                resolved_model,
                {
                    "name": name,
                    "description": description,
                    "appearance_summary": appearance_summary,
                    "has_camera_still": bool(camera_still),
                },
                {"fallback": True},
                {"error": f"{type(exc).__name__}: {exc}", "saved_path": str(out_path), "state": prop_state},
            )

    item = PropRegistryItem(
        name=name,
        image_url=f"/assets/props/{resolved_id}.png",
        state=prop_state,
        origin=origin,
        height_m=estimate_prop_height_m(name, description),
        appearance_summary=appearance_summary,
    )
    return resolved_id, item
