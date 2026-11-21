"""
Continuity Rule Memory Store (Owned by P1).

Feature & Use Cases:
- Implements the hackathon "Rule Memory" bonus design: whenever `judge()`
  catches a continuity flaw (e.g., broken vase reappearing intact or missing
  red umbrella) and Hydrogen repairs it, the lesson is distilled into a concise
  explicit rule and persisted under `backend/assets/rule_memory.json`.
- Subsequent `plan_scenes()` calls to Gemini 4 Neon automatically include all
  active rules, measurably boosting first-pass QA rates without learning physics.
"""

import json
import re
from pathlib import Path

from backend import config
from backend.logger import log_function_call

RULES_PATH: Path = config.ASSETS_DIR / "rule_memory.json"

_DEFAULT_RULES: list[str] = [
    "Once crystal_vase is broken in scene k, it MUST remain broken on the floor in all scenes > k unless explicitly repaired.",
    "Every registered prop in props_registry has at most ONE holder (or rests on stage_props) per scene; never duplicate held props.",
    "Never introduce extra unintended human characters beyond Lia and Nyx unless explicitly instructed.",
    "The red umbrella stays closed (furled) indoors; write 'opens the umbrella' explicitly in the narration of any scene where it is open.",
]


@log_function_call
def load_rules() -> list[str]:
    """Load persisted continuity rules from disk, seeding defaults if missing."""
    if not RULES_PATH.exists():
        RULES_PATH.write_text(json.dumps(_DEFAULT_RULES, ensure_ascii=False, indent=2), encoding="utf-8")
        return list(_DEFAULT_RULES)
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    return list(_DEFAULT_RULES)


def load_general_rules() -> list[str]:
    """Return only story-agnostic rules.

    Scene-specific repair notes ("Scene 2 (cam_a) continuity fix: ...", "Scene 3 check reminder: ...")
    describe one branch's plot. Feeding them to the director would fight a rewrite that changes that plot,
    so they stay in the file for display but are not used when planning a new branch.
    """
    return [r for r in load_rules() if not re.match(r"^\s*Scene\s+\d+\b", r)]


@log_function_call
def add_rule(rule_text: str) -> list[str]:
    """Append a newly distilled continuity rule if not already present and save to disk."""
    clean = rule_text.strip()
    rules = load_rules()
    if clean and clean not in rules:
        rules.append(clean)
        RULES_PATH.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return rules
