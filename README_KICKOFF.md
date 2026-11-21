# StageState 3 人并行开工包（12:00 即刻启动）

> 对应方案文档：[StageState Proposal - Gemini 4 NYC Hackathon](https://docs.google.com/document/d/1bvKYYQQ1zpmoPIRM8kV3e-_XZoxShamD8xgmTgL_Sc8/edit)

## 1. 物理目录隔离（严禁跨目录修改）

```text
StageState/
├── shared/                           # 【只读契约】已锁死，全员共享
│   ├── schema.json                   # 统一 JSON Schema（含手势/语气/judge/分叉树）
│   ├── mock_branch_1.json            # Branch 1 默认缓存 BE（4 幕完整 Mock）
│   ├── mock_branch_2.json            # Branch 2 改写 HE（4 幕完整 Mock）
│   └── assets/                       # 抠好透明底的定妆图与概念图（给 P2 做 Omni Flash 参考图）
│       ├── lia.png                   # 莉亚（星夜少女 + 红伞）
│       ├── nyx.png                   # 诺克斯（星月黑猫）
│       ├── crystal_vase.png          # 蓝玫瑰水晶花瓶
│       └── stage_concept_split.png   # BE vs HE 主视觉目标参考
├── backend/
│   ├── director/                     # 【队友 1 · P1 独占】Neon 推演 + Hydrogen 多模态 + judge()
│   │   └── prompts.py                # 已写好 6 套 Prompt（含 3 秒音画手势解析）
│   └── media/                        # 【队友 2 · P2 独占】Omni Flash 双拍 + create_prop() 抠图
├── eval/                             # 【队友 1 · P1 独占】1→4 角色崩溃曲线 + Baseline 对比表
└── frontend/                         # 【队友 3 · P3 独占】Three.js 2.5D 双机位舞台 + 分叉 UI
    └── public/assets/                # 已就位的透明底 PNG 舞台立牌
```

---

## 2. 约定 API 接口（三端联调零摩擦）

* `GET  /api/branches` → 返回所有分支树（默认带 `branch_v1`）
* `POST /api/director/fork`（P1）
  * 输入：`multipart/form-data`，包含 `parent_branch_id`, `target_scene_id`, 可选 `clip`（前端录的 3 秒音画手势 `.webm`）或 `text`（文字兜底）
  * 内部流转：Hydrogen 解析手势+语气+实物帧 → 若检测到实物调 P2 `create_prop()` → `judge()` 矛盾检查 → Neon 一次性生成 `k..4` 幕新状态 → `judge()` 标出 `needs_rerender`
  * 输出：新分支 JSON（严格符合 `shared/schema.json`）
* `POST /api/media/create-prop`（P2）
  * 输入：实物截图或文本描述 → Gemini 3.1 Flash Image 绿底生图 → 去绿幕透明 PNG → 返回 `{ prop_id, image_url }`
* `POST /api/media/render-scene`（P2）
  * 输入：`scene_json` + 前端导出的 `cam_a_preview` & `cam_b_preview`（`.webm` 或降级首尾帧 PNG）+ 上一幕尾帧
  * 内部流转：`asyncio.gather` 双机位 × 双拍（Omni Flash 360p 4 秒）→ 截尾帧 → `judge()` 打分选优（全不过调 P1 写约束重拍最多 2 次）→ Hydrogen 观察尾帧回写 `environment_notes`
  * 输出：更新后的 `media` 字段

---

## 3. 现在（12:00）三个人直接复制给各自 AI 的任务

### 👤 P1（`/backend/director/` & `/eval/`）
> 读 `shared/schema.json`、`shared/mock_branch_1.json` 和 `backend/director/prompts.py`。用 FastAPI 实现 `/api/branches` 和 `/api/director/fork`：
> 1) 先把 `judge(media_or_state, questions)` 用 Gemini 4 Hydrogen 结构化输出封装好（单函数方便后续切换 Decisions API）；
> 2) 实现接收 3 秒摄像头+麦克风 `.webm` 的 Hydrogen 多模态解析（手势+语气+实物截帧时间戳），以及 Neon 一次性输出 `k..4` 幕状态 + 受影响幕筛选；
> 3) 12:45 前保证命令行用一条音频/文字就能把 `branch_v1` 从第 2 幕分叉出 `branch_v2`。

### 👤 P2（`/backend/media/`）
> ⚡最高优先级：马上用 `shared/assets/lia.png` + `shared/assets/nyx.png` 跑通一个 Omni Flash 360p 4 秒测试脚本，**10 分钟内报出单段真实渲染耗时**（若 >40 秒立刻喊全员砍掉第 3 幕变 3 幕）！
> 然后封装 `create_prop()`（绿底生图 + OpenCV 去绿幕透明 PNG）与 `render_clip()`（`asyncio.gather` 双拍并发 + 截尾帧 + 调用 P1 的 `judge()` 选高分条，不过则重试最多 2 次）。

### 👤 P3（`/frontend/`）
> 读 `shared/schema.json`、`shared/mock_branch_1.json`、`shared/mock_branch_2.json`，直接使用 `frontend/public/assets/actors/lia.png`、`nyx.png` 和 `props/crystal_vase.png` 透明 PNG 搭建 Three.js 2.5D 3×3 格子舞台：
> 1) 架设 Camera A（正面全景）与 Camera B（45° 斜侧特写）双 Canvas，配好 `cold_blue`（下雨）和 `warm_gold_snow`（暖金下雪）粒子；
> 2) 用 `canvas.captureStream()` 导出 4 秒 `.webm`（若 12:30 卡住立刻降级为首尾 2 张 PNG）；
> 3) 把麦克风和摄像头合二为一做成 1 个「🎥 按住手势+语音导戏」3 秒 `.webm` 按钮 + 文字兜底框 + 极简场记板 QA 条 + 4 幕分叉树 + 新旧结局同屏对比（不要做成仪表盘）。
