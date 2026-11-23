"""Unit tests for timeline diff and graft. Run: `.venv/bin/python backend/test_timeline.py`. No model calls."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import config
from backend.director.timeline import build_bridge_scene, diff_branches, diff_scenes, graft
from backend.director.world_rules import inventory
from backend.schemas import BranchState


def _load(branch_id: str) -> BranchState:
    return BranchState(**json.loads((config.ASSETS_DIR / "branches" / f"{branch_id}.json").read_text(encoding="utf-8")))


def test_identical_scenes_have_no_differences():
    b = _load("branch_v1")
    assert diff_scenes(b.scenes[0], b.scenes[0]) == []


def test_diff_names_what_changed():
    b = _load("branch_v7")
    scenes = {s.scene_id: s for s in b.scenes}
    kinds = {(d["kind"], d["subject"]) for d in diff_scenes(scenes[1], scenes[2])}
    assert ("holding", "lia") in kinds  # umbrella in scene 1, vase in scene 2
    texts = " ".join(d["text"] for d in diff_scenes(scenes[1], scenes[2]))
    assert "crystal vase" in texts and "red umbrella" in texts
    moved = diff_scenes(scenes[2], scenes[3])
    assert any(d["kind"] == "position" and d["subject"] == "lia" for d in moved)


def test_branch_diff_finds_the_fork():
    base, fork = _load("branch_v1"), _load("branch_v2_sleep_head_cached")
    report = diff_branches(base, fork)
    assert report["scenes"][0]["same_take"] is True, "scene 1 is the shared opening"
    assert report["diverged_at"] == 2
    assert report["a"]["branch_id"] == "branch_v1"


def test_direct_graft_when_worlds_match():
    base, donor = _load("branch_v1"), _load("branch_v2_sleep_head_cached")
    base.scenes[0].actors = [a.model_copy(deep=True) for a in donor.scenes[0].actors]  # same world at the join
    out, report = graft(base, 1, donor, 2, "branch_test_graft")
    assert report["mode"] == "direct" and report["kept_final_clips"] is True, report
    assert [s.scene_id for s in out.scenes] == list(range(1, len(donor.scenes) + 1))
    assert out.scenes[1].media.cam_a_final_url == donor.scenes[1].media.cam_a_final_url


def test_dragged_character_at_the_join_needs_a_bridge():
    """Same opening take, but a character was dragged elsewhere afterwards: the worlds no longer match."""
    base, donor = _load("branch_v1"), _load("branch_v2_sleep_head_cached")
    base.scenes[0].actors = [a.model_copy(deep=True) for a in donor.scenes[0].actors]
    next(a for a in base.scenes[0].actors if a.id == "lia").grid = [1.8, 1.9]
    out, report = graft(base, 1, donor, 2, "branch_test_graft3")
    assert report["mode"] == "bridge" and report["kept_final_clips"] is False
    assert "walks over" in report["bridge_narration"]


def test_bridge_graft_when_worlds_differ():
    base, donor = _load("branch_v7"), _load("branch_v1")
    # after branch_v7 scene 4 (Lia right of the table with the iced tea), attach the baseline's scene 3 onwards
    out, report = graft(base, 4, donor, 3, "branch_test_graft2")
    assert report["mode"] == "bridge", report
    bridge = out.scenes[4]
    assert bridge.title == "Bridge" and bridge.scene_id == 5 and bridge.media.cam_a_final_url is None
    assert len(bridge.narration) > 10
    assert [s.scene_id for s in out.scenes] == list(range(1, len(out.scenes) + 1))
    # one of everything survives the graft: the iced tea of the base timeline is still somewhere at the end
    tea = next(pid for pid in base.props_registry if pid.startswith("iced_tea"))
    for scene in out.scenes[4:]:
        where = inventory(scene)
        assert tea in where, f"scene {scene.scene_id} lost the {tea}"
        ids = [p.id for p in scene.stage_props] + [a.holding_prop for a in scene.actors if a.holding_prop]
        assert len(ids) == len(set(ids)), f"scene {scene.scene_id} has a duplicate object: {ids}"
    assert all(s.media.cam_a_final_url is None for s in out.scenes[4:]), "re-filmed from a new start"
    assert tea in out.props_registry


def test_bridge_reaches_the_donor_state():
    base, donor = _load("branch_v7"), _load("branch_v1")
    base_end = next(s for s in base.scenes if s.scene_id == 4)
    donor_start = next(s for s in donor.scenes if s.scene_id == 2)
    bridge = build_bridge_scene(base_end, donor_start, 5)
    left = [d for d in diff_scenes(bridge, donor_start) if d["kind"] in ("position", "holding", "elevation")]
    assert left == [], left


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {str(exc)[:400]}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
