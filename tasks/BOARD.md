# StageState Hackathon Task Board & Deadlines

> **Start**: 12:10 PM EST | **V2 Core Shipped**: 15:21 EST | **V3 Speed & Fidelity Sprint**: 15:28–16:15 EST | **Hard Submission**: 17:00 PM EST

## Milestone Status (Updated 15:28 EST — V3 Speed & Visual Fidelity Sprint ACTIVE)

| Phase | Deadline | Status | Gate / Deliverable | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **T0–T3: MVP, Parallel Cache & Eval** | **14:20** | ✅ DONE | Core Neon + Hydrogen + Decisions + Omni Flash + Three.js 2.5D stage, 4 cached branches, empirical benchmarks (`README.md` + `metrics_summary_for_submission.json`) | All |
| **V2: Infinite Living Sculpture UI & Fixed Room** | **15:25** | ✅ DONE (`1b7566f`) | Minimalist Home UI (BE/HE removed), bottom-right `CameraSelfView` PiP, `/api/director/perceive` + `/extend`, fixed empty conservatory room refs (`5/5 @ 0.80`), parallel upload (`2.9x`), `catch_from_camera` conditional default `place` | All |
| **V3: Visual Fidelity & Sub-Second Speedup** | **16:00** | 🔄 ACTIVE | `W1-V3-01..05` (exact captured color/type lock + `< 0.3s` template tint for seen categories + lean `384px` icon mode for unseen objects + `prop_descriptions` video injection); `W2-V3-04..06` (optimistic instant stage prop preview, 1-click Retry Render on transient failures, V2/V3 metrics in `EvalModal`) | P1 / P2 / P3 |
| **T4: Live Booth Rehearsal & User Test** | **16:40** | ⏳ NEXT | Spin up local `uvicorn` + Vite dev; live test spontaneous voice/text + physical yellow drink can / black smartphone / unseen objects on camera | All |
| **T5: Final Video & Submit** | **17:00** | ⏳ READY (`hook_10s` + `eval_12s` + `55.5s narrated` all H.264+AAC) | Final preflight check (`preflight_check` + `npm run verify`), public repo & Devpost teammate check, submit before 17:00 EST | P3 + All |

## Active Task Queues & Status Files
- Master / P1 Status: `tasks/status_P1.json` (orchestration, `shared/schema.json`, `backend/main.py`, `backend/director/**`, `eval/**`)
- Worker-1 (P2) Queue & Status: `tasks/queue_W1.json` -> `tasks/status_P2.json` (`W1-V3-01..05`, `W1-V2-B3/B4`)
- Worker-2 (P3) Queue & Status: `tasks/queue_W2.json` -> `tasks/status_P3.json` (`W2-V3-04..06` optimistic instant preview, retry button, `EvalModal` live V2/V3 hydration)
