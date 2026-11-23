"""Yes/no questions for one scene, generated from the world state. Every expected answer is "yes"."""

from __future__ import annotations

from backend.bench.scenario import OBJECT_NAMES, answer_key
from backend.schemas import Scene

CATEGORIES = ["count", "place", "state", "actors", "clean", "cross_camera", "inter_scene"]


def single_view_questions(scene: Scene) -> list[dict]:
    """Questions asked on the LAST frame of each camera's clip. Each: {category, subject, question}."""
    key = answer_key(scene)
    out: list[dict] = []
    for pid, where in sorted(key["where"].items()):
        name = OBJECT_NAMES.get(pid, pid.replace("_", " "))
        out.append({"category": "count", "subject": pid,
                    "question": f"Is there exactly one {name} in the picture (not two of them, and not none)?"})
        if where.startswith("held:"):
            place = "held in the silver-haired girl's hands (and not on the table or the floor)"
        elif where == "table":
            place = "on top of the round table (and not on the floor or in anyone's hands)"
        else:
            place = "on the floor (and not on the table or in anyone's hands)"
        out.append({"category": "place", "subject": pid, "question": f"Is the {name} {place}?"})
        if pid in key["broken"]:
            out.append({"category": "state", "subject": pid, "question": f"Is the {name} broken (in pieces, not whole)?"})
        else:
            out.append({"category": "state", "subject": pid, "question": f"Is the {name} whole (not broken, not in pieces)?"})
    side = {"left": "on the left side of the round table", "right": "on the right side of the round table",
            "beside": "right next to the round table"}[key["lia_side"]]
    out.append({"category": "actors", "subject": "lia", "question": f"Is there exactly one silver-haired girl, standing {side}?"})
    cat = "sitting on top of the round table" if key["cat_on_table"] else "on the floor (not on top of the table)"
    out.append({"category": "actors", "subject": "nyx", "question": f"Is there exactly one black cat, {cat}?"})
    out.append({"category": "clean", "subject": "frame",
                "question": "Is the picture free of filming equipment and of any text, labels or numbers?"})
    return out


CROSS_CAMERA_QUESTION = (
    "These two pictures show the same moment of one scene from a front view (picture 1) and from a 45 degree side "
    "view (picture 2). Do both show the same characters doing the same thing with the same objects in the same places?"
)
INTER_SCENE_QUESTION = (
    "Picture 1 is the last frame of one clip and picture 2 is the first frame of the next clip from the same view. "
    "Is it the same room with the same characters and objects in the same places, with nothing appearing or vanishing?"
)
