"""
Paint the two fixed room plates (one per viewpoint) from the 3D geometry of the empty room.

Run once: `python backend/media/build_stage_plates.py`. The plates are committed; every 3D-first start frame is
composited onto them, so the room and both viewpoints are identical in every scene.
"""

import argparse
import base64
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env", override=False)

from google import genai
from google.genai import types

from backend import config
from backend.media.stage3d import PLATE_PATHS, render_empty_room_geometry

STYLE_REFS = {
    "cam_a": config.ASSETS_DIR / "stages" / "opening_cam_a.png",
    "cam_b": config.ASSETS_DIR / "stages" / "opening_cam_b.png",
}


def paint_plate(cam_id: str, out_path: Path, image_model: str) -> Path:
    geometry = render_empty_room_geometry(cam_id, config.ASSETS_DIR / "stages" / f"geometry_{cam_id}.png")
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=90_000))
    prompt = (
        "Image 1 is a plain 3D block-out of an empty room with one round pedestal table in the middle of a wooden "
        "floor. Image 2 shows the art style and the kind of room to paint. Paint Image 1 as a finished anime "
        "background in the style of Image 2: a Victorian glass conservatory at night, dark iron window frames, tall "
        "glass panes with a dark rainy city outside, warm wall lanterns, a warm herringbone wooden floor, a few "
        "potted ferns against the walls. KEEP THE GEOMETRY OF IMAGE 1 EXACTLY: the same viewpoint and perspective, "
        "the floor meets the walls along the same lines, and the round wooden pedestal table stands at exactly the "
        "same place with exactly the same size and height as in Image 1. The room is EMPTY: no people, no animals, "
        "no umbrella, no vase, nothing on the table and nothing on the floor. The floor around the table is clear "
        "and evenly lit. No text. Full 16:9 frame without borders."
    )
    parts = [
        {"type": "image", "data": base64.b64encode(geometry.read_bytes()).decode("ascii"), "mime_type": "image/png"},
        {"type": "image", "data": base64.b64encode(STYLE_REFS[cam_id].read_bytes()).decode("ascii"), "mime_type": "image/png"},
        {"type": "text", "text": prompt},
    ]
    interaction = client.interactions.create(
        model=image_model, input=parts, response_format={"type": "image", "aspect_ratio": "16:9"}
    )
    data = getattr(getattr(interaction, "output_image", None), "data", None)
    if not data:
        raise RuntimeError(f"image model returned no plate for {cam_id}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", default="cam_a,cam_b")
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()
    from backend.media.video_renderer import _resolve_image_model

    for cam in args.cams.split(","):
        target = PLATE_PATHS[cam]
        if args.suffix:
            target = target.with_name(f"{target.stem}{args.suffix}.png")
        print("painted", paint_plate(cam, target, _resolve_image_model()))
