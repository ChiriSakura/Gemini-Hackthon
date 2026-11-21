# StageState 多机协同开发规范 (`AGENTS.md`)

> 对应方案文档：[StageState Proposal - Gemini 4 NYC Hackathon](https://docs.google.com/document/d/1bvKYYQQ1zpmoPIRM8kV3e-_XZoxShamD8xgmTgL_Sc8/edit)
> 任务板与时间节点：[tasks/BOARD.md](tasks/BOARD.md) | 认领指南：[tasks/HOW_TO_CLAIM.md](tasks/HOW_TO_CLAIM.md)

## 1. 节点清单与物理目录边界（严禁越界修改）

| Agent Role | Branch | Owned Directories / Files | Task Spec |
| :--- | :--- | :--- | :--- |
| **主电脑 (`Master` / P1)** | `main` (or `feat/p1-*`) | `backend/director/**`, `eval/**`, `shared/**`, `backend/main.py`, `backend/config.py`, `backend/logger.py`, `backend/schemas.py`, `tasks/BOARD.md`, `tasks/status_P1.json` | [tasks/P1_director.md](tasks/P1_director.md) |
| **从机 1 (`worker-1` / P2)** | `feat/w1-*` | `backend/media/**`, `backend/assets/**`, `tasks/status_P2.json` | [tasks/P2_media.md](tasks/P2_media.md) |
| **从机 2 (`worker-2` / P3)** | `feat/w2-*` | `frontend/**`, `tasks/status_P3.json` | [tasks/P3_frontend.md](tasks/P3_frontend.md) |

## 2. 已锁定的共享资产与接口契约（只读引用，改契约必须由 Master 推 `main`）

- **Schema & Mocks**：
  - `shared/schema.json`（角色锁定 `lia`、`nyx`；初始道具 `red_umbrella`、`crystal_vase`；现场道具示例 `star_token`）
  - `shared/mock_branch_1.json`（BE《雨夜惊逃与碎花瓶》）
  - `shared/mock_branch_2.json`（HE《星雪下的信任与共伞》）
  - `backend/director/prompts.py`（Hydrogen 多模态解析、统一 `judge()`、Neon 推演、修复与尾帧观察 Prompt）
- **抠好透明底 PNG 资产**：
  - 后端参考图：`shared/assets/lia.png`、`nyx.png`、`crystal_vase.png`、`stage_concept_split.png`
  - 前端立牌：`frontend/public/assets/actors/lia.png`、`actors/nyx.png`、`props/crystal_vase.png`
- **统一 HTTP API 边界**：
  - `GET /api/branches`（P1）
  - `POST /api/director/fork`（P1：接收 3 秒音画手势 `.webm` 或文字兜底，返回新分支 JSON）
  - `POST /api/media/create-prop`（P2：实物截图/文字 → 绿底生图 → 透明 PNG）
  - `POST /api/media/render-scene`（P2：预览短片 + 参考图 + 尾帧 → 并发双拍 → `judge()` 选优 → 回写 `environment_notes`）

## 3. 强制编码与安全规范（Hackathon Guidelines）

1. **严禁提交 API Key 或 `.env` 文件**：仅通过环境变量读取 `GEMINI_API_KEY`。
2. **使用 Gemini Skill**（`gemini-api`）：所有 Gemini 4（`Neon`、`Hydrogen`、`Omni Flash`、`3.1 Flash Image`）调用均遵循最新 SDK 写法。
3. **中心化配置**：所有模型 ID、重试次数、分辨率、阈值均从 `backend/config.py` 导入，禁止在业务文件里硬编码。
4. **结构化日志**：所有功能函数与 GenAI 调用均通过 `backend/logger.py`（`log_function_call` / `log_genai_call`）以 `INFO` 级别记录参数、模型、配置与输出，自动剥离 inline 二进制字节。
5. **文档与注释**：在根目录维护 `Design.md`，每个文件顶部写清功能与用例长注释，每个函数添加 docstring。
6. **Decisions API → `judge()` 统一抽象**：比赛账号实测 Decisions API 返回 `404`，所有是非打分、选片、受影响幕筛选与矛盾检查必须统一调用 `backend/director/hydrogen_judge.py` 的 `judge()`。

## 4. 派活、验收与熔断流程

1. **契约先行**：任何字段或接口变动必须先在 Master 更新 `shared/schema.json` 并推至 `main`，从机 `git pull origin main` 后继续。
2. **异步派活 / 认领**：Master 可通过远程 MCP `submit_task(prompt, workdir)` 向 `worker-1` / `worker-2` 派活，或由队友直接把 `tasks/HOW_TO_CLAIM.md` 的 Prompt 发给各自 Agent，明确分支名（`feat/w1-*` / `feat/w2-*`）、独占目录和截止时间。
3. **验收合入**：Master 拉取对应分支检查 diff 未越界且联调通过后 merge 进 `main`，严禁强推 `main`。
4. **熔断检查点**：
   - 若 Omni Flash 单段耗时 > 40 秒 → 全员裁掉第 3 幕变为 3 幕故事。
   - 若前端 `canvas.captureStream()` 导出 `.webm` 卡住（13:05 检查）→ 立刻降级为截首尾 2 张 PNG 传给 Omni Flash 首尾帧模式。
   - 若某镜头重试 2 次仍未通过 `judge()` → 前端降级展示 2.5D 舞台实时预览并标注「已降级」，确保现场永不黑屏。
