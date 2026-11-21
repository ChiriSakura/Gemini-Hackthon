"""
W1-T7D: Non-English Input -> English Prop/Instruction Normalization Smoke Test (Owned by P2).

Feature & Use Cases:
- Verifies that mixed CJK, accented European, emoji, and punctuation inputs passed into
  P2 prop/video pipelines never break filesystem paths or leak CJK into persisted assets:
  1. `prop_maker._slugify()` produces strictly ASCII `[a-z0-9_]` collision-safe IDs even
     for pure-Chinese (`"金色星光信物"`), accented (`"Café Crème Tasse"`), or emoji inputs.
  2. `prop_maker.estimate_prop_height_m()` maps both English and Chinese keywords to
     identical physical SI heights (`"花瓶"` == `0.32m`, `"钥匙"` == `0.08m`, `"雨伞"` == `0.86m`).
  3. `create_prop()` with mixed CJK/accented input creates a valid ASCII-named RGBA PNG.
  4. `backend/director/prompts.py` enforces English-only narration/title/label contracts.
  5. Every persisted `backend/assets/branches/*.json` contains 0 CJK characters.
"""

import asyncio
import re
from pathlib import Path

from backend import config
from backend.director import prompts as director_prompts
from backend.media.prop_maker import _slugify, create_prop, estimate_prop_height_m

ASCII_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


async def run_english_enforced_tests() -> dict:
    """Run all W1-T7D ASCII-filename and English-normalization contract assertions."""
    # 1. Mixed/non-English _slugify() safety + collision resistance
    samples = [
        "金色星光信物",
        "红色折纸爱心 ❤️",
        "Café Crème Mug (Édition 2026)",
        "   !!!   ",
        "蓝色玫瑰水晶花瓶",
    ]
    slugs = [_slugify(s) for s in samples]
    for orig, sl in zip(samples, slugs):
        assert ASCII_SLUG_RE.match(sl), f"Non-ASCII slug generated for {orig!r}: {sl!r}"
        assert sl.isascii() and len(sl) >= 6
    assert len(set(slugs)) == len(samples), f"Slug collision detected: {slugs}"

    # 2. Bilingual SI height consistency
    assert estimate_prop_height_m("水晶花瓶") == estimate_prop_height_m("crystal vase") == 0.32
    assert estimate_prop_height_m("黄铜钥匙") == estimate_prop_height_m("brass key") == 0.08
    assert estimate_prop_height_m("红色长柄雨伞") == estimate_prop_height_m("red umbrella") == 0.86
    assert estimate_prop_height_m("星光信物") == estimate_prop_height_m("star token") == 0.10
    assert estimate_prop_height_m("陶瓷咖啡杯") == estimate_prop_height_m("ceramic coffee mug") == 0.13

    # 3. create_prop() safe ASCII output under mixed CJK/accented name (DEMO_MODE)
    prev_demo = config.DEMO_MODE
    created_paths: list[Path] = []
    try:
        config.DEMO_MODE = True
        for mixed_name in ("金色星光怀表", "Café Ceramic Mug"):
            pid, item = await create_prop(name=mixed_name, description="mixed locale input")
            assert ASCII_SLUG_RE.match(pid), f"Unsafe prop_id: {pid}"
            assert item.image_url == f"/assets/props/{pid}.png"
            out_png = config.PROPS_DIR / f"{pid}.png"
            assert out_png.is_file()
            created_paths.append(out_png)
    finally:
        config.DEMO_MODE = prev_demo
        for p in created_paths:
            p.unlink(missing_ok=True)

    # 4. Verify director prompts enforce English output contract
    prompt_blob = " ".join(
        str(getattr(director_prompts, attr, ""))
        for attr in dir(director_prompts)
        if attr.isupper()
    )
    assert "english" in prompt_blob.lower(), "Expected English output guidance in backend/director/prompts.py"

    # 5. Verify 0 CJK characters across all persisted branch JSONs
    branch_files = sorted(config.BRANCHES_DIR.glob("*.json"))
    assert len(branch_files) >= 4, f"Expected >= 4 cached branches, found {len(branch_files)}"
    for bf in branch_files:
        hits = CJK_RE.findall(bf.read_text(encoding="utf-8"))
        assert not hits, f"CJK leaked into {bf.name}: {hits[:5]}"

    report = {
        "passed": True,
        "slugs_tested": dict(zip(samples, slugs)),
        "bilingual_si_scale_verified": True,
        "director_prompts_english_contract_verified": True,
        "zero_cjk_branches_verified": [bf.name for bf in branch_files],
    }
    print("W1-T7D ENGLISH ENFORCED TESTS PASSED:", report)
    return report


if __name__ == "__main__":
    asyncio.run(run_english_enforced_tests())
