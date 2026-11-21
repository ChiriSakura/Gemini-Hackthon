# Task Spec — Subagent P2 (`worker-1`): Media Pipeline, Props & Omni Flash

- **Branch**: `feat/w1-media`
- **Owned Paths**: `backend/media/**`, `backend/assets/**`, `tasks/status_P2.json`
- **Reference Assets**: `shared/assets/lia.png`, `shared/assets/nyx.png`, `shared/assets/crystal_vase.png`, `shared/assets/stage_concept_split.png`

## Deadlines
- **T0 (12:15 – 12:25) [CRITICAL BENCHMARK]**:
  1. Run a single **Omni Flash** 360p 4s test using `shared/assets/lia.png` + `shared/assets/nyx.png` and log exact wall-clock render time (`omni_flash_latency_sec`) in `tasks/status_P2.json`.
  2. If single clip > 40s, immediately notify Master so everyone trims from 4 scenes to 3 scenes.
  3. Confirm with hackathon staff whether `Decisions API` can be enabled (`decisions_api_available`).
- **T1 (12:25 – 13:15)**:
  1. `backend/media/prop_maker.py`: Implement `create_prop()` via `config.MODEL_IMAGE` (solid chroma-green generation + OpenCV/PIL green-screen keying to RGBA PNG under `backend/assets/props/`).
  2. `backend/media/video_renderer.py`: Implement `render_single_camera_shot()` & `render_scene_media()` (`asyncio.gather` dual-camera × dual-take Best-of-2, tail frame extraction, calling P1's `judge()` in `backend/director/hydrogen_judge.py`, max 2 retries with `write_repair_constraint()`, and `inspect_tail_frame()` feedback).
- **T2 (13:15 – 14:20)**: End-to-end integration on `POST /api/media/create-prop` & `POST /api/media/render-scene`, plus pre-caching `branch_v1` final videos.
- **T3 (14:20 – 15:25)**: Polish chroma-key edges and visual consistency prompts during stress testing.
