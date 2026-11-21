# Task Spec — Subagent P2: Media Pipeline, Props & Omni Flash

- **Branch**: `feat/p2-media`
- **Owned Paths**: `backend/media/**`, `backend/assets/**`, `tasks/status_P2.json`
- **Status File**: Update `tasks/status_P2.json` at every milestone.

## Deadlines
- **T0 (12:10 – 12:25) [CRITICAL BENCHMARK]**:
  1. Test a single **Omni Flash** video-edit call (`360p`, `4s`, `16:9`) and measure exact wall-clock render time (`render_seconds`) + max character reference images (`2` vs `3`).
  2. Ask hackathon Google staff if `Decisions API` can be enabled on the project.
  3. Generate and save 2 locked protagonist portraits (`heroine.png`, `detective.png`) under `backend/assets/actors/` with transparent backgrounds.
  4. Record measured latency in `tasks/status_P2.json` (if single clip > 40s, notify Coordinator to lock story length to 3 scenes instead of 4).
- **T1 (12:25 – 13:15)**:
  1. `backend/media/prop_maker.py`:
     - `create_prop(name_or_prompt: str, camera_frame_bytes: bytes | None = None, style_hint: str = "anime stage prop") -> PropAsset`
     - Uses `config.MODEL_IMAGE` (`3.1 Flash Image` / `3 Pro Image`) on solid `#00FF00` chroma background, removes green screen into transparent PNG, saves to `backend/assets/props/<prop_id>.png`.
  2. `backend/media/video_renderer.py`:
     - `render_clip(scene: SceneState, cam_id: str, preview_path: str, prev_tail_path: str | None, reference_images: list[str], extra_constraint: str = "") -> RenderResult`
     - Launches **2 parallel takes** per shot via `asyncio.gather`, extracts tail frames, calls `judge()` (`backend/director/hydrogen_judge.py`) to pick the higher score, and retries up to `config.MAX_QA_RETRIES` (2) with Hydrogen repair constraint if both fail threshold.
- **T2 (13:15 – 14:20)**: Integration test concurrent rendering across affected scenes + cache `branch_v1` final videos to disk.
- **T3 (14:20 – 15:25)**: Tune green-screen chroma removal, style consistency prompts, and help run evaluation batch.
