# StageState — Time-Travel Dual-Camera AI Stage Director

> **Gemini 4 NYC Hackathon — Track: Element 119** (Video, audio, and images as core inputs and outputs)
> Repository: `https://github.com/ChiriSakura/Gemini-Hackthon.git`

## What We Built Today
- **Dual-Camera 2.5D Three.js Stage**: A 3×3 lit rooftop glass conservatory filmed simultaneously by **Camera A (front wide)** and **Camera B (45° diagonal close-up)**, rendering 2.5D transparent character & prop cutouts with `<1s` real-time updates, pixel-art mode, actor diff halos, and zero-drift dual-cam video sync.
- **Time-Travel Story Branching (`Gemini 4 Neon`)**: Rewrite any past scene `k` via 3-second webcam+mic `.webm` (native vocal emotion + director gesture + physical object) or text; scenes `1..k-1` stay untouched (`100%` prefix preservation) while causally affected future scenes re-simulate under hard structural invariants (grid bounds, single prop holder, object permanence) with automatic retry.
- **Physical Camera-to-Stage Props (`Gemini 3.1 Flash Image / Nano Banana 2.1`)**: Hold up any physical desk object to the webcam -> extract clear frame -> restyle to conservatory art style on chroma green -> border-connected floodfill keying & rim despill -> bottom-flush RGBA sprite persisted across scenes (`100%` persistence, `0` green-spill pixels).
- **Concurrent Best-of-2 Video Synthesis (`Omni Flash`)**: Parallel `360p/4s` H.264 + 48kHz stereo AAC video generation anchored on each scene's own 2.5D preview + locked character sheets (`never` chaining from prior clip tail frames to prevent drift).
- **Unified `judge()` & Auto-Repair (`Decisions API v1beta` + `Gemini 4 Hydrogen`)**: Uses all three `models/gemini-decision-1` primitives (`noul`, `choice`, `score`) for contradiction checks, intent routing, affected-scene dependency filtering, dual-take selection, cross-camera consistency QA, and automatic Hydrogen repair prompts (with 2.5D preview fallback if retries exhaust).

## Architecture & Multi-Agent Workflow
See [`AGENTS.md`](AGENTS.md), [`Design.md`](Design.md), [`tasks/BOARD.md`](tasks/BOARD.md), and [`tasks/HOW_TO_CLAIM.md`](tasks/HOW_TO_CLAIM.md).

## Quick Start

### 1. Backend (FastAPI + Gemini 4)
```bash
cp .env.example .env
# Fill GEMINI_API_KEY in .env (NEVER commit .env!)
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

### 2. Frontend (React + Three.js 2.5D Dual-Camera Stage)
```bash
cd frontend
npm install
npm run dev
# One-command 5-gate verification (tsc + vite build + 0-CJK locale + asset audit + helper tests):
npm run verify
```

### 3. Pre-Flight Verification (Backend)
```bash
python -m backend.media.preflight_check
python -m backend.media.test_media_smoke
python -m backend.media.test_webm_prop
```

---

## Evaluation Results (Live Empirical Benchmarks)

All metrics below are generated from live logged Gemini 4 runs (`eval/stress_report.json`, `eval/latest_report.json`, and `backend/assets/eval_*.json`), also accessible at `GET /api/eval/summary`.

### 1. Stateless Text Baseline vs. StageState (4 Scenes)
| System | `judge()` Score | All Checks Passed | Planning Latency |
| :--- | :---: | :---: | :---: |
| Stateless Text Chaining Baseline | `0.564` | ❌ False | `29.16s` |
| **StageState (WorldState + Rule Memory)** | **`0.901 – 0.952`** | ✅ **True** | **`6.90s – 9.65s`** |

### 2. Decisions API (`models/gemini-decision-1`, `v1beta`) Trifecta Calibration
| Primitive | Use Case | Live Sample Size | Accuracy / Metric | Avg Latency | Source |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **`noul`** (Yes/No) | Video & tail-frame QA calibration | 20 clips | **95.0% Acc** (`92.9%` TPR, `100%` TNR) | `1.37s` | `calibration_20_clips.json` |
| **`choice`** (5-way) | Bilingual (EN+ZH) rewrite intent routing | 15 directives | **100.0% Acc** (`15/15`) | `1.31s` | `eval_decisions_multimodal.json` |
| **`score`** (1–5) | Best-of-2 clip-to-state alignment rubric | 12 `.mp4` pairs | **1.000 AUC** (`0.874` vs `0.088`, `+0.786` margin) | `0.66s` | `eval_decisions_multimodal.json` |

### 3. Multimodal Consistency, Best-of-2 Selection & Efficiency
| Metric | Empirical Result | Details |
| :--- | :---: | :--- |
| **Dual-Camera Cross-View Consistency** | **92.9%** (`26/28`) | 7 Cam A + Cam B scene pairs audited via `models/gemini-decision-1` (`eval_dual_cam_stats.json`) |
| **Best-of-2 Take Selection Lift** | **72.7% → 97.0%** | Across 33 live shots (`eval_media_stats.jsonl`), avg score rose `0.684 → 0.835`; Take 2 beat Take 1 on 12/33 shots; 5/6 retries recovered |
| **Native Audio-Visual Alignment** | **100.0%** (`14/14` + `4/4` TNR) | Direct `.mp4` (video + 48kHz AAC audio) check via Decisions API (`1.51s` avg; `eval_audio_mood.py`) |
| **Multi-Scene Prop Persistence & Keying** | **100.0%** (`6/6` clips, `11/11` props) | `star_token` & `crystal_vase` persist across Scenes 2–4; all 11 RGBA props bottom-flush with `0` green-spill pixels |
| **4-Branch Continuity Matrix** | **87.5%** (`7/8`) | Cross-branch verification across `branch_v1..branch_v4` (`eval_branch_matrix.json`); honestly flags BE/HE edge case (`0.424`) |
| **Localized Rewrite Skip Savings** | **66.7%** (`18.8%` across 4 full forks) | `filter_affected_scenes()` skips untouched scenes (`$0.96` & `76.5s` saved across 4 cached branches; `eval_cache_savings.json`) |
| **360p vs 720p Omni Flash Trade-off** | **1.51×–1.78× faster · 3.5× cheaper** | 360p: `17.1s–18.6s`, `1.53MB`, `$0.08/clip` vs 720p: `25.8s–33.2s`, `3.05MB`, `$0.28/clip` (**71.4% cost savings**, `eval_720p_vs_360p.json`) |
| **Concurrent Burst & Failover Resilience** | **0 deadlocks · 4/4 failover pass** | Simultaneous `create_prop` + dual-cam S3 render completed in `49.56s` (`QA 0.902`, `0` retries); corrupt `.webm` cleanly falls back to PNG/2.5D (`eval_failover_rehearsal.json`) |
| **Pre-Freeze Asset & Codec Gate** | **28/28 H.264+AAC · 26/26 Blueprints** | All 4 cached branches (`branch_v1..v4`) HTTP 200 & 0 CJK; 28/28 MP4s H.264 + AAC audio; 11/11 RGBA props 0 green halo (`preflight_report.json`) |

### 4. Honest Failure & Scaling Curves (Where Quality Drops)
- **Actor Scaling (1 → 4 Actors, 4 Scenes)**: `1 actor: 0.885` → `2 actors: 0.855` → `3 actors: 0.820` → `4 actors: 0.820`. Locking the cast to **1 human (`lia`) + 1 cat (`nyx`)** prevents character identity mixing while respecting Omni Flash reference limits.
- **Scene Scaling (2 → 8 Scenes)**: `2 scenes: 0.947` → `4 scenes: 0.901–0.952` → `6 scenes: 0.699` (rule-memory prompt density regression caught by `judge()`, vs `0.937` without extra rules) → `8 scenes: 0.946`. Hard structural invariants stayed `100% True` across all runs.
- **Layer-3 Blueprint Start-Frame A/B Ablation (`eval_blueprint_anchoring.json`)**: Anchoring directly on a flat schematic PNG boosted raw spatial consistency (`0.256 → 0.325`) and scene QA (`0.859 → 0.875`), but lowered angle-separation/depth (`0.614 → 0.465` avg) when fed as a rigid frame-0; therefore 2.5D blueprints shine as instant `<1s` visual previews & spatial references alongside locked anime character sheets.

## Pre-Built Submission & Pitch Assets
- `backend/assets/finals/pitch_demo_reel_1min.mp4` — 50s HD H.264+AAC composite showcase reel
- `backend/assets/finals/demo_comparison_be_vs_he.mp4` — Side-by-side BE vs. HE comparison reel with native AAC audio
- `backend/assets/submission/hook_10s_dualcam_twist.mp4` — 10s HD dual-cam + past-rewrite quad-split opener
- `backend/assets/submission/eval_charts_board.png` & `eval_dashboard_12s.mp4` — 1920×1080 dark-theme empirical benchmark board & 12s outro
- `backend/assets/showcase_storyboard_strip.png` — 2×4 BE vs. HE storyboard contact sheet
