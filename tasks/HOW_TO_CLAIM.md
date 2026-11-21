# How Teammates' Agents Claim Tasks as Subagents

Each teammate clones `https://github.com/ChiriSakura/Gemini-Hackthon.git`, opens it in Antigravity (with the **Gemini API** plugin installed), sets up `.env` from `.env.example` (never commit `.env`), and sends **their prompt** below to their agent (or Master dispatches via `submit_task`).

---

## Prompt for Teammate 2 Agent (`worker-1` / P2: Media, Props & Omni Flash)

> Copy and paste this exact prompt to Teammate 2's agent:

```text
You are Subagent worker-1 (P2: Media Pipeline, Props & Omni Flash Video Rendering) reporting to the Master Coordinator defined in AGENTS.md.
First, read AGENTS.md, Design.md, README_KICKOFF.md, shared/schema.json, tasks/BOARD.md, and tasks/P2_media.md, and load the gemini-api skill if installed.
Create and switch to git branch `feat/w1-media` from `main`.
Strictly ONLY modify files inside `backend/media/`, `backend/assets/`, and `tasks/status_P2.json`. Do NOT modify `shared/`, `backend/director/`, or `frontend/`.
Import all model IDs & thresholds from `backend/config.py`, log all function & GenAI calls via `backend/logger.py` (stripping inline binary data), and follow the header/docstring rules in Design.md.
Start immediately with Milestone T0 (deadline 12:25): use `shared/assets/lia.png` + `shared/assets/nyx.png` to benchmark one Omni Flash 360p 4s clip and record exact latency in `tasks/status_P2.json` (if >40s, alert Master immediately to trim story to 3 scenes). Then implement `backend/media/prop_maker.py` and `backend/media/video_renderer.py`.
```

---

## Prompt for Teammate 3 Agent (`worker-2` / P3: Three.js 3D Dual-Camera Stage & UI)

> Copy and paste this exact prompt to Teammate 3's agent:

```text
You are Subagent worker-2 (P3: Three.js 2.5D Dual-Camera Stage & Frontend UI) reporting to the Master Coordinator defined in AGENTS.md.
First, read AGENTS.md, Design.md, README_KICKOFF.md, shared/schema.json, shared/mock_branch_1.json, shared/mock_branch_2.json, tasks/BOARD.md, and tasks/P3_frontend.md.
Create and switch to git branch `feat/w2-frontend` from `main`.
Strictly ONLY modify files inside `frontend/` and `tasks/status_P3.json`. Do NOT modify `shared/` or `backend/`.
Use the transparent PNGs already placed in `frontend/public/assets/actors/lia.png`, `nyx.png`, and `props/crystal_vase.png`, and keep types synced with `frontend/src/types/schema.ts`.
Start immediately with Milestone T0/T1 (deadline 13:15, fallback check at 13:05): build the Three.js 3x3 2.5D stage with Camera A (front wide) & Camera B (45-deg close-up), `cold_blue` rain + `warm_gold_snow` particles, `.webm` `captureStream()` export (if blocked at 13:05, fall back to start+end PNG keyframes), the 3s "Hold to Direct (Cam+Mic)" `.webm` button, Clapperboard QA strip, and side-by-side branch comparison. Update `tasks/status_P3.json` as you progress.
```
