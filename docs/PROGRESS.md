# StageGenesis 3D-First 进度记录

分支：`feat/3d-first`（从 `origin/feat/cam-b-from-a` @ `78bd6be` 切出）

---

## 阶段 0：先有一条每次都能跑通的路

### 已完成改动

1. **导演模型调用超时 + 1 次重试 + 明确错误提示（不返回 500）**
   - `backend/config.py`：新增 `DIRECTOR_TIMEOUT_SEC`（默认 `28.0`s）与 `DIRECTOR_MAX_RETRIES`（默认 `1`，即最多 2 次尝试）。
   - `backend/director/genai_client.py`：新增 `DirectorModelError`（带 `code` / `status_code` / `retryable`）；`generate_structured` 和 `generate_text` 按单次超时执行并在超时或瞬时异常时自动重试 1 次，耗尽后抛出 `504`（超时）或 `503`（上游失败）结构化错误。
   - `backend/main.py`：注册 `DirectorModelError` 全局异常处理器，返回 `{"detail": {"code", "message", "retryable": true}}`（HTTP 504/503，不再抛 500）。
   - `frontend/src/api/stageClient.ts`：解析结构化 `detail.message` 并将 `/api/director/perceive` 与 `/api/director/extend` 前端超时调整为 `65s`（覆盖后端 2 次尝试）。
   - `frontend/src/App.tsx`：导演调用失败时在错误条中提供一键 `Retry` 按钮重发上次导演指令；顶栏状态文案改为 `Saved` / `Live`（移除 `MOCK MODE` 字样）。
2. **无浏览器端到端黄金路径脚本**
   - 新增 `backend/media/e2e_golden_path.py`（通过 FastAPI `ASGITransport` 直接跑真实后端与真实模型）：
     - 超时重试与 504 检查（不返回 500）
     - 文字指令 → 续演第 2 幕 → 渲染双机位成片 → 检查 A/B 两段成片及尾帧落盘、物品表数量唯一
     - 移动 Lia 到右侧（`grid=[1.75, 1.1]`）→ 续演第 3 幕 → 检查新站位保留且物品表唯一
     - 「拿起花瓶」→ 续演第 4 幕 → 检查 `crystal_vase` 仅存在 1 份且在手里（`stage_props` 中无重复）
3. **本地已有存档分支与成片入库**
   - 提交 `backend/assets/branches/branch_v6.json` 及其引用的 6 段成片（`scene_2/3/4` 双机位 `.mp4`）与 6 张尾帧 PNG。

### 已亲眼验证（Verified）

- **前端构建与静态门禁**：执行 `cd frontend && npm run verify`，6/6 全部通过（`tsc --noEmit`、`vite build`、`verify-locale.mjs`、`verify_frontend.mjs`、`test_stage_helpers.mjs`、`smoke_preview.mjs`）。
- **导演超时重试与 504 错误码**：在 `e2e_golden_path.py` 中模拟超时，实测触发 2 次尝试（初试 + 1 次重试）后返回 `HTTP 504`（非 500），包含 `code="director_timeout"`、`retryable=true` 及可读提示。
- **端到端黄金路径连续 3 次运行**：执行 `.venv/bin/python backend/media/e2e_golden_path.py --runs 3`，**3/3 全部通过**：
  - Run 1：总耗时 `85.66s`（续演 S2 `28.34s`，渲染 S2 `25.58s`，移动后续演 S3 `17.34s` 站位 `[1.75, 1.1]`，拿花瓶 S4 `14.37s` `held=['crystal_vase']`、`loose=['red_umbrella']`）
  - Run 2：总耗时 `106.97s`（续演 S2 `48.19s`——首次尝试超时后由自动重试救回，渲染 S2 `25.47s`，S3 `16.54s` 站位 `[1.75, 1.1]`，S4 `16.75s` `held=['crystal_vase']`、`loose=['red_umbrella']`）
  - Run 3：总耗时 `70.44s`（续演 S2 `16.36s`，渲染 S2 `24.78s`，S3 `14.77s` 站位 `[1.75, 1.1]`，S4 `14.52s` `held=['crystal_vase']`、`loose=['red_umbrella']`）

### 尚未验证（Not Verified）

- **真实麦克风语音输入**：未在真机浏览器中用真实麦克风实测（需 Christina 本人验证）。
- **真实摄像头拍道具**：未在真机浏览器中用真实摄像头实测拍摄道具入镜（需 Christina 本人验证）。
- **前端错误条 Retry 按钮真机点击交互**：代码逻辑与静态类型已检查通过，但未在浏览器中人工触发 504 后点击实测。
