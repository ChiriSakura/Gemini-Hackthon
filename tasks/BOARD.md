# StageState Hackathon Task Board & Deadlines

> **Start**: 12:10 PM EST | **Feature Freeze**: 15:25 PM EST (Reached early at **14:20 EST!**) | **Hard Submission**: 17:00 PM EST

## Milestone Status (Updated 14:25 EST — P2 & P3 FEATURE FROZEN, 7+6 Gates ALL GREEN!)

| Phase | Deadline | Status | Gate / Deliverable | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **T0: Setup & Probe** | **12:25** | ✅ DONE | Repo live, `.env` verified, Gemini 4 model IDs + `Decisions API v1beta` (`0.64s`) + Omni Flash (`18.64s`) verified | All |
| **T1: Module MVP** | **13:15** | ✅ DONE (12:40) | P1 (`Neon` + `Decisions/Hydrogen`), P2 (`prop_maker` + `video_renderer`), P3 (`Three.js` 2.5D stage) merged into `main` | All |
| **T2: Parallel Batch & Cache** | **14:15** | ✅ DONE (13:38) | 4 cached English branches (`branch_v1..branch_v4`), 28/28 H.264+AAC MP4s, 11/11 zero-halo RGBA props, 26/26 2.5D blueprints, `rule_memory.py` + hard structural invariants + `eval/` | P1 / P2 / P3 |
| **T3: Trial, Eval & Hardening** | **15:25** | ✅ DONE (14:20) | All live empirical benchmarks published (`README.md` + `metrics_summary_for_submission.json`), 7 backend suites + 6 frontend `npm run verify` gates ALL GREEN (including Windows Node v24 `smoke_preview.mjs` 10/10 HTTP 200 & 4/4 failover rehearsal) | All |
| **T4: Feature Freeze & Rehearsal** | **16:15** | 🔄 ACTIVE | **P2 & P3 FEATURE FROZEN (`queue_W1.json` & `queue_W2.json` locked).** Live booth rehearsal (physical prop snap + contradiction check + BE vs HE split-screen compare) + fallback recording. Only critical bugfixes if rehearsal uncovers one. | All |
| **T5: Final Video & Submit** | **17:00** | ⏳ READY (`hook_10s` + `eval_12s` + `50s pitch reel` all H.264+AAC) | Record/assemble final 1-min submission video (0:00-0:10 dual-cam + twist; 0:10-0:48 live rewrite + keepsake + QA; 0:48-1:00 failure curves & baseline table), confirm public repo + all teammates added on submission page. | P3 + All |

## Task Queues & Status Files
- Master / P1 Status: `tasks/status_P1.json`
- Worker-1 (P2) Queue & Status: `tasks/queue_W1.json` (FROZEN) -> `tasks/status_P2.json`
- Worker-2 (P3) Queue & Status: `tasks/queue_W2.json` (FROZEN) -> `tasks/status_P3.json`
