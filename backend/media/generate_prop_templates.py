"""
Generate the 5 neutral-white/light-gray 2D anime base prop templates under
`backend/assets/props/templates/` for W1-V3-04 (<0.3s shade-preserving recolor):
- `soda_can.png`
- `smartphone.png`
- `coffee_mug.png`
- `snack_bag.png`
- `laptop.png`
"""

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import config
from backend.media.audit_props import audit_single_prop
from backend.media.prop_maker import _chroma_key_green_to_rgba, _generate_prop_sync

TEMPLATES_DIR = config.PROPS_DIR / "templates"

TEMPLATE_SPECS: list[tuple[str, str, str]] = [
    (
        "soda_can",
        "Neutral White Aluminum Soda Can Template",
        "Pure matte white and pale neutral light-gray unbranded blank aluminum soda beverage can with neutral silver pull-tab, "
        "clean dark ink anime outlines and smooth soft grey cel shading ONLY (zero colored logos, zero text, zero color tint)",
    ),
    (
        "smartphone",
        "Neutral White Modern Smartphone Template",
        "Pure neutral pale light-gray and white minimalist modern rectangular smartphone in 3/4 view with dark grey glass display screen "
        "and clean charcoal anime outlines (zero colored wallpaper, zero logos, neutral grayscale cel shading ONLY)",
    ),
    (
        "coffee_mug",
        "Neutral White Ceramic Coffee Mug Template",
        "Pure matte white and soft neutral light-gray ceramic cylindrical coffee mug with ear handle and crisp charcoal anime outlines, "
        "smooth neutral grey cel shading ONLY (no patterns, no color tint)",
    ),
    (
        "snack_bag",
        "Neutral White Sealed Snack Pouch Template",
        "Pure matte white and pale neutral light-gray puffy foil chips snack bag pouch with crimped top/bottom seals, "
        "clean dark anime outlines and soft grey cel shading ONLY (blank label, zero colored graphics, zero text)",
    ),
    (
        "laptop",
        "Neutral White Open Laptop Template",
        "Open minimalist pure matte white and pale neutral light-gray notebook laptop computer in 3/4 view with neutral dark-grey display screen, "
        "crisp charcoal anime outlines and neutral grayscale cel shading ONLY",
    ),
]


async def main() -> None:
    if config.DEMO_MODE or not config.GEMINI_API_KEY:
        raise RuntimeError("Live GEMINI_API_KEY required to generate neutral prop templates.")
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for cat, name, desc in TEMPLATE_SPECS:
        out_path = TEMPLATES_DIR / f"{cat}.png"
        if not out_path.is_file():
            raw_bytes, _meta = await asyncio.to_thread(_generate_prop_sync, name, desc, None, desc)
            rgba = await asyncio.to_thread(_chroma_key_green_to_rgba, raw_bytes, 384)
            rgba.save(out_path, format="PNG")
        audit = audit_single_prop(out_path)
        print(cat, audit)
        assert audit["passed"], f"Template audit failed for {cat}: {audit}"
    print("ALL 5 NEUTRAL PROP TEMPLATES GENERATED & AUDITED CLEANLY!")


if __name__ == "__main__":
    asyncio.run(main())
