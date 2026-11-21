"""
Story Branch State Tree & Disk Cache Manager (Owned by P1).

Feature & Use Cases:
- Persists story branches (`branch_v1`, `branch_v2`, ...) as JSON files under
  `backend/assets/branches/` so pre-cached base stories and forked timelines
  can be compared side-by-side and survive server restarts.
- Seeds `branch_v1` directly from `shared/mock_branch_1.json` (and copies
  `shared/assets/*` to `/assets/` paths if needed) on first boot.
"""

import asyncio
import re
from typing import Optional

from backend import config
from backend.logger import log_function_call
from backend.schemas import BranchState, Scene

_BRANCH_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_BRANCH_LOCKS: dict[str, asyncio.Lock] = {}


def get_branch_lock(branch_id: str) -> asyncio.Lock:
    """Return a per-branch `asyncio.Lock` preventing concurrent read-modify-write clobbering (R-38)."""
    if branch_id not in _BRANCH_LOCKS:
        _BRANCH_LOCKS[branch_id] = asyncio.Lock()
    return _BRANCH_LOCKS[branch_id]


async def update_scene_in_branch(branch_id: str, updated_scene: Scene) -> Optional[BranchState]:
    """Atomically merge ONLY `updated_scene` back into persisted `branch_id` under its branch lock (R-38)."""
    async with get_branch_lock(branch_id):
        latest = get_branch(branch_id)
        if not latest:
            return None
        replaced = False
        for i, existing in enumerate(latest.scenes):
            if existing.scene_id == updated_scene.scene_id:
                latest.scenes[i] = updated_scene
                replaced = True
                break
        if not replaced:
            latest.scenes.append(updated_scene)
            latest.scenes.sort(key=lambda s: s.scene_id)
        save_branch(latest)
        return latest


def _validate_branch_id(branch_id: str) -> str:
    """Validate `branch_id` against strict alphanumeric/underscore/dash pattern (R-28 path-traversal guard)."""
    if not branch_id or not _BRANCH_ID_RE.match(branch_id):
        raise ValueError(f"Invalid branch_id: {branch_id!r}")
    return branch_id


def _seed_default_branch() -> BranchState:
    """Seed `branch_v1` from `shared/mock_branch_1.json`."""
    if config.SHARED_MOCK_BRANCH_1.exists():
        branch = BranchState.model_validate_json(config.SHARED_MOCK_BRANCH_1.read_text(encoding="utf-8"))
    else:
        branch = BranchState(
            branch_id="branch_v1",
            branch_label="BE · Rainy Escape & Shattered Vase",
            props_registry={},
            scenes=[],
        )
    save_branch(branch)
    return branch


@log_function_call
def save_branch(branch: BranchState) -> None:
    """Persist a BranchState JSON snapshot to disk."""
    safe_id = _validate_branch_id(branch.branch_id)
    path = config.BRANCHES_DIR / f"{safe_id}.json"
    path.write_text(branch.model_dump_json(indent=2), encoding="utf-8")


@log_function_call
def get_branch(branch_id: str) -> Optional[BranchState]:
    """Load a BranchState from disk, seeding `branch_v1` if no branches exist."""
    if not _BRANCH_ID_RE.match(branch_id or ""):
        return None
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
