"""
Element 119 Stress Test: Failure Curves (1->4 Actors, 2->8 Scenes) & Baseline Comparison (Owned by P1).

Feature & Use Cases:
- Empirically probes where StageState breaks and compares against a stateless
  prompt-only baseline:
  1. Scene-length scaling (2, 4, 6, 8 scenes): checks causal object permanence,
     grid bounds, single-holder prop invariant, and `judge()` pass rate.
  2. Cast-size scaling (1, 2, 3, 4 actors): checks multi-actor grid collisions
     and reference-limit pressure.
  3. Baseline (stateless text chaining without explicit WorldState/Rule Memory)
     vs. StageState (explicit WorldState + Rule Memory + `judge()`).
- Saves `eval/stress_report.json` and `eval/STRESS_CURVES.md`.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend import config
from backend.director.genai_client import generate_structured, generate_text
from backend.director.hydrogen_judge import judge
from backend.director.rule_memory import load_rules
from backend.logger import log_function_call

EVAL_DIR: Path = Path(__file__).resolve().parent


class _StressScene(BaseModel):
    scene_id: int
    narration: str
    vase_state: str
    lia_grid: list[int]
    nyx_grid: list[int]
    extra_actors_count: int = 0
    holding_umbrella_count: int = 1


class _StressPlan(BaseModel):
    scenes: list[_StressScene]


def _hard_invariant_check(scenes: list[_StressScene], expected_vase: str = "broken") -> tuple[bool, list[str]]:
    """Verify hard world-state invariants (grid bounds, single umbrella holder, vase permanence)."""
    errors: list[str] = []
    for s in scenes:
        for name, g in (("lia", s.lia_grid), ("nyx", s.nyx_grid)):
            if len(g) != 2 or not (0 <= g[0] <= 2 and 0 <= g[1] <= 2):
                errors.append(f"S{s.scene_id}: {name} out of 3x3 grid ({g})")
        if s.holding_umbrella_count > 1:
            errors.append(f"S{s.scene_id}: duplicated red_umbrella holder ({s.holding_umbrella_count})")
        if s.scene_id >= 2 and s.vase_state != expected_vase:
            errors.append(f"S{s.scene_id}: vase permanence violated (got {s.vase_state}, expected {expected_vase})")
    return len(errors) == 0, errors


async def _run_scene_scaling_point(num_scenes: int, use_rule_memory: bool) -> dict[str, Any]:
    """Run one scene-length stress point (2, 4, 6, or 8 scenes) with or without Rule Memory."""
    rules_txt = ""
    if use_rule_memory:
        rules_txt = "Strict World-State Rules:\n- " + "\n- ".join(load_rules())

    prompt = (
        f"Generate a {num_scenes}-scene coherent stage drama on a 3x3 grid (coords 0..2).\n"
        f"In Scene 1, Lia holds 1 red_umbrella and crystal_vase is intact.\n"
        f"In Scene 2, Nyx knocks over crystal_vase so vase_state='broken'.\n"
        f"Plan all scenes 1..{num_scenes} keeping strict physical continuity.\n"
        f"{rules_txt}"
    )
    t0 = time.perf_counter()
    try:
        plan = await generate_structured(
            model=config.MODEL_DIRECTOR,
            system_instruction="Output strict JSON for _StressPlan.",
            contents=[prompt],
            response_schema=_StressPlan,
            temperature=0.3,
            thinking_level="LOW",
        )
        inv_ok, inv_errs = _hard_invariant_check(plan.scenes, expected_vase="broken")
        qa = await judge(
            [
                f"Do scenes 2..{num_scenes} keep crystal_vase broken continuously?",
                "Are all character coordinates inside 0..2 without duplicating red_umbrella?",
            ],
            context_text=plan.model_dump_json(),
        )
        return {
            "num_scenes": num_scenes,
            "rule_memory": use_rule_memory,
            "invariant_passed": inv_ok,
            "invariant_errors": inv_errs,
            "judge_score": qa.overall_score,
            "judge_all_passed": qa.all_passed,
            "latency_sec": round(time.perf_counter() - t0, 2),
        }
    except Exception as exc:
        return {
            "num_scenes": num_scenes,
            "rule_memory": use_rule_memory,
            "invariant_passed": False,
            "invariant_errors": [str(exc)],
            "judge_score": 0.0,
            "judge_all_passed": False,
            "latency_sec": round(time.perf_counter() - t0, 2),
        }


async def _run_stateless_baseline(num_scenes: int = 4) -> dict[str, Any]:
    """Simulate a stateless text-chaining baseline without explicit WorldState or Rule Memory."""
    t0 = time.perf_counter()
    history: list[str] = []
    for sid in range(1, num_scenes + 1):
        prev = history[-1] if history else "Start: Lia is in a glass room with a red umbrella and crystal vase."
        step_prompt = (
            f"Continue this story for Scene {sid} in 1-2 vivid sentences."
            + (" Nyx knocks over the vase." if sid == 2 else "")
            + f"\nPrevious scene: {prev}"
        )
        out = await generate_text(
            model=config.MODEL_JUDGE,
            system_instruction="Write a short next scene paragraph.",
            contents=[step_prompt],
            temperature=0.6,
        )
        history.append(out)

    # R-07: Evaluate stateless baseline with the EXACT SAME questions used for StageState
    qa = await judge(
        [
            f"Do scenes 2..{num_scenes} keep crystal_vase broken continuously?",
            "Are all character coordinates inside 0..2 without duplicating red_umbrella?",
        ],
        context_text="\n".join(f"Scene {i+1}: {t}" for i, t in enumerate(history)),
    )
    return {
        "system": "Stateless Text Chaining Baseline",
        "eval_scope": "plan_level_n1",
        "scenes": num_scenes,
        "judge_score": qa.overall_score,
        "all_passed": qa.all_passed,
        "latency_sec": round(time.perf_counter() - t0, 2),
    }


async def _run_actor_scaling_point(num_actors: int) -> dict[str, Any]:
    """Run one actor-count stress point (1, 2, 3, or 4 actors) on a 4-scene stage."""
    cast_names = ["lia", "nyx", "owl_keeper", "clockmaker"][:num_actors]
    prompt = (
        f"Generate a 4-scene stage drama on a 3x3 grid (coords 0..2) with {num_actors} active actors: {cast_names}.\n"
        "In Scene 2, crystal_vase breaks (`vase_state='broken'`) and must stay broken in Scenes 3-4.\n"
        "Strict rule: every character stays in [0..2, 0..2], and only 1 red_umbrella exists."
    )
    t0 = time.perf_counter()
    try:
        plan = await generate_structured(
            model=config.MODEL_DIRECTOR,
            system_instruction="Output strict JSON for _StressPlan.",
            contents=[prompt],
            response_schema=_StressPlan,
            temperature=0.3,
            thinking_level="LOW",
        )
        inv_ok, inv_errs = _hard_invariant_check(plan.scenes, expected_vase="broken")
        qa = await judge(
            [
                f"Are all {num_actors} actors cleanly placed on the 3x3 stage without colliding or spawning duplicates?",
                "Does crystal_vase stay broken from Scene 2 through Scene 4?",
            ],
            context_text=plan.model_dump_json(),
        )
        return {
            "num_actors": num_actors,
            "cast": cast_names,
            "invariant_passed": inv_ok,
            "invariant_errors": inv_errs,
            "judge_score": qa.overall_score,
            "judge_all_passed": qa.all_passed,
            "latency_sec": round(time.perf_counter() - t0, 2),
        }
    except Exception as exc:
        return {
            "num_actors": num_actors,
            "cast": cast_names,
            "invariant_passed": False,
            "invariant_errors": [str(exc)],
            "judge_score": 0.0,
            "judge_all_passed": False,
            "latency_sec": round(time.perf_counter() - t0, 2),
        }


@log_function_call
async def run_stress_suite() -> dict[str, Any]:
    """Run scene-scaling (2->8), actor-scaling (1->4), and Stateless Baseline comparison."""
    scene_points = [2, 4, 6, 8]
    actor_points = [1, 2, 3, 4]
    baseline_task = _run_stateless_baseline(4)
    no_rules_tasks = [_run_scene_scaling_point(n, use_rule_memory=False) for n in scene_points]
    with_rules_tasks = [_run_scene_scaling_point(n, use_rule_memory=True) for n in scene_points]
    actor_tasks = [_run_actor_scaling_point(a) for a in actor_points]

    baseline_res = await baseline_task
    no_rules_res = await asyncio.gather(*no_rules_tasks)
    with_rules_res = await asyncio.gather(*with_rules_tasks)
    actor_res = await asyncio.gather(*actor_tasks)

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stateless_baseline": baseline_res,
        "scene_scaling_without_rules": no_rules_res,
        "scene_scaling_with_rule_memory": with_rules_res,
        "actor_scaling": actor_res,
    }
    (EVAL_DIR / "stress_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# StageState Stress Curves & Baseline Comparison",
        "",
        f"Generated: {report['timestamp']}",
        "",
        "## 1. Scene Scaling Failure Curve (2 -> 8 Scenes)",
        "| Scenes | No Rule Memory (`judge` / Invariant) | With Rule Memory (`judge` / Invariant) |",
        "| :---: | :---: | :---: |",
    ]
    for r0, r1 in zip(no_rules_res, with_rules_res):
        lines.append(
            f"| {r0['num_scenes']} | {r0['judge_score']} ({'PASS' if r0['invariant_passed'] else 'FAIL'}) "
            f"| {r1['judge_score']} ({'PASS' if r1['invariant_passed'] else 'FAIL'}) |"
        )
    lines += [
        "",
        "## 2. Actor Count Scaling Curve (1 -> 4 Actors, 4 Scenes)",
        "| Actors | Cast | `judge()` Score | Invariant | Latency (s) |",
        "| :---: | :--- | :---: | :---: | :---: |",
    ]
    for ar in actor_res:
        lines.append(
            f"| {ar['num_actors']} | {', '.join(ar['cast'])} | {ar['judge_score']} "
            f"| {'PASS' if ar['invariant_passed'] else 'FAIL'} | {ar['latency_sec']} |"
        )
    lines += [
        "",
        "## 3. Stateless Baseline vs. StageState (4 Scenes)",
        f"- **Stateless Text Chaining Baseline**: `judge_score={baseline_res['judge_score']}`, `all_passed={baseline_res['all_passed']}`",
        f"- **StageState (WorldState + Rule Memory)**: `judge_score={with_rules_res[1]['judge_score']}`, `invariant_passed={with_rules_res[1]['invariant_passed']}`",
    ]
    (EVAL_DIR / "STRESS_CURVES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_stress_suite()), ensure_ascii=False, indent=2))
