"""
Story Branch State Tree & Disk Cache Manager (Owned by P1).

Feature & Use Cases:
- Persists story branches (`branch_v1`, `branch_v2`, ...) as JSON files under
  `backend/assets/branches/` so pre-cached base stories and forked timelines
  can be compared side-by-side and survive server restarts.
- Seeds `branch_v1` directly from `shared/mock_branch_1.json` (and copies
  `shared/assets/*` to `/assets/` paths if needed) on first boot.
"""

from typing import Optional

from backend import config
from backend.logger import log_function_call
from backend.schemas import BranchState


def _seed_default_branch() -> BranchState:
    """Seed `branch_v1` from `shared/mock_branch_1.json`."""
    if config.SHARED_MOCK_BRANCH_1.exists():
        branch = BranchState.model_validate_json(config.SHARED_MOCK_BRANCH_1.read_text(encoding="utf-8"))
    else:
        branch = BranchState(
            branch_id="branch_v1",
            branch_label="BE · 雨夜惊逃与碎花瓶",
            props_registry={},
            scenes=[],
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
