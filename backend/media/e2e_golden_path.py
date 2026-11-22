"""
End-to-end headless golden-path verification script for StageGenesis (Stage 0).

Feature & Use Cases:
- Runs the full live backend pipeline without a browser via FastAPI ASGITransport:
  0. Verifies director timeout + 1x retry behavior and explicit HTTP 504 (never 500).
  1. Text direction -> extend Scene 2 -> render dual-cam clips -> assert both MP4s
     and tail frames exist on disk and item ledger counts are exact.
  2. Move Lia to the right side of the stage -> extend Scene 3 -> assert new grid
     position is preserved and item ledger counts remain exact.
  3. "Pick up the crystal vase" -> extend Scene 4 -> assert crystal_vase exists
     exactly once and is held in an actor's hand (not duplicated on the stage).
- Supports `--runs N` (e.g., `--runs 3`) to confirm consecutive reliability.
"""

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend import config
from backend.director import genai_client
from backend.main import app


def _asset_url_to_path(url: str) -> Path:
    """Resolve `/assets/...` URL to a local filesystem Path under `config.ASSETS_DIR`."""
    rel = url.removeprefix("/assets/").lstrip("/")
    return config.ASSETS_DIR / rel


def _collect_item_counts(scene: dict[str, Any]) -> tuple[Counter[str], list[str], list[str]]:
    """Return (total prop counts, held prop IDs, loose stage_props IDs) for a scene dict."""
    held = [a["holding_prop"] for a in scene.get("actors", []) if a.get("holding_prop")]
    loose = [p["id"] for p in scene.get("stage_props", []) if p.get("id")]
    return Counter(held + loose), held, loose


def _assert_inventory_ledger(prev_scene: dict[str, Any], next_scene: dict[str, Any], label: str) -> None:
    """Assert actors are unique (1 lia, 1 nyx) and every prior prop exists exactly once."""
    actor_ids = [a["id"] for a in next_scene.get("actors", [])]
    actor_counts = Counter(actor_ids)
    assert actor_counts.get("lia") == 1, f"[{label}] expected 1 lia, got {actor_counts}"
    assert actor_counts.get("nyx") == 1, f"[{label}] expected 1 nyx, got {actor_counts}"

    prev_counts, _, _ = _collect_item_counts(prev_scene)
    next_counts, held, loose = _collect_item_counts(next_scene)

    for pid, cnt in next_counts.items():
        assert cnt == 1, f"[{label}] prop '{pid}' count={cnt} (held={held}, stage_props={loose})"
    for pid in prev_counts:
        assert next_counts.get(pid) == 1, (
            f"[{label}] prior prop '{pid}' missing or duplicated (prev={dict(prev_counts)}, next={dict(next_counts)})"
        )


async def verify_director_timeout_and_retry() -> None:
    """Verify director timeout triggers 1 retry and returns HTTP 504 JSON (never 500) on exhaustion."""
    if not config.GEMINI_API_KEY or config.DEMO_MODE:
        print("[SKIP] timeout/retry live handler check skipped because GEMINI_API_KEY is unset or DEMO_MODE=true")
        return

    client = genai_client.get_genai_client()
    original_generate = client.aio.models.generate_content
    orig_timeout = config.DIRECTOR_TIMEOUT_SEC
    orig_retries = config.DIRECTOR_MAX_RETRIES

    attempts = 0

    async def _always_hang(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(2.0)
        raise AssertionError("should have timed out")

    try:
        config.DIRECTOR_TIMEOUT_SEC = 0.25
        config.DIRECTOR_MAX_RETRIES = 1
        client.aio.models.generate_content = _always_hang  # type: ignore[method-assign]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://e2e",
            timeout=15.0,
        ) as http:
            resp = await http.post(
                "/api/director/perceive",
                data={"target_scene_id": "2", "text": "Lia opens the window."},
            )
            assert resp.status_code == 504, f"Expected HTTP 504 on timeout exhaustion, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail", {})
            assert isinstance(detail, dict), f"Expected structured detail dict, got {body}"
            assert detail.get("code") == "director_timeout", f"Unexpected error code: {detail}"
            assert detail.get("retryable") is True, f"Expected retryable=True: {detail}"
            assert attempts == 2, f"Expected 2 attempts (initial + 1 retry), got {attempts}"
            print(f"[PASS] Director timeout+retry check: HTTP {resp.status_code}, attempts={attempts}, message='{detail.get('message')}'")
    finally:
        client.aio.models.generate_content = original_generate  # type: ignore[method-assign]
        config.DIRECTOR_TIMEOUT_SEC = orig_timeout
        config.DIRECTOR_MAX_RETRIES = orig_retries


async def run_single_golden_path(run_idx: int, *, keep_branch: bool = False) -> dict[str, Any]:
    """Execute one full golden-path run and return timing/verification summary."""
    t0 = time.perf_counter()
    created_branch_id: str | None = None
    created_media_files: list[Path] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://e2e",
        timeout=180.0,
    ) as http:
        try:
            # 0. Health check
            health_res = await http.get("/api/health")
            assert health_res.status_code == 200, f"Health failed: {health_res.status_code} {health_res.text}"
            health = health_res.json()
            assert health.get("live") is True, f"Backend is not in live mode: {health}"

            # 1. Text direction -> extend Scene 2 from branch_v1 Scene 1
            t_ext1 = time.perf_counter()
            ext1_res = await http.post(
                "/api/director/extend",
                data={
                    "branch_id": "branch_v1",
                    "from_scene_id": "1",
                    "text": "Lia takes a quiet step toward the glass window and looks out at the rain.",
                    "end_freeze_mode": "sculpture",
                },
            )
            assert ext1_res.status_code == 200, f"[Run {run_idx}] Step 1 extend failed ({ext1_res.status_code}): {ext1_res.text}"
            branch = ext1_res.json()
            created_branch_id = branch["branch_id"]
            assert created_branch_id != "branch_v1", "Extending protected branch_v1 must fork a new branch"
            scenes = sorted(branch["scenes"], key=lambda s: s["scene_id"])
            assert len(scenes) == 2 and scenes[-1]["scene_id"] == 2, f"Expected 2 scenes, got {[s['scene_id'] for s in scenes]}"
            scene1, scene2 = scenes[0], scenes[1]
            _assert_inventory_ledger(scene1, scene2, f"Run {run_idx} S2")
            ext1_sec = round(time.perf_counter() - t_ext1, 2)
            print(f"[Run {run_idx}] Step 1a extend S2 PASS ({ext1_sec}s, branch={created_branch_id})")

            # 2. Render Scene 2 -> check both cam A and cam B MP4s + tail frames exist
            t_ren = time.perf_counter()
            render_res = await http.post(
                "/api/media/render-scene",
                data={
                    "branch_id": created_branch_id,
                    "scene_id": "2",
                    "fast_live_mode": "true",
                },
            )
            assert render_res.status_code == 200, f"[Run {run_idx}] Step 1 render failed ({render_res.status_code}): {render_res.text}"
            render_body = render_res.json()
            media = render_body.get("media") or {}
            assert not media.get("fallback_to_preview"), f"[Run {run_idx}] Render fell back to preview: {media}"
            cam_a_url = media.get("cam_a_final_url") or ""
            cam_b_url = media.get("cam_b_final_url") or ""
            tail_a_url = media.get("cam_a_tail_frame_url") or ""
            tail_b_url = media.get("cam_b_tail_frame_url") or ""
            assert cam_a_url and cam_b_url, f"[Run {run_idx}] Missing final video URLs: {media}"
            for url_label, url_val, min_bytes in [
                ("cam_a_final", cam_a_url, 50_000),
                ("cam_b_final", cam_b_url, 50_000),
                ("cam_a_tail", tail_a_url, 5_000),
                ("cam_b_tail", tail_b_url, 5_000),
            ]:
                assert url_val, f"[Run {run_idx}] Missing {url_label} URL"
                fpath = _asset_url_to_path(url_val)
                assert fpath.is_file(), f"[Run {run_idx}] {url_label} missing on disk: {fpath}"
                fsize = fpath.stat().st_size
                assert fsize >= min_bytes, f"[Run {run_idx}] {url_label} too small ({fsize} bytes): {fpath}"
                created_media_files.append(fpath)
            ren_sec = round(time.perf_counter() - t_ren, 2)
            print(f"[Run {run_idx}] Step 1b render S2 PASS ({ren_sec}s, cam_a={cam_a_url}, cam_b={cam_b_url})")

            # 3. Move Lia to the right side -> extend Scene 3 -> verify new spot
            move_res = await http.post(
                "/api/director/move-actor",
                data={
                    "branch_id": created_branch_id,
                    "scene_id": "2",
                    "actor_id": "lia",
                    "grid_x": "1.75",
                    "grid_y": "1.10",
                },
            )
            assert move_res.status_code == 200, f"[Run {run_idx}] Step 2 move-actor failed ({move_res.status_code}): {move_res.text}"
            moved_grid = move_res.json()["grid"]
            assert moved_grid[0] > 1.3, f"[Run {run_idx}] Expected moved Lia grid_x > 1.3, got {moved_grid}"

            t_ext2 = time.perf_counter()
            ext2_res = await http.post(
                "/api/director/extend",
                data={
                    "branch_id": created_branch_id,
                    "from_scene_id": "2",
                    "text": "Lia stays right where she is and waves gently to Nyx.",
                    "end_freeze_mode": "sculpture",
                },
            )
            assert ext2_res.status_code == 200, f"[Run {run_idx}] Step 2 extend S3 failed ({ext2_res.status_code}): {ext2_res.text}"
            branch2 = ext2_res.json()
            scenes2 = sorted(branch2["scenes"], key=lambda s: s["scene_id"])
            assert len(scenes2) == 3 and scenes2[-1]["scene_id"] == 3, f"Expected 3 scenes, got {[s['scene_id'] for s in scenes2]}"
            scene2_after, scene3 = scenes2[1], scenes2[2]
            _assert_inventory_ledger(scene2_after, scene3, f"Run {run_idx} S3")
            lia_s3 = next(a for a in scene3["actors"] if a["id"] == "lia")
            assert lia_s3["grid"][0] > 1.25, (
                f"[Run {run_idx}] Step 2 failed: Lia did not stay at moved right-side spot (grid={lia_s3['grid']})"
            )
            ext2_sec = round(time.perf_counter() - t_ext2, 2)
            print(f"[Run {run_idx}] Step 2 move+extend S3 PASS ({ext2_sec}s, lia.grid={lia_s3['grid']})")

            # 4. "Pick up the crystal vase" -> extend Scene 4 -> assert crystal_vase exists once and is held
            t_ext3 = time.perf_counter()
            ext3_res = await http.post(
                "/api/director/extend",
                data={
                    "branch_id": created_branch_id,
                    "from_scene_id": "3",
                    "text": "Lia walks to the table and picks up the crystal vase with both hands.",
                    "end_freeze_mode": "sculpture",
                },
            )
            assert ext3_res.status_code == 200, f"[Run {run_idx}] Step 3 extend S4 failed ({ext3_res.status_code}): {ext3_res.text}"
            branch3 = ext3_res.json()
            scenes3 = sorted(branch3["scenes"], key=lambda s: s["scene_id"])
            assert len(scenes3) == 4 and scenes3[-1]["scene_id"] == 4, f"Expected 4 scenes, got {[s['scene_id'] for s in scenes3]}"
            scene3_after, scene4 = scenes3[2], scenes3[3]
            _assert_inventory_ledger(scene3_after, scene4, f"Run {run_idx} S4")
            counts_s4, held_s4, loose_s4 = _collect_item_counts(scene4)
            assert counts_s4.get("crystal_vase") == 1, f"[Run {run_idx}] crystal_vase total count != 1: {dict(counts_s4)}"
            assert "crystal_vase" in held_s4, (
                f"[Run {run_idx}] Step 3 failed: crystal_vase not held by any actor (held={held_s4}, stage_props={loose_s4})"
            )
            assert "crystal_vase" not in loose_s4, (
                f"[Run {run_idx}] Step 3 failed: crystal_vase duplicated in stage_props (held={held_s4}, stage_props={loose_s4})"
            )
            ext3_sec = round(time.perf_counter() - t_ext3, 2)
            total_sec = round(time.perf_counter() - t0, 2)
            print(
                f"[Run {run_idx}] Step 3 pick-up-vase S4 PASS ({ext3_sec}s, held={held_s4}, loose={loose_s4}, total={total_sec}s)"
            )

            return {
                "run": run_idx,
                "passed": True,
                "branch_id": created_branch_id,
                "timings_sec": {
                    "extend_s2": ext1_sec,
                    "render_s2": ren_sec,
                    "extend_s3_moved": ext2_sec,
                    "extend_s4_vase": ext3_sec,
                    "total": total_sec,
                },
                "lia_s3_grid": lia_s3["grid"],
                "s4_held": held_s4,
                "s4_stage_props": loose_s4,
            }
        finally:
            if created_branch_id and not keep_branch:
                try:
                    await http.delete(f"/api/branches/{created_branch_id}")
                except Exception:
                    pass
                for fpath in created_media_files:
                    try:
                        if fpath.is_file():
                            fpath.unlink()
                    except Exception:
                        pass


async def _main() -> int:
    parser = argparse.ArgumentParser(description="StageGenesis headless E2E golden-path runner")
    parser.add_argument("--runs", type=int, default=1, help="Number of consecutive golden-path runs (default: 1)")
    parser.add_argument("--keep-branch", action="store_true", help="Keep temporary test branches and clips on disk")
    parser.add_argument("--skip-timeout-check", action="store_true", help="Skip the synthetic 504 timeout+retry check")
    args = parser.parse_args()

    if not args.skip_timeout_check:
        await verify_director_timeout_and_retry()

    results = []
    for i in range(1, max(1, args.runs) + 1):
        res = await run_single_golden_path(i, keep_branch=args.keep_branch)
        results.append(res)

    print("\n=== GOLDEN PATH SUMMARY ===")
    print(json.dumps({"runs_passed": len(results), "runs_requested": args.runs, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
