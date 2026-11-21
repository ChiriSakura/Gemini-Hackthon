"""
W1-T6I: Concurrent Burst Stress Test & Media Engine Hardening Verification (Owned by P2).

Feature & Use Cases:
- Verifies all hardened P2 concurrency, caching, reference-selection, and retry-guard behaviors:
  1. Concurrent `asyncio.gather` burst of `create_prop` + `render_scene_media` on identical
     `scene_id`s verifying ZERO temp-file collisions, ZERO deadlocks, and unique canonical outputs.
  2. Scene-aware `_select_scene_ref_images()` verification: guarantees live custom props active
     in a scene ALWAYS win a reference slot while unused registry props are excluded (preventing
     hallucinated props and 5-image budget overflow).
  3. Retry regression guard verification: proves a lower-scoring or failed retry attempt NEVER
     overwrites a higher-scoring earlier attempt.
  4. Upload cache `(path, mtime_ns, size)` invalidation verification: proves modifying a prop PNG
     in place invalidates its Files API cache key immediately.
- Exports metrics and test results to `backend/assets/eval_burst_stress.json`.
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import config
from backend.director.state_tree import get_branch
from backend.media.prop_maker import create_prop
from backend.media.video_renderer import (
    _select_scene_ref_images,
    _upload_cache_key,
    render_scene_media,
    render_single_camera_shot,
)
from backend.schemas import JudgeItem, JudgeResult


async def _verify_scene_ref_pruning() -> dict:
    """Verify `_select_scene_ref_images` keeps active actors & live props while pruning unused props."""
    b2 = get_branch("branch_v2_cached")
    assert b2 is not None
    scene4 = b2.scenes[3].model_copy(deep=True)
    # Scene 4 has lia (holding star_token), nyx, crystal_vase (intact), NO red_umbrella, NO pocket watch.
    # Add a live custom desk prop (`showcase_desk_coffee_mug`) to stage_props!
    from backend.schemas import StagePropState

    scene4.stage_props.append(
        StagePropState(id="showcase_desk_coffee_mug", grid=[1.0, 1.0], state="intact", on_floor=False)
    )
    bloated_refs = [
        str(config.SHARED_ASSETS_DIR / "lia.png"),
        str(config.SHARED_ASSETS_DIR / "nyx.png"),
        str(config.SHARED_ASSETS_DIR / "crystal_vase.png"),
        "/assets/props/showcase_brass_pocket_watch.png",  # UNUSED in scene4
        "/assets/props/showcase_origami_crane.png",  # UNUSED in scene4
        "/assets/props/showcase_star_lantern.png",  # UNUSED in scene4
        "/assets/props/star_token.png",  # ACTIVE (held by Lia)
        "/assets/props/showcase_desk_coffee_mug.png",  # ACTIVE (live custom prop!)
    ]
    selected = _select_scene_ref_images(scene4, bloated_refs, max_refs=5)
    names = [p.name for p in selected]

    assert "lia_clean_ref.png" in names, f"Expected lia_clean_ref.png in {names}"
    assert "nyx.png" in names, f"Expected nyx.png in {names}"
    assert "crystal_vase.png" in names, f"Expected crystal_vase.png in {names}"
    assert "star_token.png" in names, f"Expected star_token.png in {names}"
    assert "showcase_desk_coffee_mug.png" in names, f"Live custom prop dropped! {names}"
    assert "showcase_brass_pocket_watch.png" not in names, f"Unused prop leaked! {names}"
    assert "showcase_origami_crane.png" not in names, f"Unused prop leaked! {names}"
    assert len(selected) == 5
    return {"passed": True, "selected_refs": names, "pruned_unused_count": 3}


async def _verify_retry_regression_guard() -> dict:
    """Verify retry attempt scoring lower than attempt 0 NEVER overwrites attempt 0's winning clip."""
    b1 = get_branch("branch_v1")
    assert b1 is not None
    scene1 = b1.scenes[0]

    tmp_dir = Path(tempfile.mkdtemp(prefix="stagestate_guard_"))
    v0 = tmp_dir / "attempt0.mp4"
    t0 = tmp_dir / "attempt0_tail.png"
    v1 = tmp_dir / "attempt1.mp4"
    t1 = tmp_dir / "attempt1_tail.png"
    v0.write_bytes(b"ATTEMPT_0_BETTER_MP4")
    t0.write_bytes(b"ATTEMPT_0_TAIL")
    v1.write_bytes(b"ATTEMPT_1_WORSE_MP4")
    t1.write_bytes(b"ATTEMPT_1_TAIL")

    qa_att0 = JudgeResult(
        overall_score=0.72,  # Below 0.75 threshold -> triggers retry!
        all_passed=False,
        items=[JudgeItem(question="q", passed=True, confidence=0.72, reason="att0")],
    )
    qa_att1 = JudgeResult(
        overall_score=0.45,  # Regression on retry!
        all_passed=False,
        items=[JudgeItem(question="q", passed=False, confidence=0.55, reason="att1")],
    )

    async def fake_take(**kwargs):
        if kwargs["attempt"] == 0:
            return (v0, t0, qa_att0, 0.74)
        return (v1, t1, qa_att1, 0.48)

    prev_demo = config.DEMO_MODE
    prev_key = config.GEMINI_API_KEY
    out_mp4: Path | None = None
    out_tail: Path | None = None
    try:
        config.DEMO_MODE = False
        config.GEMINI_API_KEY = "test-key"
        with (
            patch("backend.media.video_renderer._run_candidate_take", side_effect=fake_take),
            patch("backend.media.video_renderer._get_or_upload_uri", new=AsyncMock(return_value=("uri://mock", "image/png"))),
            patch("backend.media.video_renderer.write_repair_constraint", new=AsyncMock(return_value="keep table visible")),
            patch("backend.media.video_renderer._append_eval_media_stat"),
        ):
            final_url, tail_url, final_qa, _, retries = await render_single_camera_shot(
                scene=scene1,
                cam_id="cam_a",
                prev_tail_path=None,
                reference_images=[str(config.SHARED_ASSETS_DIR / "lia.png")],
                overwrite_canonical=False,
            )
        assert retries == config.MAX_QA_RETRIES, f"Expected {config.MAX_QA_RETRIES} retries, got {retries}"
        assert final_qa.overall_score == 0.72, f"Retry regressed score to {final_qa.overall_score}!"
        out_mp4 = config.ASSETS_DIR / final_url.removeprefix("/assets/")
        out_tail = config.ASSETS_DIR / tail_url.removeprefix("/assets/") if tail_url else None
        assert out_mp4.read_bytes() == b"ATTEMPT_0_BETTER_MP4", "Retry overwrote better attempt 0 video!"
        return {
            "passed": True,
            "attempt_0_score": 0.72,
            "attempt_1_score": 0.45,
            "preserved_score": final_qa.overall_score,
            "retries": retries,
        }
    finally:
        config.DEMO_MODE = prev_demo
        config.GEMINI_API_KEY = prev_key
        if out_mp4:
            out_mp4.unlink(missing_ok=True)
        if out_tail:
            out_tail.unlink(missing_ok=True)
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _verify_concurrent_burst() -> dict:
    """Run 6 parallel `create_prop` + `render_scene_media` jobs simultaneously (zero temp collision)."""
    b1 = get_branch("branch_v1")
    assert b1 is not None
    scene2 = b1.scenes[1]

    prev_demo = config.DEMO_MODE
    created_props: list[Path] = []
    t0 = time.perf_counter()
    try:
        config.DEMO_MODE = True
        prop_jobs = [
            create_prop(name=f"burst_item_{i}", description="rapid booth stress prop")
            for i in range(3)
        ]
        render_jobs = [
            render_scene_media(
                scene=scene2,
                reference_images=[str(config.SHARED_ASSETS_DIR / "lia.png")],
                overwrite_canonical=False,
            )
            for _ in range(3)
        ]
        results = await asyncio.gather(*prop_jobs, *render_jobs)
        elapsed = round(time.perf_counter() - t0, 3)

        prop_ids = []
        for pid, item in results[:3]:
            prop_ids.append(pid)
            p = config.PROPS_DIR / f"{pid}.png"
            assert p.is_file(), f"Burst prop missing: {p}"
            created_props.append(p)

        cam_a_urls = [m.cam_a_final_url for m in results[3:]]
        cam_b_urls = [m.cam_b_final_url for m in results[3:]]
        assert len(set(prop_ids)) == 3, f"Prop ID collision: {prop_ids}"
        assert len(set(cam_a_urls)) == 3, f"Concurrent cam_a output collision: {cam_a_urls}"
        assert len(set(cam_b_urls)) == 3, f"Concurrent cam_b output collision: {cam_b_urls}"

        # Also verify upload cache key invalidates on file modification
        probe = config.PROPS_DIR / f"{prop_ids[0]}.png"
        k1 = _upload_cache_key(probe)
        probe.write_bytes(probe.read_bytes() + b"\x00")
        k2 = _upload_cache_key(probe)
        assert k1 != k2, "Upload cache key failed to invalidate after file modification!"

        return {
            "passed": True,
            "concurrent_jobs": 6,
            "elapsed_sec": elapsed,
            "unique_prop_ids": len(set(prop_ids)),
            "unique_cam_a_outputs": len(set(cam_a_urls)),
            "unique_cam_b_outputs": len(set(cam_b_urls)),
            "upload_cache_mtime_invalidation_ok": True,
            "zero_deadlocks_or_collisions": True,
        }
    finally:
        config.DEMO_MODE = prev_demo
        for p in created_props:
            p.unlink(missing_ok=True)


async def run_burst_stress_suite() -> dict:
    """Execute all W1-T6I concurrency, ref-pruning, cache-invalidation & retry-guard checks."""
    ref_res = await _verify_scene_ref_pruning()
    guard_res = await _verify_retry_regression_guard()
    burst_res = await _verify_concurrent_burst()

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "all_passed": ref_res["passed"] and guard_res["passed"] and burst_res["passed"],
        "scene_ref_pruning": ref_res,
        "retry_regression_guard": guard_res,
        "concurrent_burst_stress": burst_res,
    }
    out_path = config.ASSETS_DIR / "eval_burst_stress.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("W1-T6I BURST & ENGINE HARDENING PASSED:", json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    asyncio.run(run_burst_stress_suite())
