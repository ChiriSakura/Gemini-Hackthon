# StageState — Architecture & Design Document

> Track: **Element 119** (Video, audio, and images as core inputs and outputs)
> Repository: `https://github.com/ChiriSakura/Gemini-Hackthon.git`

## 1. Product Summary

**StageState** is a dual-camera, state-persistent 3D stage director driven by single-sentence multimodal commands (voice with emotion, live camera objects, or text).
When a user or judge rewrites any past scene `k`, StageState preserves scenes `1..k-1`, checks for logical contradictions, re-simulates only the affected future scenes on a 3D Three.js stage (`<1s` dual-camera preview), and concurrently re-renders stylized video clips with dual-take quality selection and automatic repair.

## 2. Why Multimodal (Answering Element 119)

1. **Raw Audio Emotion (`vocal_emotion`)**: Hydrogen ingests raw audio (not plain ASR text) to detect vocal intensity, tempo, and emotion (e.g., angry shout vs. trembling whisper), mapping directly to 3D stage lighting, camera shake, and Omni Flash audio/SFX mood.
2. **Raw Camera Props (`create_prop`)**: Judges can hold any physical object or hand-drawn sketch to the webcam and say *"give this to her as a shield"*; the image model converts it into a stage-style transparent prop carried across subsequent scenes.
3. **Video-in -> Video-out Tail Feedback**: After Omni Flash renders scene `k`, Hydrogen inspects the tail frame and writes emergent visual details (e.g., `floor_has_water_puddle`) into `WorldState.environment_notes` for scene `k+1`.
4. **Dual-Camera World Consistency**: Camera A (front wide) and Camera B (45-deg side close-up) film the exact same Three.js 3x3 grid stage, proving there is a coherent underlying 3D world rather than disconnected video generations.

## 3. End-to-End Pipeline

1. **Input**: Text / raw audio / webcam snapshot + target `scene_id = k`.
2. **Contradiction Check (`judge()`)**: Ask whether the edit contradicts established facts before scene `k`. If yes, return conflict warning for confirmation instead of blindly generating.
3. **State Planning (`Gemini 4 Neon`)**: Re-simulate scenes `k..N` into structured `SceneState[]` JSON.
4. **Affected-Scene Filtering (`judge()`)**: For each scene `j > k`, ask `"Does scene j depend on the edited fact?"`; only mark `was_regenerated = true` for `Yes` scenes.
5. **Instant Dual-Camera 2.5D Preview (`Three.js`)**: Render 2.5D billboard sprites (actors + transparent PNG props) on a 3x3 lit grid from Camera A & Camera B (`<1s`), exporting `.webm` clips (fallback: start+end PNG keyframes).
6. **Concurrent Dual-Take Rendering (`Omni Flash`)**: Run `asyncio.gather` across affected scenes (`360p`, `4s` per clip for speed), generating 2 takes per shot anchored on **each scene's own 2.5D Three.js preview clip/start-frame** + locked character sheets (`lia.png`, `nyx.png`) + fixed style prompt (never chained from previous generated tail frames, preventing error drift).
7. **Unified Quality Judge & Auto-Repair (`Decisions API v1beta` + `Hydrogen`)**:
   - Primary fast judge: **Decisions API (`gemini-decision-1` via `v1beta`, ~0.64s per call)** supporting `noul` (yes/no probability), `choice` (rewrite intent routing), and `score` (1-5 take grading), with automatic fallback to **Gemini 4 Hydrogen structured output**.
   - Evaluate both takes on 5-8 yes/no questions plus `score` grading; pick the higher take and flag low-confidence items (`< 0.70`). If both fall below threshold, Hydrogen writes corrective constraints and retries (max 2 retries); if still failing, fall back to the 2.5D stage preview (`fallback_to_preview = true`).
8. **Clapperboard QA & Branch Comparison**: UI displays dual cameras (3D preview vs. AI render, old branch vs. new branch side-by-side) plus a minimal clapperboard QA strip showing `judge()` scores and auto-retries.

## 4. Engineering & Coding Standards (Hackathon Guidelines)

- **Never commit secrets**: `.env` is gitignored; keys live only in local `.env`.
- **One file per feature**: Split director, judge, prop maker, video renderer, and UI components into focused single-purpose modules.
- **Documentation**: Every module starts with a detailed header comment describing its feature and use cases; every function has a clear docstring.
- **Centralized config**: All model names, thresholds, timeouts, and paths live in `backend/config.py`.
- **Mandatory GenAI & Call Logging**: Every function call and every Gemini/GenAI call must log parameters, model name, prompt, config, and output at `INFO` level via `backend/logger.py`, stripping inline binary/base64 payloads.
