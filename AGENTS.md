# StageState 多机协同开发规范

> 对应方案文档：[StageState Proposal - Gemini 4 NYC Hackathon](https://docs.google.com/document/d/1bvKYYQQ1zpmoPIRM8kV3e-_XZoxShamD8xgmTgL_Sc8/edit)

## 1. 节点清单与物理目录边界（严禁越界修改）
- **主电脑 (Master / P1)**：负责 `/backend/director/`、`/eval/`、`/shared/` 契约维护，以及 `main` 分支验收合入与全链路联调。
- **从机 1 (`worker-1` / P2)**：独占 `/backend/media/`，分支前缀 `feat/w1-*`。负责 Omni Flash 耗时实测、`create_prop()` 抠图、`render_clip()` 并发双拍与调用 `judge()` 选优。
- **从机 2 (`worker-2` / P3)**：独占 `/frontend/`，分支前缀 `feat/w2-*`。负责 Three.js 2.5D 3×3 双机位舞台、`.webm`（或首尾帧 PNG 熔断降级）导出、「🎥 按住手势+语音导戏」3 秒 `.webm` 按钮与分叉对比 UI。

## 2. 已锁定的共享资产与接口契约（只读引用，改契约必须由 Master 推 main）
- **Schema & Mocks**：
  - `/shared/schema.json`（角色锁定 `lia`、`nyx`；初始道具 `red_umbrella`、`crystal_vase`；现场道具 `star_token`）
  - `/shared/mock_branch_1.json`（BE《雨夜惊逃与碎花瓶》）
  - `/shared/mock_branch_2.json`（HE《星雪下的信任与共伞》）
  - `/backend/director/prompts.py`（Hydrogen 多模态解析、统一 `judge()`、Neon 推演、修复与尾帧观察 Prompt）
- **抠好透明底 PNG 资产**：
  - 后端参考图：`/shared/assets/lia.png`、`nyx.png`、`crystal_vase.png`、`stage_concept_split.png`
  - 前端立牌：`/frontend/public/assets/actors/lia.png`、`actors/nyx.png`、`props/crystal_vase.png`
- **统一 HTTP API 边界**：
  - `GET /api/branches`（P1）
  - `POST /api/director/fork`（P1：接收 3 秒音画手势 `.webm` 或文字兜底，返回新分支 JSON）
  - `POST /api/media/create-prop`（P2：实物截图/文字 → 绿底生图 → 透明 PNG）
  - `POST /api/media/render-scene`（P2：预览短片 + 参考图 + 尾帧 → 并发双拍 → `judge()` 选优 → 回写 `environment_notes`）

## 3. 派活与验收标准流程
1. **契约先行**：任何字段或接口变动必须先在 Master 更新 `/shared/schema.json` 并推至 `main`，从机 `git pull origin main` 后再继续。
2. **派发任务**：Master 通过远程 MCP `submit_task(prompt, workdir)` 向 `worker-1` / `worker-2` 派活，必须明确目标分支名（`feat/w1-*` 或 `feat/w2-*`）、允许修改目录及自测标准。
3. **异步追踪**：间隔调用 `get_task_status(task_id)` 获取从机输出与推送的分支名。
4. **验收合入**：Master 执行 `git fetch` 拉取对应分支，检查 diff 未越界且联调通过后再 merge 进 `main`，严禁强推 `main`。
5. **熔断检查点**：
   - 若 Omni Flash 单段耗时 > 40 秒 → 全员裁掉第 3 幕变为 3 幕故事。
   - 若前端 `canvas.captureStream()` 导出 `.webm` 卡住 → 立刻降级为截首尾 2 张 PNG 传给 Omni Flash 首尾帧模式。
   - 若某镜头重试 2 次仍未通过 `judge()` → 前端降级展示 2.5D 舞台实时预览并标注「已降级」，确保现场永不黑屏。
