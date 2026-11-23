"""
Run the world-consistency benchmark and draw the collapse curve.

    .venv/bin/python -m backend.bench.run --scenes 2,4,6,8 --objects 2,4 --seeds 2 \\
        --adapters stage3d_blocking,with_state_stage3d_first_last,stateless_text_chain

For every (scene count, object count, seed) a scenario is generated, every adapter makes its clips, and the judge
answers the generated questions on the last frame of each clip. The report gives the pass rate by scene number
(the collapse curve: where does the model start losing the world?), by object count and by question category.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from PIL import Image, ImageDraw

from backend import config
from backend.bench.adapters import Adapter, make_adapter
from backend.bench.questions import CATEGORIES, CROSS_CAMERA_QUESTION, INTER_SCENE_QUESTION, single_view_questions
from backend.bench.scenario import Scenario, generate_scenario
from backend.media import video_renderer as vr

# judge(questions, frame paths) -> list of (passed, confidence)
JudgeFn = Callable[[list[str], list[Path]], Awaitable[list[tuple[bool, float]]]]
REPORT_PATH = config.ASSETS_DIR / "eval" / "consistency_bench.json"
CHART_PATH = config.ASSETS_DIR / "eval" / "consistency_bench.png"


async def decisions_judge(questions: list[str], frames: list[Path]) -> list[tuple[bool, float]]:
    """The project's judge (Decisions API): one yes/no decision per question on the given frames."""
    from backend.director.hydrogen_judge import judge

    result = await judge(questions, context_text="", media_paths=[str(f) for f in frames])
    return [(item.passed, item.confidence) for item in result.items]


async def evaluate_scenario(scenario: Scenario, clips: dict[int, tuple[Path, Path]], judge_fn: JudgeFn, work: Path) -> list[dict]:
    """Ask every generated question about every scene of one scenario. Returns one record per question."""
    records: list[dict] = []
    first: dict[int, tuple[Path, Path]] = {}
    last: dict[int, tuple[Path, Path]] = {}
    for idx, (clip_a, clip_b) in clips.items():
        frames = []
        for cam, clip in (("a", clip_a), ("b", clip_b)):
            head = vr._extract_frame_at_sync(clip, 0.1, work / f"s{idx}_{cam}_first.png")
            tail = vr._extract_tail_frame_sync(clip, work / f"s{idx}_{cam}_last.png")
            frames.append((head, tail))
        first[idx] = (frames[0][0], frames[1][0])
        last[idx] = (frames[0][1], frames[1][1])
    for step in scenario.steps[1:]:
        idx = step.index
        if idx not in clips:
            continue
        questions = single_view_questions(step.scene)
        for cam_i, cam in enumerate(("cam_a", "cam_b")):
            answers = await judge_fn([q["question"] for q in questions], [last[idx][cam_i]])
            for q, (passed, conf) in zip(questions, answers):
                records.append({"scene": idx, "event": step.event, "camera": cam, **q, "passed": passed, "confidence": conf})
        (passed, conf), = await judge_fn([CROSS_CAMERA_QUESTION], [last[idx][0], last[idx][1]])
        records.append({"scene": idx, "event": step.event, "camera": "both", "category": "cross_camera", "subject": "views",
                        "question": CROSS_CAMERA_QUESTION, "passed": passed, "confidence": conf})
        if idx - 1 in last:
            for cam_i, cam in enumerate(("cam_a", "cam_b")):
                (passed, conf), = await judge_fn([INTER_SCENE_QUESTION], [last[idx - 1][cam_i], first[idx][cam_i]])
                records.append({"scene": idx, "event": step.event, "camera": cam, "category": "inter_scene", "subject": "join",
                                "question": INTER_SCENE_QUESTION, "passed": passed, "confidence": conf})
    return records


def _rate(records: list[dict]) -> Optional[float]:
    return round(sum(1 for r in records if r["passed"]) / len(records), 4) if records else None


def summarize(runs: list[dict]) -> dict:
    """Pass rate per adapter: overall, by scene number (collapse curve), by object count, by category."""
    out: dict[str, dict] = {}
    for adapter in sorted({r["adapter"] for r in runs}):
        mine = [r for r in runs if r["adapter"] == adapter]
        records = [dict(rec, n_objects=r["n_objects"]) for r in mine for rec in r["records"]]
        scenes = sorted({rec["scene"] for rec in records})
        out[adapter] = {
            "questions": len(records),
            "overall": _rate(records),
            "by_scene": {str(s): _rate([x for x in records if x["scene"] == s]) for s in scenes},
            "by_objects": {str(n): _rate([x for x in records if x["n_objects"] == n]) for n in sorted({x["n_objects"] for x in records})},
            "by_category": {c: _rate([x for x in records if x["category"] == c]) for c in CATEGORIES},
        }
    return out


def draw_curve(summary: dict, out_path: Path, title: str = "Pass rate by scene number") -> Path:
    """The collapse curve: one line per adapter, pass rate (y) against scene number (x)."""
    w, h, pad = 900, 480, 70
    img = Image.new("RGB", (w, h), (15, 18, 36))
    d = ImageDraw.Draw(img)
    scenes = sorted({int(s) for a in summary.values() for s in a["by_scene"]})
    if not scenes:
        img.save(out_path)
        return out_path
    x0, x1, y0, y1 = pad, w - 40, h - pad, 50
    sx = lambda s: x0 + (x1 - x0) * ((s - scenes[0]) / max(1, scenes[-1] - scenes[0]))
    sy = lambda v: y0 - (y0 - y1) * v
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        d.line([(x0, sy(v)), (x1, sy(v))], fill=(52, 60, 92))
        d.text((x0 - 44, sy(v) - 6), f"{int(v * 100)}%", fill=(190, 200, 220))
    for s in scenes:
        d.text((sx(s) - 4, y0 + 10), str(s), fill=(190, 200, 220))
    d.text((x0, 16), title, fill=(253, 230, 138))
    d.text(((x0 + x1) // 2 - 40, h - 28), "scene number", fill=(190, 200, 220))
    colors = [(253, 230, 138), (94, 234, 212), (248, 113, 113), (165, 180, 252), (251, 146, 60)]
    for i, (name, a) in enumerate(sorted(summary.items())):
        pts = [(sx(int(s)), sy(v)) for s, v in sorted(a["by_scene"].items(), key=lambda kv: int(kv[0])) if v is not None]
        color = colors[i % len(colors)]
        if len(pts) > 1:
            d.line(pts, fill=color, width=3)
        for px, py in pts:
            d.ellipse([(px - 4, py - 4), (px + 4, py + 4)], fill=color)
        d.rectangle([(x1 - 300, 22 + i * 18), (x1 - 288, 34 + i * 18)], fill=color)
        d.text((x1 - 280, 20 + i * 18), name, fill=(226, 232, 240))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


async def run_benchmark(
    scene_counts: list[int],
    object_counts: list[int],
    seeds: int,
    adapters: list[Adapter],
    judge_fn: JudgeFn,
    report_path: Optional[Path] = REPORT_PATH,
    chart_path: Optional[Path] = CHART_PATH,
    judge_name: str = "decisions_api",
) -> dict:
    runs: list[dict] = []
    for n_scenes in scene_counts:
        for n_objects in object_counts:
            for seed in range(seeds):
                for adapter in adapters:
                    scenario = generate_scenario(n_scenes, n_objects, seed)
                    with tempfile.TemporaryDirectory(prefix="bench_") as tmp:
                        work = Path(tmp)
                        clips = await adapter.render(scenario, work)
                        records = await evaluate_scenario(scenario, clips, judge_fn, work)
                    runs.append({
                        "adapter": adapter.name, "n_scenes": n_scenes, "n_objects": n_objects, "seed": seed,
                        "events": [s.event for s in scenario.steps], "records": records,
                    })
                    print(f"[bench] {adapter.name} scenes={n_scenes} objects={n_objects} seed={seed}: {_rate(records)}")
    summary = summarize(runs)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "judge": judge_name,
        "config": {"scene_counts": scene_counts, "object_counts": object_counts, "seeds": seeds,
                   "adapters": [a.name for a in adapters]},
        "summary": summary,
        "runs": runs,
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if chart_path is not None:
        draw_curve(summary, chart_path)
    return report


def _main() -> int:
    ap = argparse.ArgumentParser(description="World-consistency benchmark")
    ap.add_argument("--scenes", default="2,4,6,8")
    ap.add_argument("--objects", default="2,4")
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--adapters", default="stage3d_blocking,with_state_stage3d_first_last,stateless_text_chain")
    args = ap.parse_args()
    report = asyncio.run(run_benchmark(
        [int(x) for x in args.scenes.split(",")], [int(x) for x in args.objects.split(",")], args.seeds,
        [make_adapter(n) for n in args.adapters.split(",")], decisions_judge,
    ))
    print(json.dumps(report["summary"], indent=2))
    print(f"report: {REPORT_PATH}\nchart: {CHART_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
