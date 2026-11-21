# Task Spec — Subagent P1: Director Brain, `judge()`, State Tree & Eval

- **Branch**: `feat/p1-director`
- **Owned Paths**: `backend/director/**`, `eval/**`, `tasks/status_P1.json`
- **Status File**: Update `tasks/status_P1.json` at every milestone.

## Deadlines
- **T0 (12:10 – 12:25)**: Verify Python env + `.env` `GEMINI_API_KEY` + run `uvicorn backend.main:app --reload` in mock mode.
- **T1 (12:25 – 13:15)**: Complete real Gemini 4 Neon + Hydrogen calls in `backend/director/`:
  1. `hydrogen_judge.py`:
     - `judge(questions: list[str], state_summary: str | None = None, media_paths: list[str] | None = None) -> list[QAResult]` using **Gemini 4 Hydrogen** structured JSON output (since Decisions API returns 404; keep interface identical so swapping Decisions API later is a 1-function change).
     - `check_contradiction(edit_instruction: str, prior_scenes: list[SceneState]) -> tuple[bool, str]`
     - `filter_affected_scenes(edit_instruction: str, later_scenes: list[SceneState]) -> list[int]`
     - `extract_vocal_emotion(audio_path: str) -> dict` (raw audio -> `vocal_emotion`, `lighting`, `camera_shake`, `transcript`)
     - `inspect_tail_frame(tail_image_path: str) -> str` (Video-in -> `environment_notes`)
     - `write_repair_constraint(scene: SceneState, failed_qa: list[QAResult]) -> str`
  2. `neon_director.py`:
     - `plan_scenes(instruction: str, base_scenes: list[SceneState], forked_from_scene: int, props_registry: dict) -> list[SceneState]` using **Gemini 4 Neon** structured output matching `schema.json`.
  3. `state_tree.py`:
     - Persist branches (`branch_v1`, `branch_v2`, ...) to disk JSON under `backend/assets/branches/` so Branch 1 cache survives restarts.
- **T2 (13:15 – 14:20)**: Wire P1 functions into `backend/main.py` (coordinate via `main` merge) and pre-generate + cache `branch_v1` (3-4 scenes).
- **T3 (14:20 – 15:25)**: Collect 15-20 test rewrites, annotate 20 clips for `judge()` accuracy calibration, and run `eval/run_eval.py` to output failure curve (1->4 actors, 2->8 scenes) + Baseline comparison table.
