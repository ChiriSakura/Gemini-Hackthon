# Task Spec — Subagent P3 (`worker-2`): Three.js 2.5D Dual-Camera Stage & UI

- **Branch**: `feat/w2-frontend`
- **Owned Paths**: `frontend/**`, `tasks/status_P3.json`
- **Pre-placed Assets**: `frontend/public/assets/actors/lia.png`, `frontend/public/assets/actors/nyx.png`, `frontend/public/assets/props/crystal_vase.png`

## Deadlines
- **T0 (12:15 – 12:25)**: Run `npm install` in `frontend/` and verify Vite dev server starts against `shared/mock_branch_1.json` & `shared/mock_branch_2.json`.
- **T1 (12:25 – 13:15) [13:05 FALLBACK GATE]**:
  1. Build Three.js **3x3 grid stage** + `cold_blue` (rain) & `warm_gold_snow` (golden snow) particle effects + **Camera A (front wide)** & **Camera B (45-deg close-up)** dual canvases using 2.5D billboard sprites (`lia.png`, `nyx.png`, `crystal_vase.png`).
  2. Export 4s `.webm` previews via `canvas.captureStream()` + `MediaRecorder`. **13:05 Gate**: if `.webm` export is unreliable, immediately switch to exporting start + end PNG frames for Omni Flash first/last-frame mode.
  3. Minimal Stage-First UI (NO dashboard):
     - Unified **Hold to Direct (Cam + Mic 3s `.webm`)** button + text fallback input -> `POST /api/director/fork`.
     - Dual-camera player (3D Preview / AI Render toggle + Old Branch vs. New Branch side-by-side) + compact **Clapperboard QA strip**.
     - 4-scene Story Branch Tree.
- **T2 (13:15 – 14:20)**: Connect to live FastAPI (`http://localhost:8000`) and verify end-to-end rewrite + render + fallback preview badge.
- **T3 – T5 (14:20 – 17:00)**: Polish UI, record fallback demo video at 16:00, and record final 1-minute submission video by 16:45.
