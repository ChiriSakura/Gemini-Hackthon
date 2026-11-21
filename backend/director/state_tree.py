"""
Story Branch State Tree & Disk Cache Manager (Owned by P1).

Feature & Use Cases:
- Persists story branches (`branch_v1`, `branch_v2`, ...) as JSON files under
  `backend/assets/branches/` so pre-cached base stories and forked timelines
  can be compared side-by-side and survive server restarts.
- Seeds `branch_v1` directly from `shared/mock_branch_1.json` (and copies
  `shared/assets/*` to `/assets/` paths if needed) on first boot.
"""

import os
import re
import tempfile
from typing import Optional

from backend import config
from backend.logger import log_function_call
from backend.schemas import BranchState

_BRANCH_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


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
    """Atomically persist a BranchState JSON snapshot to disk (R-38 race-safe replace)."""
    safe_id = _validate_branch_id(branch.branch_id)
    path = config.BRANCHES_DIR / f"{safe_id}.json"
    payload = branch.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{safe_id}_", suffix=".tmp", dir=str(config.BRANCHES_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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


def is_protected_branch(branch_id: str) -> bool:
    """Return True for immutable preset baselines (`branch_v1` and `*_cached`)."""
    return branch_id == "branch_v1" or branch_id.endswith("_cached")


@log_function_call
def delete_branch(branch_id: str) -> bool:
    """Delete a non-protected live branch JSON from disk cleanly (R-26 safe)."""
    safe_id = _validate_branch_id(branch_id)
    if is_protected_branch(safe_id):
        raise PermissionError(f"Protected preset branch cannot be deleted: {safe_id}")
    path = config.BRANCHES_DIR / f"{safe_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
