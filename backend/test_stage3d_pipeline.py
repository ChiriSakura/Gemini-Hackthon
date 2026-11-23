"""
Offline plumbing test for the 3D-first render bases.

The model calls are replaced by stand-ins (the video "generation" returns the 3D blocking clip, the judge returns a
fixed pass), so this checks everything around them without a network or an API key:
the stage frames are composited from the world state, the right inputs reach the video model for each render base,
the split clip is cut into one clip per camera, the tail frames are saved, and the media record is filled in.

It does NOT say anything about how good the generated video is. Run: `.venv/bin/python backend/test_stage3d_pipeline.py`
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import config
from backend.director.world_rules import apply_world_rules
from backend.media import stage3d
from backend.media import video_renderer as vr
from backend.schemas import BranchState, JudgeItem, JudgeResult, SceneMedia

SOURCE_BRANCH = config.ASSETS_DIR / "branches" / "branch_v7.json"


def _load_states():
    branch = BranchState(**json.loads(SOURCE_BRANCH.read_text(encoding="utf-8")))
    scenes = sorted(branch.scenes, key=lambda s: s.scene_id)
    for prev, nxt in zip(scenes, scenes[1:]):
        apply_world_rules(prev, nxt)
    heights = {pid: float(p.height_m) for pid, p in branch.props_registry.items() if p.height_m}
    return branch, scenes, heights


async def _run_base(render_base: str) -> dict:
    branch, scenes, heights = _load_states()
    prev, scene = scenes[4], scenes[5]  # scene 5 -> 6: the cat jumps onto the table
    scene.media = SceneMedia()
    seen: dict = {}
    made: list[Path] = []

    def fake_generate(model_id, input_parts, duration_sec, resolution, aspect_ratio, out_video_path):
        seen["parts"] = [p["type"] for p in input_parts]
        seen["prompt"] = next(p["text"] for p in input_parts if p["type"] == "text")
        seen["resolution"] = resolution
        clip = stage3d.render_blocking_clip(
            prev, scene, Path(out_video_path), seconds=2.0, fps=8, prop_heights=heights, ffmpeg_exe=vr._resolve_ffmpeg_exe()
        )
        made.append(clip)
        return {"interaction_id": "offline", "bytes": clip.stat().st_size, "saved_path": str(clip)}

    async def fake_upload(path, use_cache=True):
        return (f"offline://{Path(path).name}", "video/mp4" if str(path).endswith(".mp4") else "image/png")

    async def fake_judge(questions, context_text="", media_paths=None):
        return JudgeResult(
            overall_score=1.0, all_passed=True,
            items=[JudgeItem(question=q, passed=True, confidence=1.0, reason="offline stand-in") for q in questions],
        )

    saved = (vr._generate_omni_take_sync, vr._get_or_upload_uri, vr.judge, config.RENDER_BASE, config.GEMINI_API_KEY, config.DEMO_MODE)
    vr._generate_omni_take_sync, vr._get_or_upload_uri, vr.judge = fake_generate, fake_upload, fake_judge
    config.RENDER_BASE, config.GEMINI_API_KEY, config.DEMO_MODE = render_base, "offline-test", False
    try:
        media = await vr.render_scene_media(
            scene, [], prev_tail_a=prev.media.cam_a_tail_frame_url, prev_tail_b=prev.media.cam_b_tail_frame_url,
            fast_live_mode=True, start_state=prev, prop_heights=heights,
        )
    finally:
        vr._generate_omni_take_sync, vr._get_or_upload_uri, vr.judge = saved[0], saved[1], saved[2]
        config.RENDER_BASE, config.GEMINI_API_KEY, config.DEMO_MODE = saved[3], saved[4], saved[5]

    files = [vr._resolve_asset_to_local_path(u) for u in (media.cam_a_final_url, media.cam_b_final_url, media.cam_a_tail_frame_url, media.cam_b_tail_frame_url)]
    result = {"media": media, "seen": seen, "files": files}
    assert all(f and f.is_file() and f.stat().st_size > 2000 for f in files), f"{render_base}: missing output {files}"
    assert media.fallback_to_preview is False
    for f in files + made:  # leave no test output in the asset folders
        if f:
            f.unlink(missing_ok=True)
    for extra in list(config.TAILS_DIR.glob(f"scene_{scene.scene_id}_3d_*")) + list(config.TAILS_DIR.glob(f"scene_{scene.scene_id}_sync_*")) + list(config.PREVIEWS_DIR.glob(f"scene_{scene.scene_id}_blocking_*")):
        extra.unlink(missing_ok=True)
    return result


def test_prev_tail_base_unchanged():
    r = asyncio.run(_run_base("prev_tail"))
    assert r["seen"]["parts"] == ["image", "text"], r["seen"]["parts"]
    assert "laid out from a 3D stage" not in r["seen"]["prompt"]


def test_stage3d_start_frame():
    r = asyncio.run(_run_base("stage3d"))
    assert r["seen"]["parts"] == ["image", "text"], r["seen"]["parts"]
    assert "laid out from a 3D stage" in r["seen"]["prompt"]
    assert "exact LAST frame" not in r["seen"]["prompt"]


def test_stage3d_first_last():
    r = asyncio.run(_run_base("stage3d_first_last"))
    assert r["seen"]["parts"] == ["image", "image", "text"], r["seen"]["parts"]
    assert "exact LAST frame" in r["seen"]["prompt"]


def test_stage3d_video():
    r = asyncio.run(_run_base("stage3d_video"))
    assert r["seen"]["parts"] == ["image", "video", "text"], r["seen"]["parts"]
    assert "blocking pass" in r["seen"]["prompt"]


def test_no_equipment_or_numbers_in_prompt():
    r = asyncio.run(_run_base("stage3d_first_last"))
    prompt = r["seen"]["prompt"].lower()
    start = prompt.split("no camera, tripod")[0]
    for word in ("tripod", "lens", " 1.62", " 0.75"):
        assert word not in start, f"'{word}' would be drawn into the picture"


def test_blocking_preview_two_clips():
    branch, scenes, heights = _load_states()
    urls = vr.build_blocking_preview(scenes[5], scenes[4], heights)
    assert urls and len(urls) == 2
    paths = [vr._resolve_asset_to_local_path(u) for u in urls]
    assert all(p and p.is_file() and p.stat().st_size > 2000 for p in paths)
    for p in paths:
        p.unlink(missing_ok=True)


def test_one_of_everything_in_composite():
    """The compositor never draws a held object a second time on the table."""
    branch, scenes, heights = _load_states()
    held_scene = scenes[1]  # scene 2: Lia holds the vase
    assert next(a for a in held_scene.actors if a.id == "lia").holding_prop == "crystal_vase"
    held_scene.stage_props.append(held_scene.stage_props[0].model_copy(update={"id": "crystal_vase", "grid": [1.0, 1.0], "on_floor": False}))
    with_dup = stage3d.compose_stage_image(held_scene, "cam_a", 640, 360, heights)
    held_scene.stage_props.pop()
    without = stage3d.compose_stage_image(held_scene, "cam_a", 640, 360, heights)
    assert list(with_dup.getdata()) == list(without.getdata())


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {str(exc)[:300]}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
