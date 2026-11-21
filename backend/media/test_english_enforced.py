"""
W1-T7D: Non-English Input -> English Prop/Instruction Normalization Smoke Test (Owned by P2).

Verifies:
1. `_slugify()` and `create_prop()` produce deterministic ASCII-safe `prop_id`s and
   zero-collision PNG filenames even on CJK, accented Latin, and mixed strings.
2. Director prompts (`HYDROGEN_HEAR_SEE_PROMPT`, `NEON_DIRECTOR_PROMPT`) explicitly
   enforce natural English outputs for `instruction`, `prop_name`, `prop_description`,
   `title`, and `narration`.
3. Zero CJK characters leak into any persisted `backend/assets/branches/*.json` files.
"""

import asyncio
import re

from backend import config
from backend.director.prompts import HYDROGEN_HEAR_SEE_PROMPT, NEON_DIRECTOR_PROMPT
from backend.media.prop_maker import _slugify, create_prop, estimate_prop_height_m


def run_english_enforced_checks() -> None:
    """Assert ASCII filename normalization, prompt English rules, and 0 CJK in branch JSONs."""
    # 1. Mixed/accented/non-ASCII prop names -> clean ASCII slugs with deterministic hashes
    slug_samples = [
        ("星光怀表", "prop_"),
        ("Café Étoile Lamp", "cafe_etoile_lamp_"),
        ("玻璃花房-Lia_2", "lia_2_"),
        ("Origami Star!", "origami_star_"),
    ]
    seen: set[str] = set()
    for raw_name, expected_prefix in slug_samples:
        slug = _slugify(raw_name)
        assert re.fullmatch(r"[a-z0-9_]+", slug), f"Non-ASCII/unsafe slug for {raw_name}: {slug}"
        assert slug.startswith(expected_prefix), f"Unexpected slug {slug} for {raw_name}"
        assert slug not in seen, f"Collision on slug {slug}"
        seen.add(slug)

    assert _slugify("星光怀表") != _slugify("水晶护盾"), "Distinct CJK names must never collide!"

    # 2. End-to-end create_prop with non-English name under DEMO_MODE=True -> ASCII PNG filename
    prev_demo = config.DEMO_MODE
    config.DEMO_MODE = True
    created_path = None
    try:
        pid, item = asyncio.run(
            create_prop(
                name="星光怀表 Café Watch",
                description="Golden pocket watch with cyan hands",
            )
        )
        assert re.fullmatch(r"[a-z0-9_]+", pid), f"Unsafe prop_id: {pid}"
        assert item.image_url.isascii() and item.image_url.startswith("/assets/props/")
        created_path = config.ASSETS_DIR / item.image_url.removeprefix("/assets/")
        assert created_path.is_file()
    finally:
        config.DEMO_MODE = prev_demo
        if created_path is not None:
            created_path.unlink(missing_ok=True)

    # 3. Verify bilingual keywords still map safely to SI metric heights in meters
    assert estimate_prop_height_m("纸星星") == 0.10
    assert estimate_prop_height_m("雨伞") == 0.86

    # 4. Verify director prompts explicitly mandate English normalization
    assert "English" in HYDROGEN_HEAR_SEE_PROMPT and "accents or mixed languages" in HYDROGEN_HEAR_SEE_PROMPT
    assert "English" in NEON_DIRECTOR_PROMPT

    # 5. Verify zero CJK characters remain in all persisted branches/*.json
    for bjson in sorted(config.BRANCHES_DIR.glob("*.json")):
        hits = re.findall(r"[\u4e00-\u9fff]+", bjson.read_text(encoding="utf-8"))
        assert not hits, f"CJK characters leaked in {bjson.name}: {hits[:5]}"

    print("W1-T7D ENGLISH ENFORCED & ASCII SLUG TESTS PASSED!")


if __name__ == "__main__":
    run_english_enforced_checks()
