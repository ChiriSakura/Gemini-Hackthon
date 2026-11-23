"""Unit tests for improvised mode. Run: `.venv/bin/python backend/test_improv.py`. No model calls."""

import asyncio
import json
import random
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import config
from backend.director.improv import ActorIntent, arbitrate, describe_world, improvise_scene
from backend.director.world_rules import inventory
from backend.schemas import BranchState


def _opening():
    b = BranchState(**json.loads((config.ASSETS_DIR / "branches" / "branch_v7.json").read_text(encoding="utf-8")))
    scene = sorted(b.scenes, key=lambda s: s.scene_id)[0]
    for a in scene.actors:  # the canonical opening, whatever was dragged around locally
        a.grid = [0.0, 1.0] if a.id == "lia" else [2.0, 1.0]
    return scene


def _legal(prev, scene):
    ids_before, where = set(inventory(prev)), inventory(scene)
    assert set(where) == ids_before, f"objects appeared or vanished: {ids_before ^ set(where)}"
    listed = [p.id for p in scene.stage_props] + [a.holding_prop for a in scene.actors if a.holding_prop]
    assert len(listed) == len(set(listed)), f"duplicate object: {listed}"
    assert next(a for a in scene.actors if a.id == "lia").elevation == "floor"


def test_conflict_whoever_is_first_wins():
    prev = _opening()
    intents = [
        ActorIntent(actor_id="lia", verb="pick_up", target="crystal_vase", line="Lia takes the vase."),
        ActorIntent(actor_id="nyx", verb="knock_off", target="crystal_vase", line="Nyx bats the vase."),
    ]
    outcomes = set()
    for seed in range(12):
        scene, report = arbitrate(prev, intents, "A loud bang outside", random.Random(seed))
        _legal(prev, scene)
        where = inventory(scene)["crystal_vase"]
        refused = [i for i in report["intents"] if not i["applied"]]
        assert len(refused) == 1, "exactly one of the two wishes is impossible"
        outcomes.add(where)
        if where == "held:lia":
            assert refused[0]["actor"] == "nyx"
        else:
            assert where == "floor" and refused[0]["actor"] == "lia"
            assert next(p for p in scene.stage_props if p.id == "crystal_vase").state == "broken"
    assert outcomes == {"held:lia", "floor"}, "both endings must be reachable"


def test_illegal_wishes_are_refused_not_applied():
    prev = _opening()
    intents = [
        ActorIntent(actor_id="lia", verb="jump_on_table"),          # not something Lia can do
        ActorIntent(actor_id="nyx", verb="pick_up", target="crystal_vase"),  # a cat cannot carry it
    ]
    scene, report = arbitrate(prev, intents, "", random.Random(1))
    _legal(prev, scene)
    assert all(not i["applied"] for i in report["intents"])
    assert inventory(scene) == inventory(prev)


def test_same_situation_twice_gives_different_legal_endings():
    prev = _opening()
    endings = set()
    for seed in range(20):
        scene, report = asyncio.run(improvise_scene("A loud bang outside makes the lanterns flicker", [prev], seed=seed, use_model=False))
        _legal(prev, scene)
        assert report["intent_source"] == "persona_sampler"
        assert scene.scene_id == prev.scene_id + 1 and scene.narration.startswith("A loud bang")
        endings.add(describe_world(scene))
    assert len(endings) >= 3, f"only {len(endings)} different endings in 20 runs"


def test_a_chain_of_improvised_scenes_stays_legal():
    scenes = [_opening()]
    situations = ["Thunder rolls", "It is quiet again", "A knock at the door", "Soft music plays", "The lights go out"]
    for i, situation in enumerate(situations):
        scene, _ = asyncio.run(improvise_scene(situation, scenes, seed=100 + i, use_model=False))
        _legal(scenes[-1], scene)
        scenes.append(scene)
    broken_seen = False
    for scene in scenes:
        now = any(p.id == "crystal_vase" and p.state == "broken" for p in scene.stage_props)
        assert not (broken_seen and not now), "a broken vase mended itself"
        broken_seen = broken_seen or now


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
