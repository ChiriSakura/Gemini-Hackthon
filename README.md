# StageState — Time-Travel Dual-Camera AI Stage Director

> **Gemini 4 NYC Hackathon — Track: Element 119**
> Repository: `https://github.com/ChiriSakura/Gemini-Hackthon.git`

## What We Built Today
- **Dual-Camera 2.5D Three.js Stage**: A 3x3 lit stage filmed simultaneously by Camera A (front wide) and Camera B (45° close-up), rendering 2.5D transparent actor portraits & props with `<1s` live updates.
- **Time-Travel Story Branching (`Gemini 4 Neon`)**: Rewrite any past scene `k` via voice (raw audio emotion), webcam physical object, or text; scenes `1..k-1` remain untouched while affected future scenes re-simulate and branch side-by-side.
- **Concurrent Dual-Take Video Rendering (`Omni Flash`)**: Parallel `360p/4s` video synthesis anchored on each scene's own 2.5D preview start frame + locked character sheets (`never` chaining from previous clip tail frames) with native audio.
- **Unified `judge()` & Auto-Repair (`Gemini 4 Hydrogen` + Decisions API)**: Handles contradiction checks, affected-scene dependency filtering, dual-take scoring/selection, tail-frame `environment_notes` observation, and corrective prompt retries.

## Multi-Agent Setup
See [`AGENTS.md`](AGENTS.md), [`Design.md`](Design.md), [`tasks/BOARD.md`](tasks/BOARD.md), and [`tasks/HOW_TO_CLAIM.md`](tasks/HOW_TO_CLAIM.md).

## Quick Start (Backend)
```bash
cp .env.example .env
# Fill GEMINI_API_KEY in .env (NEVER commit .env!)
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

## Evaluation Results (Live Empirical Benchmark)

Generated via [`eval/run_eval.py`](eval/run_eval.py) and [`eval/stress_test.py`](eval/stress_test.py) using **Gemini 4 Neon**, **Gemini 4 Hydrogen**, and **Decisions API (`models/gemini-decision-1` v1beta)**:

### 1. Stateless Text Baseline vs. StageState (4 Scenes)
| System | `judge()` Score | All Checks Passed | Planning Latency |
| :--- | :---: | :---: | :---: |
| Stateless Text Chaining Baseline | `0.564` | ❌ False | `29.16s` |
| **StageState (WorldState + Rule Memory)** | **`0.901 – 0.952`** | ✅ **True** | **`6.90s – 9.65s`** |

### 2. Failure & Scaling Curves
- **Actor Scaling (1 → 4 Actors, 4 Scenes)**: `1 actor: 0.885` → `2 actors: 0.855` → `3 actors: 0.820` → `4 actors: 0.820` (2-character `lia + nyx` cast balances visual distinctiveness with Omni Flash reference limits).
- **Scene Scaling (2 → 8 Scenes)**: `2 scenes: 0.947` → `4 scenes: 0.901–0.952` → `6 scenes: 0.699 (with rule memory prompt density noise, caught by judge()) vs 0.937` → `8 scenes: 0.946`. Hard structural invariants (`invariant_passed`) stayed `100% True` across all runs, while `judge()` honestly caught regressions below `0.75`.
- **Localized Rewrite Savings**: `66.7%` of scenes skipped (`needs_rerender=False`) on localized Scene 2 edits via `filter_affected_scenes()`.
- **Omni Flash 360p 4s Latency**: `18.64s` generation (`22.65s` with I/O; `25.47s` parallel dual-take).
