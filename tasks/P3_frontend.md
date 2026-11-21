# Task Spec — Subagent P3: Three.js 3D Dual-Camera Stage & UI

- **Branch**: `feat/p3-frontend`
- **Owned Paths**: `frontend/**`, `tasks/status_P3.json`
- **Status File**: Update `tasks/status_P3.json` at every milestone.

## Deadlines
- **T0 (12:10 – 12:25)**: Initialize Vite + React + TypeScript + Three.js (`npm install`) in `frontend/`, verify `frontend/src/types/schema.ts` matches `schema.json`.
- **T1 (12:25 – 13:15) [FALLBACK CHECK AT 13:05]**:
  1. Build Three.js **3x3 grid stage** + dynamic lighting (`warm_gold`, `cold_blue`, `crimson_alert`) + **Camera A (front wide)** & **Camera B (45-deg side close-up)** dual canvases.
  2. Render **2.5D billboard sprites** (`PlaneMesh`) for actors and transparent PNG props (`lerp` movement + jump parabola + `camera_shake`).
  3. Export 4s `.webm` preview clips from Canvas A & Canvas B using `canvas.captureStream()` + `MediaRecorder` and upload to backend.
     - **13:05 Fallback Gate**: If `.webm` captureStream does not work reliably by 13:05, immediately switch to exporting **start + end PNG screenshots** (`canvas.toDataURL('image/png')`) for Omni Flash first/last-frame mode.
  4. Minimal Stage-First UI (NO complex analytics dashboard):
     - Top: Dual-camera player (toggle **3D Preview / AI Render** and **Old Branch vs. New Branch** side-by-side) + compact **Clapperboard QA strip** showing `judge()` pass/fail & confidence.
     - Bottom: 3-4 Scene Story Branch Tree + Voice Record button (sends raw audio blob) + Webcam Snapshot Prop button + Text Rewrite input.
- **T2 (13:15 – 14:20)**: Connect frontend directly to FastAPI (`http://localhost:8000/api/...`), verify `<1s` 3D update + async AI render polling + branch comparison.
- **T3 (14:20 – 15:25)**: Polish stage visuals, pixel-art 3D low-res preview toggle, and ensure demo cache loads instantaneously.
- **T4-T5 (15:25 – 17:00)**: Record fallback demo video at 16:00 and final **1-minute pitch/demo video** by 16:45 (first 10s: dual-cam + live rewrite twist; last 12s: failure curve + baseline table).
