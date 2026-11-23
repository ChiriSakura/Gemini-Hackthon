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

---

## 阶段 1：在成片上测一致性

### 已完成改动

1. **成片一致性自动评测套件**
   - 新增 `backend/media/eval_video_consistency.py`：
     - 固定 6 幕测试剧本（`SIX_SCENE_SCRIPT`），从 `branch_v1` 第 1 幕出发覆盖全部 6 个动作：拿起花瓶 → 喝饮料（含新建 `iced_tea_cup` 道具）→ 拖动 Lia 到右侧（`/api/director/move-actor`）→ 把伞放地上 → 猫跳上桌 → 放回花瓶。
     - 根据每幕世界状态自动生成 7 类是非题（共 6 幕 × 15 题 = 90 题）：
       - 单机位 5 类（每幕 A/B 各问 1 次）：`count`（数量）、`holding`（持有）、`position`（位置）、`permanence`（恒存）、`clean`（无拍摄设备/文字/数字）。
       - `cross_camera`（跨机位同步）：开头 / 中间 / 结尾 3 个时间点各取 A/B 帧对比。
       - `inter_scene`（幕间连贯）：上一幕尾帧与本幕首帧（A/B 各 1 题）对比。
     - 使用 `ffmpeg -nostdin` 抽取关键帧并调用 `judge()`（Decisions API）打分，结果写入 `backend/assets/eval/video_consistency.json`，同时抽存 30 张代表帧到 `backend/assets/eval/frames/` 写入 `human_audit_sample` 供人工核对。
2. **后端与前端展示接入**
   - `backend/main.py`：`/api/eval/summary` 新增 `video_consistency` 字段读取 `backend/assets/eval/video_consistency.json`。
   - `frontend/src/api/stageClient.ts` & `frontend/src/components/ModelsPanel.tsx`：在 `Models & Evaluation` 面板新增 **Clip-level consistency (6-scene benchmark)** 表格，展示 7 类题与总计的通过数和通过率。

### 已亲眼验证（Verified）

- **前端构建与静态门禁**：执行 `cd frontend && npm run verify`，6/6 全通过（含更新后的 `ModelsPanel.tsx`）。
- **现有流程（Method A：`A_baseline_prev_tail`）在 6 幕剧本（`branch_v7`）上的成片评测结果**（90 题全部通过真实 Decisions API 跑完，写入 `backend/assets/eval/video_consistency.json`）：

| 题类 | 通过 / 总数 | 通过率 | 平均置信度 |
| :--- | :---: | :---: | :---: |
| `count`（数量） | 12 / 12 | 100.0% | 0.860 |
| `holding`（持有） | 12 / 12 | 100.0% | 0.913 |
| `position`（位置） | 8 / 12 | 66.7% | 0.906 |
| `permanence`（恒存） | 10 / 12 | 83.3% | 0.891 |
| `clean`（干净画面） | 12 / 12 | 100.0% | 0.876 |
| `cross_camera`（跨机位同步） | 13 / 18 | 72.2% | 0.715 |
| `inter_scene`（幕间连贯） | 12 / 12 | 100.0% | 0.835 |
| **总计（6 幕）** | **79 / 90** | **87.8%** | — |

- **主要短板**：`position`（66.7%）与 `cross_camera`（72.2%）是现有流程最明显的失分项，正好作为阶段 2（3D 做底对比实验）的重点改进指标。

### 尚未验证（Not Verified）

- **人工标注核对约 30 帧**：已生成 30 条待标注样本（`backend/assets/eval/video_consistency.json` 中的 `human_audit_sample`，对应帧在 `backend/assets/eval/frames/`），尚未由 Christina 人工核对自动判题准确率。
- **浏览器中打开 `ModelsPanel` 目视检查新表格排版**：前端编译与静态检查全绿，但未在真机浏览器里点开面板截图目视确认。



---

## 重要：API key 已失效（2026-11-23）

比赛发的 key 现在所有调用都返回 `401 API key not valid`（2026-11-22 12:14 还能用）。下面阶段 2 到 5 里，凡是需要真实生成视频或自动判题的，都标在「尚未验证」里。代码和离线测试都已完成。

离线测试一共 37 项，全部通过，不需要网络和 key：

```bash
.venv/bin/python backend/test_world_rules.py        # 14 项：物品表、谁在桌上、3D 投影、每个动作都有表现
.venv/bin/python backend/test_stage3d_pipeline.py   # 7 项：三种 3D 做法的整条流程（模型调用换成假的）
.venv/bin/python backend/test_timeline.py           # 7 项：时间线对比和嫁接
.venv/bin/python backend/test_bench.py              # 5 项：一致性基准
.venv/bin/python backend/test_improv.py             # 4 项：角色自己演
cd frontend && npm run verify                       # 前端 7 步，含浏览器和后端投影一致性
```

---

## 阶段 2：3D 做底，三种接法

详见 `docs/3d_first_experiment.md`。

### 已完成改动

1. `backend/media/stage3d.py`：按世界状态合成两个机位的画面。房间用固定底图，人物和道具按 3D 坐标投影贴上去。一帧 0.2 秒，不调用模型。
2. `shared/viewpoints.json`：两个机位的参数只定义在这一处，后端和前端都读它。
3. `RENDER_BASE` 开关：`prev_tail`（默认，原流程）、`stage3d`、`stage3d_first_last`、`stage3d_video`。`SPLIT_RESOLUTION`：`720p` 或 `1080p`。
4. `backend/director/world_rules.py`：物品表、谁在桌上、桌子避让，从 `main.py` 里拆出来成为独立模块。
5. 角色状态加 `elevation`（`floor` 或 `table`），导演提示词加了对应规则。旧规则会把桌子范围内的角色推出去，所以「猫在桌上」以前表达不出来。
6. `eval_video_consistency.py`：加了 `--states-from`（同一套世界状态换做法重渲染，不调导演）、`--render-base`、`--resolution`、`--compare`。题目集升到 v2。
7. 基准分支 `branch_v7` 的成片和道具图补进了仓库（之前只提交了 JSON，成片没提交）。

### 已亲眼验证

- 合成帧的图：猫在桌上、伞躺在地上、花瓶和杯子各一个，两个机位一致（`docs/img/stage3d_composites.jpg`）。
- `test_stage3d_pipeline.py` 7 项全过。
- 没有 key 时渲染接口返回 503 和明确的说明，不再是 500。

### 尚未验证

- **三种做法的对比表没跑**（要 key）。默认做法没改。
- 视频模型从合成帧出发的成片质量。
- `interactions` 接口是否接受视频输入（做法 C）。
- 1080p 的耗时和花费。

---

## 阶段 3：即时预览

### 已完成改动

1. 每次续演出新一幕，后端立刻生成 3D 预览片：舞台从上一幕的状态演到这一幕，两个机位各一段。约 2 秒，不调用模型。接口 `POST /api/media/blocking-preview` 可以给已有的幕补预览。
2. 前端：一幕没有成片时，两个机位显示这段预览片，右上角标「3D preview」；成片渲染期间标「3D preview · final clip rendering…」。成片好了自动替换。
3. 前端 3D 舞台改成读 `shared/viewpoints.json`，单位改成米（之前人物被放大了 1.36 倍）。
4. 世界视角在固定机位时显示和后端同一张房间底图，3D 人物画在上面；拖动旋转时切回普通 3D 房间（平面图没法转）。
5. 网址加 `?branch=<id>&scene=<n>` 可以直接打开某条存档的某一幕。`branch_v7_3d` 是基准的六幕状态，不带成片，专门用来看 3D 预览。

### 已亲眼验证（在内置浏览器里打开页面实测）

- `?branch=branch_v7_3d&scene=6`：两个机位播放 3D 预览片，猫从地上到桌上，标签显示正常。
- 世界视角：固定机位时是画好的房间，拖动后切到 3D 房间并能旋转。
- 浏览器和后端把 9 个测试点投到屏幕上的位置，最大误差 0.001 像素。
- `npm run verify` 全过。

### 尚未验证

- 「提交指令后 1 秒内画面开始动」：预览片生成约 2 秒，加上导演模型的时间。没有 key 测不了导演这一段。离线的「角色自己演」模式下，从提交到预览片出现约 3 秒。
- 成片渲染完成后自动替换预览片这一步（要 key）。

---

## 阶段 4：动作库和道具挂点

### 已完成改动

1. `stage3d.ACTION_POSES`：清单里 14 个动作在 3D 舞台上都有表现。蹲和坐压低身高，跳有弧线，喝和吃时手里的东西举到嘴边再放下，撑伞时头顶画打开的伞。
2. 道具三种挂点：桌上、地上、手里（胸前，靠桌子的那一侧）。放下的伞平躺在地上或桌上。
3. 没有专门碎片图的道具，碎了之后画成倒下的两半。
4. 物品表补了规则：一件东西只能被一个角色拿着；碎了的不会自己复原。

### 已亲眼验证

- `test_world_rules.py` 14 项全过，包括：拿起、放下、递交、打碎、带着走、导演漏写的东西留在原地、每个动作的画面都和站立不同。

### 尚未验证

- 没做的可选项：把人物贴片换成带骨骼的 3D 模型。要等阶段 2 的结果说明需要更准的姿势信号再做。

---

## 阶段 5：往外扩展

### 5a 角色变成智能体

已完成：

- `backend/director/improv.py`。每个角色有性格和记忆，对同一个情境各自提出想做什么（每个角色一次模型调用，并行）。裁决器按随机顺序落实，做不到的拒绝并写进旁白，最后过一遍世界规则。
- 续演接口加 `mode=improvised`。输入栏左边加了 Directed / Improvised 开关。
- 模型连不上时，角色的意图改由离线的性格采样器给出，功能照常可用。

已亲眼验证：

- 女孩想拿花瓶、猫想把花瓶拨下桌，这两个意图冲突时，跑 12 次两种结局都出现，每次恰好一个意图被拒绝。
- 同一个情境跑 20 次，得到 3 种以上不同结局，物品表全部合法。
- 在页面上切到 Improvised，输入「The lights go out for a moment」，生成了新一幕和 3D 预览。

尚未验证：

- **用模型给意图的那条路没跑过**（要 key）。页面上实测走的是离线采样器。

### 5b 时间线对比和合并

已完成：

- `backend/director/timeline.py`。逐幕对比两条时间线的世界状态。嫁接时如果接口处两个世界一致就直接接，不一致就自动加一幕过渡。过渡幕由差异直接生成，不调用模型。
- 接口 `GET /api/timeline/diff`、`POST /api/timeline/graft`。顶栏加了 Timelines 面板。

已亲眼验证：

- 在页面上对比 Sleep Fork A 和 B：前两幕标为同一条成片，第 3 幕起列出差异。
- 把 A 保留到第 3 幕、接上 B 的第 4 幕：提示两个世界不一致，加了过渡幕，打开新时间线后过渡幕显示 3D 预览。
- `test_timeline.py` 7 项全过。

尚未验证：

- 嫁接后的幕重新渲染成片（要 key）。

### 5c 长程一致性基准

已完成：

- `backend/bench/`。剧本生成器（指定幕数、物品数、随机种子；事件有拿起、放下、打碎、移动、猫上桌下桌）。生成出来的世界状态就是标准答案。
- 自动出题：数量、位置、是否完好、角色、画面干净、跨机位、幕间。
- 三个适配器：3D 舞台本身（不用模型，作参照）、带世界状态的 StageGenesis、不带状态只串文字的基线。接新的视频模型只要加一个适配器。
- 输出通过率随幕数变化的曲线、按物品数和题目类别的通过率，并画图。

已亲眼验证：

- `test_bench.py` 5 项全过：剧本可复现且合法、每种事件都会出现、题目和状态一致、整条流程能跑通并画出曲线。
- 这里的判题用的是假的替身，只为了测流程，**没有产生任何结果数据**。

尚未验证：

- **真实的崩溃曲线没跑**（要 key）。命令：

```bash
.venv/bin/python -m backend.bench.run --scenes 2,4,6,8 --objects 2,4 --seeds 2 \
    --adapters stage3d_blocking,with_state_stage3d_first_last,stateless_text_chain
```

### 5d 任意机位

按计划只记录方向，没做。

---

## 需要 Christina 本人做的

1. 提供一把能用的 Gemini API key。阶段 2 的对比表、5a 的模型意图、5c 的崩溃曲线都等它。
2. 用真麦克风测一次语音输入，用真摄像头测一次拍道具（阶段 0 留下的）。
3. 人工标约 30 帧，核对自动判题准不准（阶段 1 留下的）。因为题目集已升到 v2，建议等做法 A 用 v2 重新判完之后再标。
