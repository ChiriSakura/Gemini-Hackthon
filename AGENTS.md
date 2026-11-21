# Multi-Agent Coordinator & Subagent Protocol (`AGENTS.md`)

This repository is orchestrated using a **Main Coordinator + 3 Isolated Roles (P1 / P2 / P3)** workflow for the Gemini 4 Hackathon (**Element 119 — StageState**).

## 1. Directory Ownership (STRICT — Zero Merge Conflict Rule)

Each agent **MUST ONLY edit files inside its assigned directory** and its own status file. Never edit another agent's files or `schema.json` without Coordinator approval.

| Agent Role | Branch Name | Owned Directories / Files | Task Spec |
| :--- | :--- | :--- | :--- |
| **Coordinator (Main Agent)** | `main` | `AGENTS.md`, `Design.md`, `schema.json`, `backend/main.py`, `backend/config.py`, `backend/logger.py`, `backend/schemas.py`, `tasks/BOARD.md` | Orchestration & integration |
| **Subagent P1 (Director & Eval)** | `feat/p1-director` | `backend/director/**`, `eval/**`, `tasks/status_P1.json` | [tasks/P1_director.md](tasks/P1_director.md) |
| **Subagent P2 (Media & Props)** | `feat/p2-media` | `backend/media/**`, `backend/assets/**`, `tasks/status_P2.json` | [tasks/P2_media.md](tasks/P2_media.md) |
| **Subagent P3 (3D Stage & UI)** | `feat/p3-frontend` | `frontend/**`, `tasks/status_P3.json` | [tasks/P3_frontend.md](tasks/P3_frontend.md) |

## 2. Mandatory Coding & Security Rules

1. **NEVER commit API keys or `.env` files.** Only read `GEMINI_API_KEY` from environment variables.
2. **Use the Gemini Skill** (`gemini-api`) whenever writing Gemini 4 (`Neon`, `Hydrogen`, `Omni Flash`, `3.1 Flash Image`) API calls.
3. **Centralized Config**: Import all model IDs, retry counts, resolutions, and thresholds from `backend/config.py`. Never hardcode model strings inside feature files.
4. **Structured Logging**: Wrap or log all feature function calls and all GenAI calls via `backend/logger.py` (`log_function_call` / `log_genai_call`), stripping raw inline image/video/audio bytes.
5. **Docstrings & File Headers**: Start every `.py` / `.ts` / `.tsx` file with a clear multi-line comment explaining the feature and use cases, and add docstrings to all functions.
6. **Decisions API -> `judge()` Abstraction**: Decisions API currently returns `404` on hackathon accounts. All yes/no scoring, take selection, affected-scene filtering, and contradiction checks MUST call the unified `judge()` function in `backend/director/hydrogen_judge.py`.

## 3. Git & Claiming Workflow

1. Run `git checkout -b feat/p<N>-<role>` from `main` (or pull latest `main`).
2. Read `Design.md`, `schema.json`, and your `tasks/P<N>_*.md`.
3. Update `tasks/status_P<N>.json` as you complete milestones (`T0`, `T1`, `T2`, `T3`).
4. Push your branch (`git push -u origin feat/p<N>-<role>`) at every checkpoint so the Coordinator / teammates can merge cleanly into `main`.
