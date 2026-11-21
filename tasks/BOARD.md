# StageState Hackathon Task Board & Deadlines

> **Current Start**: 12:10 PM EST | **Hard Submission**: 17:00 PM EST | **MVP Target**: 14:20 PM EST (leaves **2h 40m** for debugging, eval curves, and video)

## Milestone Deadlines

| Phase | Deadline | Window | Gate / Deliverable | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **T0: Setup & Probe** | **12:25** | 15 min | Clone repo, set `.env`, install Gemini skill; **P2 benchmarks Omni Flash 360p/4s latency + asks staff about Decisions API** | All + P2 |
| **T1: Module MVP** | **13:15** | 50 min | P1: Neon rewrite + Hydrogen `judge()` + state tree; P2: 2 actor portraits + `create_prop()` + `render_clip()`; P3: Three.js 3x3 2.5D dual-cam + `.webm` export *(13:05 fallback: start/end PNG)* | P1 / P2 / P3 |
| **T2: Full Integration** | **14:20** | 65 min | End-to-end live loop: Voice/Webcam/Text -> `<1s` 3D preview -> concurrent affected-scene render -> side-by-side old/new branch + Clapperboard QA; **Cache Branch 1 to disk** | All |
| **T3: Trial, Eval & Fix** | **15:25** | 65 min | 15-20 external test prompts, 20 human calibration labels for `judge()`, failure-curve & baseline comparison charts in `README.md`, fix visual glitches | P1 + All |
| **T4: Feature Freeze** | **16:15** | 50 min | **FREEZE ALL NEW FEATURES at 15:25.** Stability fixes only + dry-run Live Demo script + record fallback demo video | All |
| **T5: Final Video & Submit** | **17:00** | 45 min | Record 1-min submission video (first 10s: dual-cam + past rewrite twist; last 12s: failure curve + baseline table), add all teammates to submission | P3 + All |

## Agent Status Files
- P1: `tasks/status_P1.json`
- P2: `tasks/status_P2.json`
- P3: `tasks/status_P3.json`
