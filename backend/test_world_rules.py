"""
Unit tests for the world rules (item ledger, who is on the table, table clearance) and for the 3D stage projection.

Run: `.venv/bin/python backend/test_world_rules.py`. No model calls, no network.
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.director.world_rules import (
    apply_world_rules,
    inventory,
    keep_actors_clear_of_table,
    reconcile_inventory,
    settle_elevation,
)
from backend.schemas import ActorState, Scene, StagePropState


def _scene(scene_id: int, lia: dict, nyx: dict, props: list[dict], narration: str = "A quiet moment.") -> Scene:
    return Scene(
        scene_id=scene_id,
        title=f"Scene {scene_id}",
        narration=narration,
        lighting="warm_dim",
        actors=[
            ActorState(id="lia", name="Lia", grid=lia.get("grid", [0.0, 1.0]), action=lia.get("action", "stand"),
                       holding_prop=lia.get("holding"), elevation=lia.get("elevation", "floor")),
            ActorState(id="nyx", name="Nyx", grid=nyx.get("grid", [2.0, 1.0]), action=nyx.get("action", "sit"),
                       holding_prop=nyx.get("holding"), elevation=nyx.get("elevation", "floor")),
        ],
        stage_props=[StagePropState(**p) for p in props],
    )


VASE = {"id": "crystal_vase", "grid": [1.0, 1.0]}


def test_pick_up_removes_object_from_table():
    prev = _scene(1, {"holding": "red_umbrella"}, {}, [VASE])
    nxt = _scene(2, {"holding": "crystal_vase", "action": "hold_up"}, {}, [VASE])  # director forgot to remove it
    reconcile_inventory(prev, nxt)
    where = inventory(nxt)
    assert where["crystal_vase"] == "held:lia"
    assert [p.id for p in nxt.stage_props].count("crystal_vase") == 0
    assert where["red_umbrella"] == "floor", "the umbrella she let go of is put down, not deleted"


def test_put_down_keeps_exactly_one():
    prev = _scene(2, {"holding": "crystal_vase"}, {}, [])
    nxt = _scene(3, {}, {}, [VASE, VASE])  # listed twice
    reconcile_inventory(prev, nxt)
    assert [p.id for p in nxt.stage_props].count("crystal_vase") == 1
    assert inventory(nxt)["crystal_vase"] == "table"


def test_forgotten_object_stays_where_it_was():
    cup = {"id": "iced_tea", "grid": [1.1, 1.0]}
    prev = _scene(3, {}, {}, [VASE, cup])
    nxt = _scene(4, {}, {}, [VASE])  # director dropped the cup
    reconcile_inventory(prev, nxt)
    assert inventory(nxt) == {"crystal_vase": "table", "iced_tea": "table"}


def test_hand_over_one_holder_only():
    prev = _scene(4, {"holding": "star_token"}, {}, [VASE])
    nxt = _scene(5, {"holding": "star_token"}, {"holding": "star_token"}, [VASE])
    reconcile_inventory(prev, nxt)
    holders = [a.id for a in nxt.actors if a.holding_prop == "star_token"]
    assert holders == ["lia"], holders
    assert "star_token" not in [p.id for p in nxt.stage_props]


def test_broken_stays_broken():
    broken = {"id": "crystal_vase", "grid": [1.0, 1.3], "on_floor": True, "state": "broken"}
    prev = _scene(2, {}, {}, [broken])
    nxt = _scene(3, {}, {}, [VASE])  # director "mended" it
    reconcile_inventory(prev, nxt)
    vase = next(p for p in nxt.stage_props if p.id == "crystal_vase")
    assert vase.state == "broken"


def test_carried_out_and_back_in_hand():
    prev = _scene(2, {"holding": "crystal_vase"}, {}, [])
    nxt = _scene(3, {"holding": "crystal_vase", "grid": [2.0, 2.0], "action": "walk"}, {}, [])
    reconcile_inventory(prev, nxt)
    assert inventory(nxt)["crystal_vase"] == "held:lia"
    assert nxt.stage_props == []


def test_cat_jumps_onto_table_and_stays():
    s1 = _scene(1, {}, {}, [VASE])
    s2 = _scene(2, {}, {"grid": [1.0, 1.0], "action": "jump"}, [VASE], "Nyx leaps up onto the round table.")
    apply_world_rules(s1, s2)
    nyx = next(a for a in s2.actors if a.id == "nyx")
    assert nyx.elevation == "table" and nyx.grid == [1.0, 1.0]
    # next scene: director writes 'sit' near the table and says nothing about getting down
    s3 = _scene(3, {}, {"grid": [1.0, 1.0], "action": "sit"}, [VASE], "Lia smiles at Nyx.")
    apply_world_rules(s2, s3)
    nyx3 = next(a for a in s3.actors if a.id == "nyx")
    assert nyx3.elevation == "table" and nyx3.grid == [1.0, 1.0], (nyx3.elevation, nyx3.grid)


def test_cat_narration_backstop_and_jump_down():
    s1 = _scene(1, {}, {}, [VASE])
    s2 = _scene(2, {}, {"grid": [0.62, 1.0], "action": "sit"}, [VASE],
                "With a soft flick of her tail, Nyx leaps gracefully up onto the round wooden center table and settles beside the vase.")
    apply_world_rules(s1, s2)
    assert next(a for a in s2.actors if a.id == "nyx").elevation == "table"
    s3 = _scene(3, {}, {"grid": [1.0, 1.0], "action": "walk"}, [VASE], "Nyx hops down off the table and pads over to Lia.")
    apply_world_rules(s2, s3)
    nyx3 = next(a for a in s3.actors if a.id == "nyx")
    assert nyx3.elevation == "floor"
    wx, wz = (nyx3.grid[0] - 1) * 2.1, (nyx3.grid[1] - 1) * 1.7
    assert (wx**2 + wz**2) ** 0.5 >= 0.79, "back on the floor, the cat is pushed clear of the table"


def test_lia_never_on_table_and_is_pushed_clear():
    s = _scene(2, {"grid": [1.0, 1.0], "elevation": "table"}, {}, [VASE], "Lia stands on the table.")
    apply_world_rules(None, s)
    lia = next(a for a in s.actors if a.id == "lia")
    assert lia.elevation == "floor"
    wx, wz = (lia.grid[0] - 1) * 2.1, (lia.grid[1] - 1) * 1.7
    assert (wx**2 + wz**2) ** 0.5 >= 1.04


def test_lia_sentence_does_not_lift_cat():
    s1 = _scene(1, {}, {}, [VASE])
    s2 = _scene(2, {}, {}, [VASE], "Lia sets the vase on the table. Nyx watches from the floor.")
    apply_world_rules(s1, s2)
    assert next(a for a in s2.actors if a.id == "nyx").elevation == "floor"


def test_stage3d_projection_and_one_of_everything():
    from backend.media import stage3d

    s = _scene(2, {"grid": [0.5, 1.0], "holding": "crystal_vase", "action": "hold_up"}, {"elevation": "table", "grid": [1.0, 1.0]}, [])
    for cam in ("cam_a", "cam_b"):
        feet = stage3d.project_actor_feet(s, cam)
        assert set(feet) == {"lia", "nyx"}
        for x, y in feet.values():
            assert 0 <= x <= 1280 and 0 <= y <= 720, (cam, feet)
    # the cat on the table is drawn higher than it would be on the floor
    floor_cat = _scene(2, {}, {"grid": [1.0, 1.0]}, [])
    assert stage3d.project_actor_feet(s, "cam_a")["nyx"][1] < stage3d.project_actor_feet(floor_cat, "cam_a")["nyx"][1]
    # left of the table in the world = left of the table on screen in the front view
    cam = stage3d.StageCamera("cam_a")
    assert stage3d.project_actor_feet(s, "cam_a")["lia"][0] < cam.project((0.0, 0.0, 0.0))[0]


def test_viewpoints_single_definition():
    from backend import config
    from backend.media import stage3d

    raw = json.loads((config.SHARED_DIR / "viewpoints.json").read_text())
    for cam in ("cam_a", "cam_b"):
        assert tuple(raw[cam]["pos"]) == stage3d.VIEWPOINTS[cam]["pos"]
        assert raw[cam]["fov"] == stage3d.VIEWPOINTS[cam]["fov"]


def test_every_action_shows_on_the_stage():
    """Each action in the schema has a pose entry, and the ones that change the figure really change the picture."""
    import typing

    from backend.media import stage3d

    actions = typing.get_args(ActorState.model_fields["action"].annotation)
    assert set(actions) <= set(stage3d.ACTION_POSES), set(actions) - set(stage3d.ACTION_POSES)

    def frame(action: str, phase: float, holding=None):
        s = _scene(2, {"grid": [0.4, 1.4], "action": action, "holding": holding}, {}, [])
        return stage3d.compose_stage_image(s, "cam_a", 640, 360, {"star_token": 0.2}, None, phase).tobytes()

    stand = frame("stand", 0.5)
    for action in ("kneel", "crouch", "sit", "jump", "flee", "catch_from_camera"):
        assert frame(action, 0.5) != stand, f"{action} looks the same as standing"
    carried = frame("stand", 0.5, "star_token")
    for action in ("drink", "eat", "hold_up", "reach"):
        assert frame(action, 0.5, "star_token") != carried, f"{action} does not move the held object"
    assert frame("shared_umbrella", 0.5, "red_umbrella") != frame("stand", 0.5, "red_umbrella")


def _viewpoints_check_payload() -> dict:
    """World points and where backend/media/stage3d.py puts them on screen, for the browser-side comparison."""
    from backend.media import stage3d

    width, height = 1280, 720
    named = {
        "table_base": (0.0, 0.0, 0.0),
        "table_top": (0.0, 0.75, 0.0),
        "lia_default_feet": (-2.1, 0.0, 0.0),
        "lia_default_head": (-2.1, 1.62, 0.0),
        "nyx_default_feet": (2.1, 0.0, 0.0),
        "front_row": (0.0, 0.0, 1.7),
        "back_row": (0.0, 0.0, -1.7),
        "cat_on_table": (-0.28, 0.75, 0.04),
        "right_front": (1.6, 0.0, 1.2),
    }
    points: dict = {}
    for cam_id in ("cam_a", "cam_b"):
        cam = stage3d.StageCamera(cam_id, width, height)
        points[cam_id] = []
        for name, world in named.items():
            pr = cam.project(world)
            points[cam_id].append({"name": name, "world": list(world), "pixel": [round(pr[0], 3), round(pr[1], 3)]})
    return {"width": width, "height": height, "points": points}


def test_viewpoints_check_file_is_current():
    """shared/viewpoints_check.json is what frontend/scripts/test_viewpoints.mjs compares the browser camera to."""
    from backend import config

    path = config.SHARED_DIR / "viewpoints_check.json"
    payload = _viewpoints_check_payload()
    if "--write-viewpoints-check" in sys.argv or not path.is_file():
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == payload, "run with --write-viewpoints-check after changing the viewpoints"
    from backend.media import stage3d

    spot = stage3d.actor_world_position(ActorState(id="nyx", name="Nyx", grid=[1.0, 1.0], elevation="table"))
    assert [round(v, 2) for v in spot] == [-0.28, 0.75, 0.04], "the browser stage uses the same on-table spot"


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
