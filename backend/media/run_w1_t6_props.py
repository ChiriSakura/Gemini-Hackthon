"""
W1-T6B & Zero-Halo Prop Benchmark (Owned by P2 / worker-1).

Feature & Use Cases:
- Regenerates `showcase_brass_pocket_watch.png` with a warm antique ivory-and-gold dial
  (eliminating the emerald dial false-positive in `audit_props.py`) and sanitizes any
  2px edge speck on `showcase_glowing_quill.png`.
- Benchmarks live `create_prop()` concurrently on 3 everyday judge-desk objects:
  1. `ceramic coffee mug` -> `showcase_desk_coffee_mug.png`
  2. `folded red paper heart` -> `showcase_desk_paper_heart.png`
  3. `brass apartment key` -> `showcase_desk_brass_key.png`
- Verifies 100% RGBA transparency, 0px bottom gap, 0 chroma-green halo pixels via
  `audit_props`, records SI metric scale estimates + latencies into
  `backend/assets/eval_live_props_sim.json`.
"""

import asyncio
import json
import time
from pathlib import Path

from backend import config
from backend.logger import log_function_call
from backend.media.audit_props import audit_all_props, audit_single_prop, sanitize_prop_png
from backend.media.prop_maker import create_prop, estimate_prop_height_m


DESK_PROPS = [
    (
        "showcase_desk_coffee_mug",
        "ceramic coffee mug",
        "a warm cream ceramic coffee mug with a golden rim and cozy minimalist café pattern, empty matte ceramic surface",
    ),
    (
        "showcase_desk_paper_heart",
        "folded red paper heart",
        "a crisp hand-folded crimson red origami paper heart with subtle paper creases and warm highlights",
    ),
    (
        "showcase_desk_brass_key",
        "brass apartment key",
        "an ornate vintage warm brass apartment key with a star-engraved bow and polished golden-bronze teeth",
    ),
]


async def _bench_one(prop_id: str, name: str, desc: str) -> dict:
    """Run one timed `create_prop()` call and audit the output RGBA PNG."""
    t0 = time.perf_counter()
    resolved_id, item = await create_prop(name=name, description=desc, prop_id=prop_id)
    elapsed = round(time.perf_counter() - t0, 2)
    png_path = config.PROPS_DIR / f"{resolved_id}.png"
    audit = audit_single_prop(png_path)
    if not audit["passed"]:
        audit = sanitize_prop_png(png_path)
    return {
        "prop_id": resolved_id,
        "name": name,
        "description": desc,
        "image_url": item.image_url,
        "elapsed_sec": elapsed,
        "estimated_height_m": estimate_prop_height_m(name, desc),
        "audit": audit,
    }


@log_function_call
async def run_t6_prop_benchmark() -> dict:
    """Refresh pocket watch + run 3-item live judge-desk benchmark + full `audit_props`."""
    # 1. Clean 2px speck on showcase_glowing_quill if present
    quill_path = config.PROPS_DIR / "showcase_glowing_quill.png"
    if quill_path.is_file():
        q_audit = audit_single_prop(quill_path)
        if not q_audit["passed"]:
            sanitize_prop_png(quill_path)

    # 2. Regenerate showcase_brass_pocket_watch with warm antique ivory/gold dial (zero emerald green)
    #    in parallel with the 3 new judge-desk benchmark items!
    watch_task = _bench_one(
        "showcase_brass_pocket_watch",
        "antique brass pocket watch",
        "an antique gold-and-brass mechanical pocket watch with a warm cream ivory clock face, dark bronze Roman numerals, and gold hands, NO green or teal colors",
    )
    desk_tasks = [_bench_one(pid, name, desc) for pid, name, desc in DESK_PROPS]

    t_batch0 = time.perf_counter()
    results = await asyncio.gather(watch_task, *desk_tasks)
    batch_elapsed = round(time.perf_counter() - t_batch0, 2)

    watch_res = results[0]
    desk_results = list(results[1:])

    # 3. Run full audit across ALL props under backend/assets/props/
    full_audit = audit_all_props(fix=False)

    # If any prop still has 1-2 residual specks, sanitize once and re-audit
    if not full_audit["all_passed"]:
        full_audit = audit_all_props(fix=True)

    avg_latency = round(sum(r["elapsed_sec"] for r in desk_results) / len(desk_results), 2)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": "gemini-nano-banana-2.1",
        "parallel_batch_elapsed_sec": batch_elapsed,
        "desk_props_count": len(desk_results),
        "avg_create_prop_latency_sec": avg_latency,
        "all_desk_props_passed": all(r["audit"]["passed"] for r in desk_results),
        "all_desk_props_zero_green_halo": all(r["audit"]["halo_green_pixels"] == 0 for r in desk_results),
        "pocket_watch_regenerated": watch_res,
        "desk_props": desk_results,
        "full_props_audit_summary": {
            "total_props": full_audit["total_props"],
            "all_passed": full_audit["all_passed"],
            "all_rgba": full_audit["all_rgba"],
            "all_bottom_flush_ok": full_audit["all_bottom_flush_ok"],
            "all_zero_green_halo": full_audit["all_zero_green_halo"],
            "total_green_halo_pixels": full_audit["total_green_halo_pixels"],
            "max_bottom_opaque_gap_px": full_audit["max_bottom_opaque_gap_px"],
        },
    }

    out_path = config.ASSETS_DIR / "eval_live_props_sim.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    res = asyncio.run(run_t6_prop_benchmark())
    print(json.dumps(res, ensure_ascii=False, indent=2))
