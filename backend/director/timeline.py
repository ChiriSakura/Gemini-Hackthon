"""
Version control for stories: compare two timelines scene by scene, and graft one timeline's later scenes
onto another.

Everything here works on the world state (who stands where, what each actor holds, where each object is and
what state it is in). No model call is needed: the differences are computed, and a bridging scene is built from
the differences when two timelines do not join cleanly.
"""

from __future__ import annotations

import math
from typing import Optional

from backend.director.world_rules import apply_world_rules, inventory
from backend.schemas import BranchState, Scene, SceneMedia

PROP_WORDS = {"crystal_vase": "crystal vase", "red_umbrella": "red umbrella"}


def prop_words(prop_id: str) -> str:
    if prop_id in PROP_WORDS:
        return PROP_WORDS[prop_id]
    words = prop_id.replace("_", " ").split()
    if words and len(words[-1]) == 4 and all(c in "0123456789abcdef" for c in words[-1]):
        words = words[:-1]  # drop the generated id suffix
    return " ".join(words) or prop_id


def stage_spot(grid: list[float]) -> str:
    """Plain words for a floor position, as seen from the front view."""
    side = "left of the table" if grid[0] < 0.7 else ("right of the table" if grid[0] > 1.3 else "beside the table")
    depth = "at the back" if grid[1] < 0.7 else ("at the front" if grid[1] > 1.3 else "")
    return f"{side} {depth}".strip()


def _where_words(where: str, scene: Scene) -> str:
    if where.startswith("held:"):
        holder = next((a.name for a in scene.actors if a.id == where.split(":", 1)[1]), where.split(":", 1)[1])
        return f"in {holder}'s hands"
    return "on the table" if where == "table" else "on the floor"


def diff_scenes(a: Scene, b: Scene, move_tolerance: float = 0.25) -> list[dict]:
    """
    What is different between two world states. Each entry: {kind, subject, a, b, text}.
    Positions closer than `move_tolerance` grid cells count as the same spot.
    """
    out: list[dict] = []
    actors_a, actors_b = {x.id: x for x in a.actors}, {x.id: x for x in b.actors}
    for actor_id in sorted(set(actors_a) | set(actors_b)):
        xa, xb = actors_a.get(actor_id), actors_b.get(actor_id)
        if xa is None or xb is None:
            present = xa or xb
            out.append({"kind": "actor_presence", "subject": actor_id, "a": xa is not None, "b": xb is not None,
                        "text": f"{present.name} is only in one of the two timelines"})
            continue
        ea, eb = getattr(xa, "elevation", "floor"), getattr(xb, "elevation", "floor")
        if ea != eb:
            out.append({"kind": "elevation", "subject": actor_id, "a": ea, "b": eb,
                        "text": f"{xa.name} is on the {ea} in A and on the {eb} in B"})
        elif ea == "floor" and math.hypot(xa.grid[0] - xb.grid[0], xa.grid[1] - xb.grid[1]) > move_tolerance:
            out.append({"kind": "position", "subject": actor_id, "a": list(xa.grid), "b": list(xb.grid),
                        "text": f"{xa.name} stands {stage_spot(xa.grid)} in A and {stage_spot(xb.grid)} in B"})
        if xa.holding_prop != xb.holding_prop:
            wa = prop_words(xa.holding_prop) if xa.holding_prop else "nothing"
            wb = prop_words(xb.holding_prop) if xb.holding_prop else "nothing"
            out.append({"kind": "holding", "subject": actor_id, "a": xa.holding_prop, "b": xb.holding_prop,
                        "text": f"{xa.name} holds {wa} in A and {wb} in B"})
        if xa.action != xb.action:
            out.append({"kind": "action", "subject": actor_id, "a": xa.action, "b": xb.action,
                        "text": f"{xa.name}: {xa.action} in A, {xb.action} in B"})

    inv_a, inv_b = inventory(a), inventory(b)
    state_a = {p.id: p.state for p in a.stage_props}
    state_b = {p.id: p.state for p in b.stage_props}
    for prop_id in sorted(set(inv_a) | set(inv_b)):
        name = prop_words(prop_id)
        if prop_id not in inv_a or prop_id not in inv_b:
            only = "A" if prop_id in inv_a else "B"
            out.append({"kind": "object_presence", "subject": prop_id, "a": prop_id in inv_a, "b": prop_id in inv_b,
                        "text": f"The {name} exists only in {only}"})
            continue
        held_pair = inv_a[prop_id].startswith("held:") and inv_b[prop_id].startswith("held:")
        if inv_a[prop_id] != inv_b[prop_id] and not (held_pair and inv_a[prop_id] == inv_b[prop_id]):
            if not (inv_a[prop_id].startswith("held:") or inv_b[prop_id].startswith("held:")) or True:
                out.append({"kind": "object_place", "subject": prop_id, "a": inv_a[prop_id], "b": inv_b[prop_id],
                            "text": f"The {name} is {_where_words(inv_a[prop_id], a)} in A and {_where_words(inv_b[prop_id], b)} in B"})
        broken_a, broken_b = state_a.get(prop_id) == "broken", state_b.get(prop_id) == "broken"
        if broken_a != broken_b:
            out.append({"kind": "object_state", "subject": prop_id, "a": "broken" if broken_a else "intact",
                        "b": "broken" if broken_b else "intact",
                        "text": f"The {name} is {'broken' if broken_a else 'intact'} in A and {'broken' if broken_b else 'intact'} in B"})
    if a.lighting != b.lighting:
        out.append({"kind": "lighting", "subject": "stage", "a": a.lighting, "b": b.lighting,
                    "text": f"Lighting: {a.lighting} in A, {b.lighting} in B"})
    # "holding" already says where a held object is; drop the duplicate object_place line for it.
    holding_subjects = {d["a"] for d in out if d["kind"] == "holding"} | {d["b"] for d in out if d["kind"] == "holding"}
    return [d for d in out if not (d["kind"] == "object_place" and d["subject"] in holding_subjects
                                   and (str(d["a"]).startswith("held:") or str(d["b"]).startswith("held:")))]


def _same_rendered_scene(a: Scene, b: Scene) -> bool:
    """True when two scenes are literally the same take (shared prefix of a fork)."""
    return bool(a.media.cam_a_final_url) and a.media.cam_a_final_url == b.media.cam_a_final_url


def diff_branches(branch_a: BranchState, branch_b: BranchState) -> dict:
    """Scene-by-scene comparison of two timelines, aligned by scene number."""
    scenes_a = {s.scene_id: s for s in branch_a.scenes}
    scenes_b = {s.scene_id: s for s in branch_b.scenes}
    rows: list[dict] = []
    diverged_at: Optional[int] = None
    for scene_id in sorted(set(scenes_a) | set(scenes_b)):
        sa, sb = scenes_a.get(scene_id), scenes_b.get(scene_id)
        if sa is None or sb is None:
            rows.append({"scene_id": scene_id, "a_title": sa.title if sa else None, "b_title": sb.title if sb else None,
                         "same_take": False, "differences": [],
                         "only_in": "A" if sa else "B"})
            diverged_at = diverged_at or scene_id
            continue
        same_take = _same_rendered_scene(sa, sb)
        differences = [] if same_take else diff_scenes(sa, sb)
        if (differences or (not same_take and sa.narration != sb.narration)) and diverged_at is None:
            diverged_at = scene_id
        rows.append({"scene_id": scene_id, "a_title": sa.title, "b_title": sb.title, "same_take": same_take,
                     "same_story": sa.narration == sb.narration, "differences": differences, "only_in": None})
    return {
        "a": {"branch_id": branch_a.branch_id, "label": branch_a.branch_label, "scenes": len(branch_a.scenes)},
        "b": {"branch_id": branch_b.branch_id, "label": branch_b.branch_label, "scenes": len(branch_b.scenes)},
        "diverged_at": diverged_at,
        "scenes": rows,
    }


def build_bridge_scene(base_end: Scene, donor_start: Scene, scene_id: int) -> Scene:
    """
    A scene that takes the world from `base_end` to the state `donor_start` (the state the grafted scenes were
    written to start from). Built from the differences, without a model: the actors go where they need to be and
    the objects are picked up or put where they need to be.
    """
    differences = diff_scenes(base_end, donor_start)
    names = {a.id: a.name for a in donor_start.actors}
    sentences: list[str] = []
    for d in differences:
        who = names.get(d["subject"], d["subject"])
        if d["kind"] == "position":
            sentences.append(f"{who} walks over and stops {stage_spot(d['b'])}.")
        elif d["kind"] == "elevation":
            sentences.append(f"{who} jumps up onto the table." if d["b"] == "table" else f"{who} hops down from the table.")
        elif d["kind"] == "holding":
            if d["b"] and d["a"]:
                sentences.append(f"{who} puts down the {prop_words(d['a'])} and picks up the {prop_words(d['b'])}.")
            elif d["b"]:
                sentences.append(f"{who} picks up the {prop_words(d['b'])}.")
            else:
                place = inventory(donor_start).get(d["a"], "floor")
                sentences.append(f"{who} puts the {prop_words(d['a'])} down on the {'table' if place == 'table' else 'floor'}.")
        elif d["kind"] == "object_place":
            sentences.append(f"The {prop_words(d['subject'])} ends up {_where_words(d['b'], donor_start)}.")
        elif d["kind"] == "object_state" and d["b"] == "broken":
            sentences.append(f"The {prop_words(d['subject'])} falls and breaks.")
    bridge = donor_start.model_copy(deep=True)
    bridge.scene_id = scene_id
    bridge.title = "Bridge"
    bridge.narration = " ".join(sentences) or "A quiet beat as everyone settles."
    bridge.qa_questions = [f"Does the clip show: {s}" for s in sentences[:3]] or ["Is the scene calm and unchanged?"]
    bridge.media = SceneMedia()
    bridge.needs_rerender = True
    bridge.reused_from_branch = None
    bridge.parent_scene_id = base_end.scene_id
    for actor in bridge.actors:
        before = next((x for x in base_end.actors if x.id == actor.id), None)
        if before is not None and math.hypot(before.grid[0] - actor.grid[0], before.grid[1] - actor.grid[1]) > 0.25:
            actor.action = "walk" if getattr(actor, "elevation", "floor") == "floor" else actor.action
    apply_world_rules(base_end, bridge)
    return bridge


def graft(
    base: BranchState,
    base_scene_id: int,
    donor: BranchState,
    donor_from_scene_id: int,
    new_branch_id: str,
) -> tuple[BranchState, dict]:
    """
    Continue `base` after its scene `base_scene_id` with `donor`'s scenes from `donor_from_scene_id` on.

    If the world at the end of the base part equals the world the donor scenes start from, they are attached
    directly. Otherwise a bridging scene is inserted first. The item ledger then runs through every attached
    scene, so an object that exists only in the base timeline is carried along instead of vanishing.
    """
    base_scenes = sorted(base.scenes, key=lambda s: s.scene_id)
    donor_scenes = sorted(donor.scenes, key=lambda s: s.scene_id)
    kept = [s.model_copy(deep=True) for s in base_scenes if s.scene_id <= base_scene_id]
    tail = [s.model_copy(deep=True) for s in donor_scenes if s.scene_id >= donor_from_scene_id]
    if not kept or kept[-1].scene_id != base_scene_id:
        raise ValueError(f"Scene {base_scene_id} is not in {base.branch_id}")
    if not tail:
        raise ValueError(f"Scene {donor_from_scene_id} is not in {donor.branch_id}")
    donor_start = next((s for s in donor_scenes if s.scene_id == donor_from_scene_id - 1), None)
    base_end = kept[-1]

    differences = diff_scenes(base_end, donor_start) if donor_start is not None else []
    material = [d for d in differences if d["kind"] not in ("action", "lighting")]
    same_take = donor_start is not None and _same_rendered_scene(base_end, donor_start)
    attached: list[Scene] = []
    next_id = base_scene_id + 1
    if material and donor_start is not None:
        attached.append(build_bridge_scene(base_end, donor_start, next_id))
        next_id += 1
    for scene in tail:
        scene.parent_scene_id = next_id - 1
        scene.scene_id = next_id
        scene.reused_from_branch = donor.branch_id
        if not same_take or material:
            scene.media = SceneMedia()  # filmed from another start: shown as 3D preview until it is rendered again
            scene.needs_rerender = True
        attached.append(scene)
        next_id += 1

    prev = base_end
    for scene in attached:
        apply_world_rules(prev, scene)
        prev = scene

    registry = dict(donor.props_registry)
    registry.update(base.props_registry)
    out = BranchState(
        branch_id=new_branch_id,
        branch_label=f"Graft: {base.branch_id} S{base_scene_id} + {donor.branch_id} S{donor_from_scene_id}+",
        parent_branch_id=base.branch_id,
        forked_from_scene=base_scene_id + 1,
        rewrite_summary=f"Grafted scenes {donor_from_scene_id}+ of {donor.branch_id} after scene {base_scene_id}",
        props_registry=registry,
        scenes=[*kept, *attached],
    )
    report = {
        "mode": "bridge" if material else "direct",
        "differences_at_join": differences,
        "bridge_narration": attached[0].narration if material else None,
        "kept_final_clips": bool(same_take and not material),
        "scenes_attached": len(attached),
        "new_branch_id": new_branch_id,
    }
    return out, report
