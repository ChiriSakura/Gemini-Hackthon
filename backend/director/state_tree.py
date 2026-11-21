"""
Story Branch State Tree & Disk Cache Manager (Owned by P1).

Feature & Use Cases:
- Persists story branches (`branch_v1`, `branch_v2`, ...) as JSON files under
  `backend/assets/branches/` so pre-cached base stories and forked timelines
  can be compared side-by-side and survive server restarts.
- Seeds a default 4-scene `branch_v1` story on first boot so P2 and P3 can
  develop and test immediately.
"""

import json
from typing import Optional

from backend import config
from backend.logger import log_function_call
from backend.schemas import (
    ActorAsset,
    ActorPlacement,
    BranchState,
    PropAsset,
    SceneMedia,
    SceneState,
    StagePropPlacement,
)


def _seed_default_branch() -> BranchState:
    """Create and persist the initial 4-scene StageState story (`branch_v1`)."""
    branch = BranchState(
        branch_id="branch_v1",
        parent_branch_id=None,
        forked_from_scene=1,
        props_registry={
            "red_umbrella": PropAsset(
                prop_id="red_umbrella",
                name="红伞",
                image_url="/assets/props/red_umbrella.png",
                state="intact",
                source_type="text",
            ),
            "crystal_vase": PropAsset(
                prop_id="crystal_vase",
                name="水晶花瓶",
                image_url="/assets/props/crystal_vase.png",
                state="intact",
                source_type="text",
            ),
        },
        actors_registry={
            "heroine": ActorAsset(
                actor_id="heroine",
                name="少女",
                portrait_url="/assets/actors/heroine.png",
                color_hex="#E91E63",
            ),
            "detective": ActorAsset(
                actor_id="detective",
                name="侦探",
                portrait_url="/assets/actors/detective.png",
                color_hex="#1E88E5",
            ),
        },
        scenes=[
            SceneState(
                scene_id=1,
                narration="雨夜舞台中央，少女撑着红伞等待侦探，桌旁放着水晶花瓶。",
                vocal_emotion="calm_mystery",
                lighting="warm_gold",
                actors=[
                    ActorPlacement(id="heroine", grid=[1, 1], action="stand", holding_prop="red_umbrella"),
                    ActorPlacement(id="detective", grid=[1, 0], action="walk_in", holding_prop=None),
                ],
                stage_props=[StagePropPlacement(id="crystal_vase", grid=[0, 2], on_floor=False)],
                qa_questions=[
                    "Is the heroine holding the red umbrella?",
                    "Is the crystal vase intact on the side?",
                ],
                media=SceneMedia(qa_score=0.95),
            ),
            SceneState(
                scene_id=2,
                narration="侦探质问少女的秘密，少女情绪激动但仍紧握红伞。",
                vocal_emotion="tense",
                lighting="warm_gold",
                actors=[
                    ActorPlacement(id="heroine", grid=[1, 1], action="argue", holding_prop="red_umbrella"),
                    ActorPlacement(id="detective", grid=[1, 2], action="point", holding_prop=None),
                ],
                stage_props=[StagePropPlacement(id="crystal_vase", grid=[0, 2], on_floor=False)],
                qa_questions=[
                    "Are both characters facing each other on stage?",
                    "Is the heroine still holding the red umbrella?",
                ],
                media=SceneMedia(qa_score=0.92),
            ),
            SceneState(
                scene_id=3,
                narration="争执升级，两人各自退开一步，花瓶在桌角微微晃动。",
                vocal_emotion="anxious",
                lighting="cold_blue",
                actors=[
                    ActorPlacement(id="heroine", grid=[2, 0], action="step_back", holding_prop="red_umbrella"),
                    ActorPlacement(id="detective", grid=[0, 1], action="inspect", holding_prop=None),
                ],
                stage_props=[StagePropPlacement(id="crystal_vase", grid=[0, 2], on_floor=False)],
                qa_questions=[
                    "Are the heroine and detective separated across the stage?",
                ],
                media=SceneMedia(qa_score=0.90),
            ),
            SceneState(
                scene_id=4,
                narration="少女撑着红伞独自走入雨幕，真相被带走。",
                vocal_emotion="melancholy",
                lighting="cold_blue",
                actors=[
                    ActorPlacement(id="heroine", grid=[2, 2], action="exit", holding_prop="red_umbrella"),
                    ActorPlacement(id="detective", grid=[1, 1], action="watch", holding_prop=None),
                ],
                stage_props=[StagePropPlacement(id="crystal_vase", grid=[0, 2], on_floor=False)],
                qa_questions=[
                    "Is the heroine leaving with the red umbrella?",
                ],
                media=SceneMedia(qa_score=0.93),
            ),
        ],
    )
    save_branch(branch)
    return branch


@log_function_call
def save_branch(branch: BranchState) -> None:
    """Persist a BranchState JSON snapshot to disk."""
    path = config.BRANCHES_DIR / f"{branch.branch_id}.json"
    path.write_text(branch.model_dump_json(indent=2), encoding="utf-8")


@log_function_call
def get_branch(branch_id: str) -> Optional[BranchState]:
    """Load a BranchState from disk, seeding `branch_v1` if no branches exist."""
    if not any(config.BRANCHES_DIR.glob("*.json")):
        _seed_default_branch()
    path = config.BRANCHES_DIR / f"{branch_id}.json"
    if not path.exists():
        return None
    return BranchState.model_validate_json(path.read_text(encoding="utf-8"))


@log_function_call
def list_branches() -> list[BranchState]:
    """Return all saved branches ordered by filename."""
    if not any(config.BRANCHES_DIR.glob("*.json")):
        _seed_default_branch()
    branches: list[BranchState] = []
    for file_path in sorted(config.BRANCHES_DIR.glob("*.json")):
        branches.append(BranchState.model_validate_json(file_path.read_text(encoding="utf-8")))
    return branches
