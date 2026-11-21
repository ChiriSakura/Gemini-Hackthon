# StageState Hackathon Task Board & Deadlines

> **Current Start**: 12:10 PM EST | **Hard Submission**: 17:00 PM EST | **MVP Target**: 14:20 PM EST (leaves **2h 40m** for debugging, eval curves, and video)

## Milestone Deadlines (Updated 12:42 — T0 & T1 DONE AHEAD OF SCHEDULE!)

| Phase | Deadline | Window | Gate / Deliverable | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **T0: Setup & Probe** | **12:25** | ✅ DONE | Repo live, `.env` verified, Gemini 4 model IDs + `Decisions API v1beta` (`0.66s`) + Omni Flash (`18.64s`) verified | All |
| **T1: Module MVP** | **13:15** | ✅ DONE (12:40) | P1 (`Neon` + `Decisions/Hydrogen`), P2 (`prop_maker` + `video_renderer`), P3 (`Three.js` 2.5D stage) all merged cleanly into `main` | All |
| **T2: Parallel Batch & Cache** | **14:15** | 🔄 IN PROGRESS | Execute `tasks/queue_W1.json` (4 tasks: Branch 1/2 cache, H.264 check, `score`, multimodal Decision bench) & `tasks/queue_W2.json` (4 tasks: live integration, conflict UI, pixel toggle, single-screen polish); P1 builds `rule_memory.py` + `eval/` | P1 / P2 / P3 |
| **T3: Trial, Eval & Fix** | **15:25** | 70 min | 15-20 external test prompts, 20 human calibration labels for `judge()`, failure-curve & baseline comparison charts in `README.md`, fix visual glitches | P1 + All |
| **T4: Feature Freeze** | **16:15** | 50 min | **FREEZE ALL NEW FEATURES at 15:25.** Stability fixes only + dry-run Live Demo script + record fallback demo video | All |
| **T5: Final Video & Submit** | **17:00** | 45 min | Record 1-min submission video (first 10s: dual-cam + past rewrite twist; last 12s: failure curve + baseline table), add all teammates to submission | P3 + All |

## Task Queues & Status Files
- Master / P1 Status: `tasks/status_P1.json`
- Worker-1 (P2) Queue & Status: `tasks/queue_W1.json` -> `tasks/status_P2.json`
- Worker-2 (P3) Queue & Status: `tasks/queue_W2.json` -> `tasks/status_P3.json`
