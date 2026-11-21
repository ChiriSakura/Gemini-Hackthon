# How Teammates' Agents Claim Tasks as Subagents

Each teammate clones `https://github.com/ChiriSakura/Gemini-Hackthon.git`, opens it in Antigravity (with the **Gemini API** plugin installed), sets up their local `.env` from `.env.example` (never commit `.env`), and sends **their corresponding prompt** below to their agent.

---

## Prompt for Teammate 2 Agent (Media, Props & Omni Flash — `P2`)

> Copy and paste this exact message to Teammate 2's agent:

```text
You are Subagent P2 (Media Pipeline, Props & Video Rendering) working under the Main Coordinator defined in AGENTS.md.
First, read AGENTS.md, Design.md, schema.json, tasks/BOARD.md, and tasks/P2_media.md, and load the gemini-api skill if installed.
Create and switch to git branch `feat/p2-media` from `main`.
Strictly ONLY modify files inside `backend/media/`, `backend/assets/`, and `tasks/status_P2.json`.
All models/settings must be imported from `backend/config.py`, all function & GenAI calls must log via `backend/logger.py` (stripping inline binary data), and every file/function must follow the header/docstring rules in Design.md.
Start immediately with Milestone T0 (deadline 12:25): benchmark Omni Flash 360p 4s single-clip latency and character reference limit, and update `tasks/status_P2.json` as you finish each step.
```

---

## Prompt for Teammate 3 Agent (3D Dual-Camera Stage & UI — `P3`)

> Copy and paste this exact message to Teammate 3's agent:

```text
You are Subagent P3 (Three.js 3D Dual-Camera Stage & Frontend UI) working under the Main Coordinator defined in AGENTS.md.
First, read AGENTS.md, Design.md, schema.json, tasks/BOARD.md, and tasks/P3_frontend.md.
Create and switch to git branch `feat/p3-frontend` from `main`.
Strictly ONLY modify files inside `frontend/` and `tasks/status_P3.json`.
Build against `schema.json` and the FastAPI endpoints in `backend/main.py` (using mock mode until T2 integration).
Start immediately with Milestone T0/T1 (deadline 13:15, fallback check at 13:05): build the Three.js 3x3 2.5D billboard stage with Camera A (front wide) and Camera B (45-deg close-up) + `.webm` captureStream export (if captureStream blocks by 13:05, immediately fall back to start/end PNG keyframes). Update `tasks/status_P3.json` as you progress.
```

---

## How to Sync Work Without Merge Conflicts
1. Each agent works on its own branch (`feat/p1-director`, `feat/p2-media`, `feat/p3-frontend`) and touches ONLY its owned directory + its own `tasks/status_P<N>.json`.
2. At **13:15 (T1)** and **14:20 (T2)**, push your branch (`git push -u origin feat/p<N>-...`) and merge into `main`. Because directories and status files are 100% disjoint, merges will be clean fast-forwards/auto-merges!
