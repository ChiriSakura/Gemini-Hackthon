"""
Offline tests for the world-consistency benchmark. Run: `.venv/bin/python backend/test_bench.py`.

No model and no network: the clips come from the 3D stage, and the judge is a stand-in that passes or fails by
a fixed rule. The stand-in exists only to exercise the harness; its numbers are not results and are never saved
into the asset folders.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.bench.adapters import Blocking3DAdapter
from backend.bench.questions import CATEGORIES, single_view_questions
from backend.bench.run import draw_curve, run_benchmark, summarize
from backend.bench.scenario import EVENT_KINDS, answer_key, generate_scenario
from backend.director.world_rules import inventory


def test_scenarios_are_reproducible_and_legal():
    a, b = generate_scenario(8, 4, seed=5), generate_scenario(8, 4, seed=5)
    assert [s.event for s in a.steps] == [s.event for s in b.steps]
    assert [s.event for s in a.steps] != [s.event for s in generate_scenario(8, 4, seed=6).steps]
    for seed in range(25):
        sc = generate_scenario(8, 4, seed)
        ids = {p.id for p in sc.scenes[0].stage_props}
        broken: set[str] = set()
        for scene in sc.scenes:
            where = inventory(scene)
            assert set(where) == ids, f"seed {seed} scene {scene.scene_id}: objects appeared or vanished"
            listed = [p.id for p in scene.stage_props] + [x.holding_prop for x in scene.actors if x.holding_prop]
            assert len(listed) == len(set(listed)), "an object exists twice"
            now_broken = {p.id for p in scene.stage_props if p.state == "broken"}
            assert broken <= now_broken, "a broken object mended itself"
            broken = now_broken


def test_every_event_kind_occurs():
    seen = {s.event for seed in range(40) for s in generate_scenario(8, 3, seed).steps}
    assert set(EVENT_KINDS) <= seen, set(EVENT_KINDS) - seen


def test_questions_follow_the_state():
    sc = generate_scenario(8, 3, seed=1)
    for scene in sc.scenes[1:]:
        key = answer_key(scene)
        qs = single_view_questions(scene)
        assert {q["category"] for q in qs} <= set(CATEGORIES)
        for pid, where in key["where"].items():
            place_q = next(q for q in qs if q["category"] == "place" and q["subject"] == pid)["question"]
            expected = "hands" if where.startswith("held:") else ("on top of the round table" if where == "table" else "on the floor (")
            assert expected in place_q, (where, place_q)
        cat_q = next(q for q in qs if q["subject"] == "nyx")["question"]
        assert ("on top of the round table" in cat_q) == key["cat_on_table"]


def test_harness_end_to_end_with_stand_in_judge():
    calls = {"n": 0}

    async def stand_in_judge(questions, frames):
        # Fixed rule so the aggregation can be checked: everything passes except cross-camera questions.
        calls["n"] += len(questions)
        assert all(Path(f).is_file() for f in frames)
        return [(not q.startswith("These two pictures"), 0.9) for q in questions]

    with tempfile.TemporaryDirectory() as tmp:
        chart = Path(tmp) / "curve.png"
        report = asyncio.run(run_benchmark([3], [2], 1, [Blocking3DAdapter()], stand_in_judge,
                                           report_path=None, chart_path=chart, judge_name="stand_in"))
        assert chart.is_file() and chart.stat().st_size > 1000
    summary = report["summary"]["stage3d_blocking"]
    assert summary["by_category"]["cross_camera"] == 0.0
    assert summary["by_category"]["place"] == 1.0 and summary["by_category"]["inter_scene"] == 1.0
    assert set(summary["by_scene"]) == {"2", "3"}
    assert calls["n"] == summary["questions"]
    assert report["judge"] == "stand_in"


def test_summarize_shows_a_collapse():
    def run(adapter, fail_from):
        recs = [{"scene": s, "category": "place", "passed": s < fail_from} for s in range(2, 9) for _ in range(4)]
        return {"adapter": adapter, "n_objects": 2, "records": recs}

    summary = summarize([run("keeps_world", 99), run("loses_world", 5)])
    assert summary["keeps_world"]["by_scene"]["8"] == 1.0
    assert summary["loses_world"]["by_scene"]["4"] == 1.0 and summary["loses_world"]["by_scene"]["5"] == 0.0
    with tempfile.TemporaryDirectory() as tmp:
        assert draw_curve(summary, Path(tmp) / "c.png").stat().st_size > 1000


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
