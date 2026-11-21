# Task Spec — Master / P1: Director Brain, `judge()`, State Tree & Eval

- **Branch**: `main` (or `feat/p1-director`)
- **Owned Paths**: `backend/director/**`, `eval/**`, `shared/**`, `backend/main.py`, `backend/config.py`, `backend/logger.py`, `backend/schemas.py`, `tasks/status_P1.json`

## Deadlines
- **T0 (12:15 – 12:25)**: Verify FastAPI server (`uvicorn backend.main:app --reload`) and `.env` `GEMINI_API_KEY`.
- **T1 (12:25 – 13:15)**: Implement live Gemini 4 calls using `backend/director/prompts.py`:
  1. `hydrogen_judge.py`:
     - `judge()` via Gemini 4 Hydrogen structured output matching `JudgeResult` (swappable to Decisions API).
     - `parse_director_input()` ingesting 3s `.webm` audio+video into `DirectorInputParsed`.
     - `check_contradiction()`, `filter_affected_scenes()`, `inspect_tail_frame()`, `write_repair_constraint()`.
  2. `neon_director.py`:
     - `plan_scenes()` via Gemini 4 Neon structured output matching `shared/schema.json`.
  3. Verify CLI / `curl` test forks `branch_v1` from scene 2 into `branch_v2`.
- **T2 (13:15 – 14:20)**: Merge `feat/w1-*` and `feat/w2-*` into `main`, verify full-stack loop, cache `branch_v1`.
- **T3 (14:20 – 15:25)**: Collect 15-20 external prompts, annotate 20 clips for `judge()` accuracy, and run `eval/run_eval.py` for failure curves & Baseline comparison.
