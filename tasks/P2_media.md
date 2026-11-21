# Task Spec — Subagent P2 (`worker-1`): Media Pipeline, Props & Omni Flash

- **Branch**: `feat/w1-media`
- **Owned Paths**: `backend/media/**`, `backend/assets/**`, `tasks/status_P2.json`
- **Reference Assets**: `shared/assets/lia.png`, `shared/assets/nyx.png`, `shared/assets/crystal_vase.png`, `shared/assets/stage_concept_split.png`

## Critical Design Update (12:25)
1. **Decisions API is LIVE (`v1beta` `gemini-decision-1`, ~0.64s/call)!** `backend/director/hydrogen_judge.py` now implements `judge()` and `score_clip_alignment()` via `v1beta` Decisions API (with Hydrogen fallback). Just call `judge()` / `score_clip_alignment()`.
2. **Consistency Anchor Rule**: Start every scene's Omni Flash generation from that scene's **2.5D Three.js preview clip / start-frame screenshot** + locked character sheets (`lia.png`, `nyx.png`) + `config.STYLE_ANCHOR_PROMPT` — **do NOT chain from the previous scene's generated tail frame**, so minor errors never compound.

## Deadlines
- **T0 (12:15 – 12:25) [CRITICAL BENCHMARK]**:
  1. Run a single **Omni Flash** 360p 4s test using `shared/assets/lia.png` + `shared/assets/nyx.png` and log exact wall-clock render time (`omni_flash_latency_sec`) in `tasks/status_P2.json`.
  2. If single clip > 40s, immediately notify Master so everyone trims from 4 scenes to 3 scenes.
- **T1 (12:25 – 13:15)**:
  1. `backend/media/prop_maker.py`: Implement `create_prop()` via `config.MODEL_IMAGE` (solid chroma-green generation + OpenCV/PIL green-screen keying to RGBA PNG under `backend/assets/props/`).
  2. `backend/media/video_renderer.py`: Implement `render_single_camera_shot()` & `render_scene_media()` (`asyncio.gather` dual-camera × dual-take Best-of-2 anchored on 2.5D preview, calling `judge()` & `score_clip_alignment()`, max 2 retries with `write_repair_constraint()`, and `fallback_to_preview=True` if still failing).
- **T2 (13:15 – 14:20)**: End-to-end integration on `POST /api/media/create-prop` & `POST /api/media/render-scene`, plus pre-caching `branch_v1` final videos.
- **T3 (14:20 – 15:25)**: Polish chroma-key edges and visual consistency prompts during stress testing.
