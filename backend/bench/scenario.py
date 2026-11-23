"""
Scenario generator for the world-consistency benchmark.

A scenario is a seeded chain of world states. Each step applies one event (pick up, put down, break, move,
cat onto / off the table) to the previous state. Because the states are generated, the answer to every question
about every scene is known exactly: the world state is the answer key.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from backend.director.world_rules import apply_world_rules, inventory, keep_actors_clear_of_table
from backend.schemas import ActorState, Scene, StagePropState

# Objects the 3D stage already has cut-outs for: id -> (plain name, height in metres)
OBJECT_POOL: list[tuple[str, str, float]] = [
    ("crystal_vase", "crystal vase with a blue rose", 0.38),
    ("showcase_desk_coffee_mug", "white coffee mug", 0.12),
    ("showcase_star_lantern", "star lantern", 0.3),
    ("showcase_origami_crane", "paper crane", 0.14),
    ("showcase_brass_pocket_watch", "brass pocket watch", 0.1),
    ("showcase_everyday_soda_can", "soda can", 0.13),
    ("showcase_desk_brass_key", "brass key", 0.08),
    ("star_token", "glowing star", 0.2),
]
OBJECT_NAMES = {pid: name for pid, name, _ in OBJECT_POOL}
OBJECT_HEIGHTS = {pid: h for pid, _, h in OBJECT_POOL}

TABLE_SLOTS = [[1.0, 1.0], [1.12, 1.03], [0.88, 1.03], [1.06, 0.9], [0.94, 0.9]]
FLOOR_SLOTS = [[0.35, 1.75], [1.65, 1.75], [0.3, 0.4], [1.7, 0.4], [1.0, 1.9]]
LIA_SPOTS = {"left": [0.0, 1.0], "right": [2.0, 1.2], "front left": [0.4, 1.8], "front right": [1.6, 1.8]}
NYX_SPOTS = {"right": [2.0, 1.0], "left": [0.1, 1.5], "front": [1.0, 1.95]}

EVENT_KINDS = ("pick_up", "put_down", "break", "move_lia", "move_cat", "cat_up", "cat_down")


@dataclass
class Step:
    index: int  # 1-based scene number
    event: str  # event kind, or "opening"
    detail: dict
    scene: Scene


@dataclass
class Scenario:
    seed: int
    n_objects: int
    steps: list[Step] = field(default_factory=list)

    @property
    def scenes(self) -> list[Scene]:
        return [s.scene for s in self.steps]

    @property
    def prop_heights(self) -> dict[str, float]:
        return dict(OBJECT_HEIGHTS)


def _opening(object_ids: list[str]) -> Scene:
    props = []
    for i, pid in enumerate(object_ids):
        if i < 3:
            props.append(StagePropState(id=pid, grid=list(TABLE_SLOTS[i]), on_floor=False))
        else:
            props.append(StagePropState(id=pid, grid=list(FLOOR_SLOTS[(i - 3) % len(FLOOR_SLOTS)]), on_floor=True))
    names = ", ".join(f"a {OBJECT_NAMES[p]}" for p in object_ids)
    return Scene(
        scene_id=1,
        title="Opening",
        narration=f"Lia stands on the left and the black cat Nyx sits on the right. In the room: {names}.",
        lighting="warm_dim",
        actors=[
            ActorState(id="lia", name="Lia", grid=[0.0, 1.0], action="stand"),
            ActorState(id="nyx", name="Nyx", grid=[2.0, 1.0], action="sit"),
        ],
        stage_props=props,
    )


def _legal_events(scene: Scene) -> list[tuple[str, dict]]:
    lia = next(a for a in scene.actors if a.id == "lia")
    nyx = next(a for a in scene.actors if a.id == "nyx")
    out: list[tuple[str, dict]] = []
    intact_free = [p for p in scene.stage_props if p.state != "broken"]
    if lia.holding_prop is None:
        out += [("pick_up", {"object": p.id}) for p in intact_free]
    else:
        out += [("put_down", {"where": w}) for w in ("table", "floor")]
    out += [("break", {"object": p.id}) for p in intact_free if not p.on_floor]
    out += [("move_lia", {"spot": name}) for name, grid in LIA_SPOTS.items() if grid != lia.grid]
    if nyx.elevation == "table":
        out.append(("cat_down", {"spot": "right"}))
    else:
        out.append(("cat_up", {}))
        out += [("move_cat", {"spot": name}) for name, grid in NYX_SPOTS.items() if grid != nyx.grid]
    return out


def _apply(prev: Scene, event: str, detail: dict, scene_id: int) -> Scene:
    scene = prev.model_copy(deep=True)
    scene.scene_id = scene_id
    scene.parent_scene_id = prev.scene_id
    lia = next(a for a in scene.actors if a.id == "lia")
    nyx = next(a for a in scene.actors if a.id == "nyx")
    lia.action, nyx.action = "stand", "sit"
    if event == "pick_up":
        pid = detail["object"]
        prop = next(p for p in scene.stage_props if p.id == pid)
        if not prop.on_floor:
            lia.grid = [0.5, 1.0] if lia.grid[0] <= 1.0 else [1.5, 1.0]
        else:
            lia.grid = [min(2.0, max(0.0, prop.grid[0])), min(2.0, max(0.0, prop.grid[1] - 0.05))]
        scene.stage_props = [p for p in scene.stage_props if p.id != pid]
        lia.holding_prop, lia.action = pid, "hold_up"
        scene.title = "Pick up"
        scene.narration = f"Lia walks over, picks up the {OBJECT_NAMES[pid]} and holds it in both hands."
    elif event == "put_down":
        pid = lia.holding_prop
        on_floor = detail["where"] == "floor"
        if on_floor:
            grid = [round(min(2.0, max(0.0, lia.grid[0] + 0.25)), 2), round(lia.grid[1], 2)]
        else:
            lia.grid = [0.5, 1.0] if lia.grid[0] <= 1.0 else [1.5, 1.0]
            used = {tuple(p.grid) for p in scene.stage_props if not p.on_floor}
            grid = next((list(s) for s in TABLE_SLOTS if tuple(s) not in used), list(TABLE_SLOTS[0]))
        scene.stage_props.append(StagePropState(id=pid, grid=grid, on_floor=on_floor))
        lia.holding_prop = None
        scene.title = "Put down"
        scene.narration = f"Lia puts the {OBJECT_NAMES[pid]} down on the {'floor beside her' if on_floor else 'round table'} and lets go of it."
    elif event == "break":
        pid = detail["object"]
        prop = next(p for p in scene.stage_props if p.id == pid)
        prop.on_floor, prop.state, prop.grid = True, "broken", [1.0, 1.55]
        scene.title = "Break"
        scene.narration = f"The {OBJECT_NAMES[pid]} slips off the round table, hits the floor and breaks into pieces."
    elif event == "move_lia":
        lia.grid, lia.action = list(LIA_SPOTS[detail["spot"]]), "walk"
        scene.title = "Lia moves"
        scene.narration = f"Lia walks to the {detail['spot']} side of the room and stops there."
    elif event == "move_cat":
        nyx.grid, nyx.action = list(NYX_SPOTS[detail["spot"]]), "walk"
        scene.title = "Cat moves"
        scene.narration = f"The black cat Nyx pads across the floor to the {detail['spot']} of the room and sits down."
    elif event == "cat_up":
        nyx.grid, nyx.elevation, nyx.action = [1.0, 1.0], "table", "jump"
        scene.title = "Cat on the table"
        scene.narration = "The black cat Nyx jumps up onto the round table and sits there."
    elif event == "cat_down":
        nyx.grid, nyx.elevation, nyx.action = list(NYX_SPOTS[detail["spot"]]), "floor", "walk"
        scene.title = "Cat off the table"
        scene.narration = "The black cat Nyx hops down off the table onto the floor and sits on the right."
    scene.qa_questions = [f"Does the clip show: {scene.narration}"]
    keep_actors_clear_of_table(scene)  # nobody ends up standing inside the table
    return scene


def generate_scenario(n_scenes: int, n_objects: int, seed: int, events: Optional[tuple[str, ...]] = None) -> Scenario:
    """A seeded chain of `n_scenes` world states over `n_objects` objects (scene 1 is the opening)."""
    if not 1 <= n_objects <= len(OBJECT_POOL):
        raise ValueError(f"n_objects must be 1..{len(OBJECT_POOL)}")
    rng = random.Random(seed * 7919 + n_scenes * 31 + n_objects)
    object_ids = [pid for pid, _, _ in OBJECT_POOL[:n_objects]]
    scenario = Scenario(seed=seed, n_objects=n_objects)
    current = _opening(object_ids)
    scenario.steps.append(Step(1, "opening", {}, current))
    allowed = set(events or EVENT_KINDS)
    last_kind = ""
    for scene_id in range(2, n_scenes + 1):
        options = [e for e in _legal_events(current) if e[0] in allowed]
        varied = [e for e in options if e[0] != last_kind] or options
        kinds = sorted({e[0] for e in varied})
        kind = rng.choice(kinds)  # pick the kind first, so many objects do not crowd out the other events
        event, detail = rng.choice([e for e in varied if e[0] == kind])
        nxt = _apply(current, event, detail, scene_id)
        before = nxt.model_dump_json(exclude={"media"})
        apply_world_rules(current, nxt)
        if nxt.model_dump_json(exclude={"media"}) != before:
            raise AssertionError(f"generated state for scene {scene_id} ({event}) broke a world rule")
        scenario.steps.append(Step(scene_id, event, detail, nxt))
        current, last_kind = nxt, event
    return scenario


def answer_key(scene: Scene) -> dict:
    """The facts every question about `scene` is derived from."""
    lia = next(a for a in scene.actors if a.id == "lia")
    nyx = next(a for a in scene.actors if a.id == "nyx")
    return {
        "where": inventory(scene),
        "broken": sorted(p.id for p in scene.stage_props if p.state == "broken"),
        "lia_side": "left" if lia.grid[0] < 0.75 else ("right" if lia.grid[0] > 1.25 else "beside"),
        "lia_holding": lia.holding_prop,
        "cat_on_table": nyx.elevation == "table",
    }
