"""
World rules: the checks that keep the world state physically sensible after the director model proposes a scene.

These are plain functions on the state (no model calls), so they are cheap, deterministic and unit-tested.
The item ledger is the ONLY place that decides where an object is: in one hand, on the table, or on the floor.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from backend.schemas import Scene, StagePropState

TABLE_CLEARANCE_M = {"nyx": 0.8}
DEFAULT_TABLE_CLEARANCE_M = 1.05
ACTORS_THAT_FIT_ON_TABLE = {"nyx"}

_ONTO_TABLE = re.compile(
    r"\b(leap|leaps|jump|jumps|hop|hops|climb|climbs|spring|springs|perch|perches|settles|sits|sitting|lands)\b[^.]*"
    r"\b(onto|on top of|atop|on)\b[^.]*\btable",
    re.IGNORECASE,
)
_OFF_TABLE = re.compile(
    r"\b(leap|leaps|jump|jumps|hop|hops|climb|climbs|drops|slips)\b[^.]*\b(down|off)\b", re.IGNORECASE
)


def _world_xz(grid: list[float]) -> tuple[float, float]:
    return (grid[0] - 1.0) * 2.1, (grid[1] - 1.0) * 1.7


def _mentions(narration: str, actor_name: str, actor_id: str, pattern: re.Pattern) -> bool:
    """True when a sentence that names this actor (or 'cat' for Nyx) matches `pattern`."""
    words = {actor_name.lower(), actor_id.lower()}
    if actor_id == "nyx":
        words.add("cat")
    for sentence in re.split(r"(?<=[.!?])\s+", narration or ""):
        low = sentence.lower()
        if any(w and w in low for w in words) and pattern.search(sentence):
            return True
    return False


def settle_elevation(prev_scene: Optional[Scene], next_scene: Scene) -> None:
    """
    Decide who is on top of the table in `next_scene`.

    - Only the cat fits on the table; anyone else is put back on the floor.
    - The cat is on the table when the director says so (`elevation: table`), when it jumps while standing at the
      table, or when the narration says it got onto the table.
    - Once on the table the cat stays there until the director moves it away or the narration says it got down.
    """
    prev_by_id = {a.id: a for a in prev_scene.actors} if prev_scene else {}
    for actor in next_scene.actors:
        if actor.id not in ACTORS_THAT_FIT_ON_TABLE:
            actor.elevation = "floor"
            continue
        wx, wz = _world_xz(actor.grid)
        at_table = math.hypot(wx, wz) < TABLE_CLEARANCE_M.get(actor.id, DEFAULT_TABLE_CLEARANCE_M)
        was_on_table = getattr(prev_by_id.get(actor.id), "elevation", "floor") == "table"
        got_down = _mentions(next_scene.narration, actor.name, actor.id, _OFF_TABLE)
        got_up = _mentions(next_scene.narration, actor.name, actor.id, _ONTO_TABLE)

        on_table = actor.elevation == "table"
        if not on_table and actor.action == "jump" and at_table:
            on_table = True
        if not on_table and got_up and not got_down:
            on_table = True
        if not on_table and was_on_table and at_table and not got_down and actor.action not in ("walk", "flee"):
            on_table = True
        if on_table and got_down and not got_up:
            on_table = False

        if on_table:
            actor.elevation = "table"
            actor.grid = [1.0, 1.0]
        else:
            actor.elevation = "floor"


def keep_actors_clear_of_table(scene: Scene) -> None:
    """Push any actor standing inside the centre table out to its edge, so nothing clips through it."""
    for actor in scene.actors:
        if getattr(actor, "elevation", "floor") == "table":
            continue  # on top of the table on purpose
        wx, wz = _world_xz(actor.grid)
        clearance = TABLE_CLEARANCE_M.get(actor.id, DEFAULT_TABLE_CLEARANCE_M)
        dist = math.hypot(wx, wz)
        if dist >= clearance:
            continue
        if dist < 1e-3:
            wx, wz, dist = -1.0, 0.0, 1.0
        wx, wz = wx / dist * clearance, wz / dist * clearance
        actor.grid = [round(wx / 2.1 + 1, 2), round(wz / 1.7 + 1, 2)]


def reconcile_inventory(prev_scene: Scene, next_scene: Scene) -> None:
    """
    The item ledger: every object exists exactly once and never appears, vanishes or mends itself on its own.

    - An object is held by at most one actor; a second claim is dropped.
    - An object in an actor's hands is not also listed on the stage, and no object is listed twice.
    - Every object of the previous scene is still somewhere in the new scene. One the director forgot stays
      where it was; one that was in a hand and is no longer held is put down on the floor beside that actor.
    - A broken object stays broken.
    """
    claimed: set[str] = set()
    for actor in next_scene.actors:
        if not actor.holding_prop:
            continue
        if actor.holding_prop in claimed:
            actor.holding_prop = None
        else:
            claimed.add(actor.holding_prop)
    held = claimed

    unique: list[StagePropState] = []
    seen: set[str] = set()
    for prop in next_scene.stage_props:
        if prop.id in held or prop.id in seen:
            continue
        seen.add(prop.id)
        unique.append(prop)
    next_scene.stage_props = unique

    present = held | seen
    for prop in prev_scene.stage_props:
        if prop.id not in present:
            carried = prop.model_copy(deep=True)
            carried.entry_motion = "place"
            carried.entry_origin = None
            next_scene.stage_props.append(carried)
            present.add(prop.id)
    for actor in prev_scene.actors:
        pid = actor.holding_prop
        if not pid or pid in present:
            continue
        now = next((a for a in next_scene.actors if a.id == actor.id), actor)
        next_scene.stage_props.append(
            StagePropState(
                id=pid,
                grid=[round(min(2.0, max(0.0, now.grid[0] - 0.3)), 2), round(now.grid[1], 2)],
                on_floor=True,
                state="intact",
            )
        )
        present.add(pid)

    broken_before = {p.id for p in prev_scene.stage_props if p.state == "broken"}
    for prop in next_scene.stage_props:
        if prop.id in broken_before:
            prop.state = "broken"


def inventory(scene: Scene) -> dict[str, str]:
    """Where every object is right now: `held:<actor>`, `table` or `floor`. One entry per object."""
    where: dict[str, str] = {}
    for actor in scene.actors:
        if actor.holding_prop:
            where[actor.holding_prop] = f"held:{actor.id}"
    for prop in scene.stage_props:
        where.setdefault(prop.id, "floor" if prop.on_floor else "table")
    return where


def apply_world_rules(prev_scene: Optional[Scene], next_scene: Scene) -> None:
    """Run every rule in the right order. Call this on each scene the director proposes."""
    settle_elevation(prev_scene, next_scene)
    keep_actors_clear_of_table(next_scene)
    if prev_scene is not None:
        reconcile_inventory(prev_scene, next_scene)
