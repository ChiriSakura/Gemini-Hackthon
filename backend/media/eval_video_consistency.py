"""
Clip-level Video Consistency Evaluation Harness for StageGenesis (Stage 1).

Feature & Use Cases:
- Runs a fixed 6-scene benchmark script covering:
  1. Pick up crystal vase
  2. Drink a beverage (with a registered drink prop)
  3. Drag character position (move Lia to right side via /api/director/move-actor)
  4. Put red umbrella on the floor
  5. Black cat Nyx jumps onto the center table
  6. Put crystal vase back onto the center table
- Automatically generates binary yes/no evaluation questions directly from the
  world state for every scene and both cameras (Camera A & Camera B):
  - `count`: exact entity counts (1 girl, 1 black cat, 1 crystal vase)
  - `holding`: umbrella & held prop state (in hand vs on floor/table)
  - `position`: Lia left/right of table, Nyx on floor vs on table
  - `permanence`: state changes from prior scenes persist without reverting
  - `clean`: zero filming gear (camera/tripod/lens), zero text/number overlays
  - `cross_camera`: synced start / mid / end frames across Camera A & B show the same action
  - `inter_scene`: previous scene end frame -> current scene start frame room & cast continuity
- Evaluates frames via `backend.director.hydrogen_judge.judge()` (Decisions API),
  writes `backend/assets/eval/video_consistency.json`, and saves a 30-frame
  `human_audit_sample` under `backend/assets/eval/frames/` for human calibration.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend import config
from backend.director.hydrogen_judge import judge
from backend.director.state_tree import get_branch, save_branch
from backend.main import app

EVAL_DIR = config.ASSETS_DIR / "eval"
FRAMES_DIR = EVAL_DIR / "frames"
DEFAULT_OUTPUT_PATH = EVAL_DIR / "video_consistency.json"

CATEGORY_ORDER = [
    "count",
    "holding",
    "position",
    "permanence",
    "clean",
    "cross_camera",
    "inter_scene",
]

# Fixed 6-scene benchmark script (Stage 1 specification)
SIX_SCENE_SCRIPT: list[dict[str, Any]] = [
    {
        "step": 1,
        "key": "pick_up_vase",
        "label": "Pick up crystal vase",
        "instruction": "Lia puts her closed red umbrella down on the floor beside her, walks to the center round table, and picks up the crystal vase with both hands.",
    },
    {
        "step": 2,
        "key": "drink_beverage",
        "label": "Drink beverage",
        "create_prop": {
            "name": "iced_tea_cup",
            "description": "a tall clear glass of amber iced tea with a lemon slice",
            "placement": "table",
        },
        "instruction": "Lia sets the crystal vase down on the round table, picks up the tall glass of iced tea from the table, and takes a calm sip.",
    },
    {
        "step": 3,
        "key": "drag_position",
        "label": "Move Lia to right side",
        "move_actor": {"actor_id": "lia", "grid_x": 1.75, "grid_y": 1.10},
        "instruction": "Lia stays standing on the right side of the room where she moved and looks warmly toward Nyx.",
    },
    {
        "step": 4,
        "key": "umbrella_on_floor",
        "label": "Put umbrella on floor",
        "instruction": "Lia places the closed red umbrella neatly on the wooden floor beside her feet and rests her empty hands at her sides.",
    },
    {
        "step": 5,
        "key": "cat_jumps_on_table",
        "label": "Cat jumps on table",
        "instruction": "Black cat Nyx leaps gracefully up onto the round wooden center table and sits beside the crystal vase while Lia watches.",
    },
    {
        "step": 6,
        "key": "put_vase_back",
        "label": "Put crystal vase back",
        "instruction": "Lia gently lifts the crystal vase for a moment and sets it carefully back onto the center of the round wooden table, then steps back with empty hands.",
    },
]


def _asset_url_to_path(url: Optional[str]) -> Optional[Path]:
    """Resolve `/assets/...` or filesystem path to an existing local Path."""
    if not url:
        return None
    p = Path(url)
    if p.is_file():
        return p
    if url.startswith("/assets/"):
        cand = config.ASSETS_DIR / url.removeprefix("/assets/").lstrip("/")
        if cand.is_file():
            return cand
    return None


def _probe_duration(video_path: Path) -> float:
    """Return video duration in seconds via ffprobe (fallback 4.0s)."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            text=True,
            timeout=10,
        ).strip()
        val = float(out)
        return val if val > 0.2 else 4.0
    except Exception:
        return 4.0


def _extract_frame(video_path: Path, timestamp_sec: float, out_path: Path, *, from_eof: bool = False) -> Path:
    """Extract a single PNG frame using ffmpeg with -nostdin so it never blocks."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seek_args = ["-sseof", f"-{max(0.25, timestamp_sec):.3f}"] if from_eof else ["-ss", f"{max(0.0, timestamp_sec):.3f}"]
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *seek_args,
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    return out_path


def _extract_clip_keyframes(video_path: Path, prefix: str) -> dict[str, Path]:
    """Extract start, mid, and end frames for a rendered clip."""
    dur = _probe_duration(video_path)
    return {
        "start": _extract_frame(video_path, min(0.35, dur * 0.12), FRAMES_DIR / f"{prefix}_start.png"),
        "mid": _extract_frame(video_path, dur * 0.5, FRAMES_DIR / f"{prefix}_mid.png"),
        "end": _extract_frame(video_path, 0.35, FRAMES_DIR / f"{prefix}_end.png", from_eof=True),
    }


def _describe_lia_side(grid: list[float]) -> str:
    """Return left / right / near-center description from Lia's grid x."""
    gx = float(grid[0]) if grid else 1.0
    if gx < 0.75:
        return "left"
    if gx > 1.25:
        return "right"
    return "near_table"


def build_scene_questions(
    scene: dict[str, Any],
    prior_scenes: list[dict[str, Any]],
    step_meta: Optional[dict[str, Any]] = None,
) -> dict[str, tuple[str, str]]:
    """
    Generate the 5 single-camera questions (`count`, `holding`, `position`, `permanence`, `clean`)
    from the scene's world state and prior history.

    Returns mapping: category -> (frame_slot ['mid'|'end'], question_text).
    """
    actors = {a["id"]: a for a in scene.get("actors", [])}
    stage_props = {p["id"]: p for p in scene.get("stage_props", [])}
    lia = actors.get("lia", {})
    nyx = actors.get("nyx", {})

    lia_holding = lia.get("holding_prop")
    lia_side = _describe_lia_side(lia.get("grid", [0.0, 1.0]))
    # The world state says where the cat is (`elevation`); older saved branches without the field fall back to
    # the action / script step that put it on the table.
    nyx_on_table = (
        nyx.get("elevation") == "table"
        or nyx.get("action") == "jump"
        or (step_meta is not None and step_meta.get("key") == "cat_jumps_on_table")
        or (0.75 <= float(nyx.get("grid", [2.0, 1.0])[0]) <= 1.25 and 0.75 <= float(nyx.get("grid", [2.0, 1.0])[1]) <= 1.25)
    )

    # 1. Count
    q_count = (
        "Are there at most one silver-haired girl (not two girls), at most one black cat (not two cats), "
        "and at most one crystal vase with a blue rose (no duplicated people, cats, or vases) in this frame?"
    )

    # 2. Holding
    if lia_holding == "red_umbrella":
        q_holding = "Is the silver-haired girl holding the closed red umbrella in her hand (and NOT a second umbrella on the floor)?"
    elif lia_holding == "crystal_vase":
        q_holding = "Is the silver-haired girl holding the crystal vase in her hands (and NOT holding the red umbrella)?"
    elif lia_holding:
        prop_words = lia_holding.replace("_", " ")
        q_holding = f"Is the silver-haired girl holding the {prop_words} in her hand (and NOT holding the red umbrella)?"
    else:
        umb = stage_props.get("red_umbrella")
        if umb and umb.get("on_floor"):
            q_holding = "Are the silver-haired girl's hands free of the red umbrella (with the red umbrella resting on the floor or set aside, not held open)?"
        else:
            q_holding = "Are the silver-haired girl's hands free of the red umbrella (not holding an umbrella in this frame)?"

    # 3. Position
    if lia_side == "left":
        lia_pos_desc = "on the left side of the round table"
    elif lia_side == "right":
        lia_pos_desc = "on the right side of the round table"
    else:
        lia_pos_desc = "beside the center round table"
    nyx_pos_desc = "on top of the round wooden table" if nyx_on_table else "on the floor (not on top of the table)"
    q_position = (
        f"Is the silver-haired girl positioned {lia_pos_desc}, and is the black cat positioned {nyx_pos_desc}?"
    )

    # 4. Permanence
    vase_prop = stage_props.get("crystal_vase")
    prior_had_broken_vase = any(
        any(p.get("id") == "crystal_vase" and p.get("state") == "broken" for p in ps.get("stage_props", []))
        for ps in prior_scenes
    )
    if prior_had_broken_vase:
        q_perm = "Does the crystal vase remain broken (not magically restored to intact) as established in earlier scenes?"
    elif lia_holding == "crystal_vase":
        q_perm = "Has the crystal vase been removed from the tabletop so that it is only in the girl's hands and not still sitting on the table?"
    elif vase_prop is not None:
        q_perm = "Is the intact crystal vase with the blue rose still present on the round wooden table (not vanished or broken)?"
    else:
        q_perm = "Are the glass conservatory room, round wooden table, and previously established props consistent with earlier scenes without items vanishing?"

    # 5. Clean
    q_clean = (
        "Is the image completely free of filming equipment (no cameras, tripods, lenses, or crew) "
        "and completely free of overlaid text, labels, subtitles, or numbers?"
    )

    return {
        "count": ("mid", q_count),
        "holding": ("end", q_holding),
        "position": ("end", q_position),
        # Asked on the last frame: an object that is picked up during the clip is still on the table halfway.
        "permanence": ("end", q_perm),
        "clean": ("mid", q_clean),
    }


async def generate_six_scene_branch(method_name: str = "A_baseline_prev_tail") -> dict[str, Any]:
    """Run the fixed 6-scene script through the live FastAPI pipeline and render all 6 scenes."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://eval",
        timeout=240.0,
    ) as http:
        health = (await http.get("/api/health")).json()
        assert health.get("live") is True, f"Backend must be live to run video consistency eval: {health}"

        current_branch_id = "branch_v1"
        current_scene_id = 1
        step_timings: list[dict[str, Any]] = []

        for step in SIX_SCENE_SCRIPT:
            t_step = time.perf_counter()
            injected_prop_json: Optional[str] = None

            if step.get("create_prop"):
                cp = step["create_prop"]
                prop_res = await http.post(
                    "/api/media/create-prop",
                    data={
                        "name": cp["name"],
                        "description": cp["description"],
                        "branch_id": current_branch_id,
                        "scene_id": str(current_scene_id),
                        "placement": cp.get("placement", "table"),
                        "entry_motion": "place",
                    },
                )
                assert prop_res.status_code == 200, f"Step {step['step']} create-prop failed: {prop_res.text}"
                prop_body = prop_res.json()
                injected_prop_json = json.dumps(
                    {
                        "prop_id": prop_body["prop_id"],
                        "item": prop_body["item"],
                        "placement": cp.get("placement", "table"),
                        "entry_motion": "place",
                    }
                )
                print(f"[Script Step {step['step']}] Created prop {prop_body['prop_id']}")

            if step.get("move_actor"):
                mv = step["move_actor"]
                mv_res = await http.post(
                    "/api/director/move-actor",
                    data={
                        "branch_id": current_branch_id,
                        "scene_id": str(current_scene_id),
                        "actor_id": mv["actor_id"],
                        "grid_x": str(mv["grid_x"]),
                        "grid_y": str(mv["grid_y"]),
                    },
                )
                assert mv_res.status_code == 200, f"Step {step['step']} move-actor failed: {mv_res.text}"
                print(f"[Script Step {step['step']}] Moved {mv['actor_id']} -> {mv_res.json()['grid']}")

            t_ext = time.perf_counter()
            ext_data: dict[str, str] = {
                "branch_id": current_branch_id,
                "from_scene_id": str(current_scene_id),
                "text": step["instruction"],
                "end_freeze_mode": "sculpture",
            }
            if injected_prop_json:
                ext_data["injected_prop_json"] = injected_prop_json
            ext_res = await http.post("/api/director/extend", data=ext_data)
            assert ext_res.status_code == 200, f"Step {step['step']} extend failed ({ext_res.status_code}): {ext_res.text}"
            branch = ext_res.json()
            current_branch_id = branch["branch_id"]
            scenes = sorted(branch["scenes"], key=lambda s: s["scene_id"])
            new_scene = scenes[-1]
            current_scene_id = int(new_scene["scene_id"])
            ext_sec = round(time.perf_counter() - t_ext, 2)

            t_ren = time.perf_counter()
            ren_res = await http.post(
                "/api/media/render-scene",
                data={
                    "branch_id": current_branch_id,
                    "scene_id": str(current_scene_id),
                    "fast_live_mode": "true",
                },
            )
            assert ren_res.status_code == 200, f"Step {step['step']} render failed ({ren_res.status_code}): {ren_res.text}"
            ren_body = ren_res.json()
            ren_sec = round(time.perf_counter() - t_ren, 2)
            media = ren_body.get("media") or {}
            step_timings.append(
                {
                    "step": step["step"],
                    "key": step["key"],
                    "scene_id": current_scene_id,
                    "extend_sec": ext_sec,
                    "render_sec": ren_sec,
                    "total_sec": round(time.perf_counter() - t_step, 2),
                    "fallback": bool(media.get("fallback_to_preview")),
                }
            )
            print(
                f"[Script Step {step['step']}/6] S{current_scene_id} ({step['key']}) "
                f"extend={ext_sec}s render={ren_sec}s cam_a={media.get('cam_a_final_url')} cam_b={media.get('cam_b_final_url')}"
            )

        final_branch = (await http.get(f"/api/branches/{current_branch_id}")).json()
        return {
            "branch": final_branch,
            "method": method_name,
            "step_timings": step_timings,
        }


async def evaluate_branch_clips(
    branch: dict[str, Any],
    *,
    method_name: str = "A_baseline_prev_tail",
    step_timings: Optional[list[dict[str, Any]]] = None,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    """Evaluate rendered clips of scenes 2..N of `branch` across all 7 consistency categories."""
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    branch_id = branch["branch_id"]
    ordered_scenes = sorted(branch["scenes"], key=lambda s: s["scene_id"])
    assert len(ordered_scenes) >= 2, f"Branch {branch_id} needs at least 2 scenes to evaluate"

    timings_by_scene = {t["scene_id"]: t for t in (step_timings or [])}

    # Pre-extract keyframes for every scene (including Scene 1 for S1->S2 inter-scene continuity)
    keyframes_by_scene: dict[int, dict[str, dict[str, Path]]] = {}
    for sc in ordered_scenes:
        sid = int(sc["scene_id"])
        media = sc.get("media") or {}
        cam_a_path = _asset_url_to_path(media.get("cam_a_final_url"))
        cam_b_path = _asset_url_to_path(media.get("cam_b_final_url"))
        keyframes_by_scene[sid] = {}
        if cam_a_path:
            keyframes_by_scene[sid]["cam_a"] = _extract_clip_keyframes(cam_a_path, f"{branch_id}_s{sid}_a")
        if cam_b_path:
            keyframes_by_scene[sid]["cam_b"] = _extract_clip_keyframes(cam_b_path, f"{branch_id}_s{sid}_b")

    eval_scenes = [s for s in ordered_scenes if int(s["scene_id"]) >= 2]
    category_stats: dict[str, dict[str, Any]] = {
        cat: {"passed": 0, "total": 0, "conf_sum": 0.0} for cat in CATEGORY_ORDER
    }
    per_scene_results: list[dict[str, Any]] = []
    human_audit_sample: list[dict[str, Any]] = []

    for idx, sc in enumerate(eval_scenes):
        sid = int(sc["scene_id"])
        prior = [s for s in ordered_scenes if int(s["scene_id"]) < sid]
        step_meta = SIX_SCENE_SCRIPT[idx] if idx < len(SIX_SCENE_SCRIPT) else None
        q_map = build_scene_questions(sc, prior, step_meta)
        scene_kf = keyframes_by_scene.get(sid, {})
        prev_kf = keyframes_by_scene.get(sid - 1, {})

        checks: list[dict[str, Any]] = []

        # A. Single-camera checks (5 categories × 2 cameras = 10 checks/scene)
        for cam_key, cam_label in (("cam_a", "Camera A"), ("cam_b", "Camera B")):
            kf = scene_kf.get(cam_key)
            if not kf:
                for cat, (_slot, q_text) in q_map.items():
                    checks.append(
                        {
                            "category": cat,
                            "camera": cam_key,
                            "question": q_text,
                            "passed": False,
                            "confidence": 0.0,
                            "reason": f"Missing rendered clip for {cam_label}",
                            "frames": [],
                        }
                    )
                    category_stats[cat]["total"] += 1
                continue

            # Batch questions by frame slot (`mid` vs `end`) for fast Decisions API evaluation
            by_slot: dict[str, list[tuple[str, str]]] = {"mid": [], "end": []}
            for cat, (slot, q_text) in q_map.items():
                by_slot[slot].append((cat, q_text))

            for slot, cat_qs in by_slot.items():
                if not cat_qs:
                    continue
                frame_path = kf[slot]
                q_texts = [q for _, q in cat_qs]
                jres = await judge(
                    questions=q_texts,
                    context_text=f"Scene {sid} ({sc.get('title', '')}) — {cam_label} ({slot} frame). Narration: {sc.get('narration', '')}",
                    media_paths=[str(frame_path)],
                )
                rel_frame = f"/assets/eval/frames/{frame_path.name}"
                for (cat, q_text), item in zip(cat_qs, jres.items):
                    checks.append(
                        {
                            "category": cat,
                            "camera": cam_key,
                            "frame_slot": slot,
                            "question": q_text,
                            "passed": bool(item.passed),
                            "confidence": float(item.confidence),
                            "reason": item.reason,
                            "frames": [rel_frame],
                        }
                    )
                    category_stats[cat]["total"] += 1
                    category_stats[cat]["passed"] += int(bool(item.passed))
                    category_stats[cat]["conf_sum"] += float(item.confidence)

        # B. Cross-camera sync checks (start, mid, end = 3 checks/scene)
        kf_a = scene_kf.get("cam_a")
        kf_b = scene_kf.get("cam_b")
        for slot in ("start", "mid", "end"):
            q_cross = (
                f"These two images show the same scene at the exact same {slot} moment from a front viewpoint (image 1) "
                "and a 45-degree right viewpoint (image 2). Do both viewpoints show the silver-haired girl and the black cat "
                "performing the same action with consistent props and room layout?"
            )
            if kf_a and kf_b:
                fa, fb = kf_a[slot], kf_b[slot]
                jres = await judge(
                    questions=[q_cross],
                    context_text=f"Scene {sid} cross-camera sync check ({slot} instant). Narration: {sc.get('narration', '')}",
                    media_paths=[str(fa), str(fb)],
                )
                item = jres.items[0]
                rel_frames = [f"/assets/eval/frames/{fa.name}", f"/assets/eval/frames/{fb.name}"]
                checks.append(
                    {
                        "category": "cross_camera",
                        "camera": "cam_a+cam_b",
                        "frame_slot": slot,
                        "question": q_cross,
                        "passed": bool(item.passed),
                        "confidence": float(item.confidence),
                        "reason": item.reason,
                        "frames": rel_frames,
                    }
                )
                category_stats["cross_camera"]["total"] += 1
                category_stats["cross_camera"]["passed"] += int(bool(item.passed))
                category_stats["cross_camera"]["conf_sum"] += float(item.confidence)
            else:
                checks.append(
                    {
                        "category": "cross_camera",
                        "camera": "cam_a+cam_b",
                        "frame_slot": slot,
                        "question": q_cross,
                        "passed": False,
                        "confidence": 0.0,
                        "reason": "Missing one or both camera clips",
                        "frames": [],
                    }
                )
                category_stats["cross_camera"]["total"] += 1

        # C. Inter-scene continuity checks (prev end -> curr start for cam_a & cam_b = 2 checks/scene)
        for cam_key, cam_label in (("cam_a", "Camera A"), ("cam_b", "Camera B")):
            q_inter = (
                f"Image 1 is the end of Scene {sid - 1} and Image 2 is the start of Scene {sid} from the same {cam_label} viewpoint. "
                "Do the glass conservatory room, furniture, lighting, and characters connect seamlessly without abrupt resets or swapped outfits?"
            )
            prev_end = prev_kf.get(cam_key, {}).get("end")
            curr_start = scene_kf.get(cam_key, {}).get("start")
            if prev_end and curr_start:
                jres = await judge(
                    questions=[q_inter],
                    context_text=f"Inter-scene continuity S{sid - 1}->S{sid} ({cam_label}).",
                    media_paths=[str(prev_end), str(curr_start)],
                )
                item = jres.items[0]
                rel_frames = [f"/assets/eval/frames/{prev_end.name}", f"/assets/eval/frames/{curr_start.name}"]
                checks.append(
                    {
                        "category": "inter_scene",
                        "camera": cam_key,
                        "frame_slot": "prev_end->curr_start",
                        "question": q_inter,
                        "passed": bool(item.passed),
                        "confidence": float(item.confidence),
                        "reason": item.reason,
                        "frames": rel_frames,
                    }
                )
                category_stats["inter_scene"]["total"] += 1
                category_stats["inter_scene"]["passed"] += int(bool(item.passed))
                category_stats["inter_scene"]["conf_sum"] += float(item.confidence)
            else:
                checks.append(
                    {
                        "category": "inter_scene",
                        "camera": cam_key,
                        "frame_slot": "prev_end->curr_start",
                        "question": q_inter,
                        "passed": False,
                        "confidence": 0.0,
                        "reason": "Missing prior or current clip",
                        "frames": [],
                    }
                )
                category_stats["inter_scene"]["total"] += 1

        # Select 5 representative checks per scene (30 total across 6 scenes) for human audit calibration
        audit_picks = [
            next((c for c in checks if c["category"] == "count" and c["camera"] == "cam_a"), None),
            next((c for c in checks if c["category"] == "holding" and c["camera"] == "cam_a"), None),
            next((c for c in checks if c["category"] == "position" and c["camera"] == "cam_b"), None),
            next((c for c in checks if c["category"] == "cross_camera" and c.get("frame_slot") == "mid"), None),
            next((c for c in checks if c["category"] == "inter_scene" and c["camera"] == "cam_a"), None),
        ]
        for pick in audit_picks:
            if pick:
                human_audit_sample.append(
                    {
                        "sample_id": len(human_audit_sample) + 1,
                        "scene_id": sid,
                        "step_key": step_meta["key"] if step_meta else f"scene_{sid}",
                        "category": pick["category"],
                        "camera": pick["camera"],
                        "frames": pick["frames"],
                        "question": pick["question"],
                        "auto_passed": pick["passed"],
                        "auto_confidence": pick["confidence"],
                        "human_label": None,
                    }
                )

        scene_passed = sum(1 for c in checks if c["passed"])
        per_scene_results.append(
            {
                "scene_id": sid,
                "step_key": step_meta["key"] if step_meta else f"scene_{sid}",
                "step_label": step_meta["label"] if step_meta else sc.get("title", f"Scene {sid}"),
                "title": sc.get("title", ""),
                "narration": sc.get("narration", ""),
                "cam_a_final_url": (sc.get("media") or {}).get("cam_a_final_url"),
                "cam_b_final_url": (sc.get("media") or {}).get("cam_b_final_url"),
                "timings": timings_by_scene.get(sid),
                "passed": scene_passed,
                "total": len(checks),
                "pass_rate": round(scene_passed / len(checks), 4) if checks else 0.0,
                "checks": checks,
            }
        )
        print(f"[Eval S{sid}] {scene_passed}/{len(checks)} passed ({round(scene_passed / max(1, len(checks)) * 100, 1)}%)")

    category_summary: dict[str, dict[str, Any]] = {}
    total_passed = 0
    total_checks = 0
    for cat in CATEGORY_ORDER:
        st = category_stats[cat]
        tot = st["total"]
        pas = st["passed"]
        total_passed += pas
        total_checks += tot
        category_summary[cat] = {
            "passed": pas,
            "total": tot,
            "pass_rate": round(pas / tot, 4) if tot else 0.0,
            "avg_confidence": round(st["conf_sum"] / tot, 4) if tot else 0.0,
        }

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method_name,
        "branch_id": branch_id,
        "script_name": "stage1_six_scene_benchmark",
        # v2: the cat's place is read from `elevation`, and the permanence question is asked on the last frame.
        "question_set": "v2",
        "scenes_evaluated": len(per_scene_results),
        "overall": {
            "passed": total_passed,
            "total": total_checks,
            "pass_rate": round(total_passed / total_checks, 4) if total_checks else 0.0,
        },
        "category_summary": category_summary,
        "per_scene": per_scene_results,
        "human_audit_sample_count": len(human_audit_sample),
        "human_audit_sample": human_audit_sample,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[DONE] Wrote video consistency report to {output_path}")
    print(
        json.dumps(
            {
                "branch_id": branch_id,
                "method": method_name,
                "overall": report["overall"],
                "category_summary": category_summary,
                "human_audit_sample_count": len(human_audit_sample),
            },
            indent=2,
        )
    )
    return report


RENDER_BASE_LABELS = {
    "prev_tail": "A_prev_tail",
    "stage3d": "B_stage3d_start",
    "stage3d_first_last": "B2_stage3d_first_last",
    "stage3d_video": "C_stage3d_video",
}


async def rerender_fixed_states(
    source_branch_id: str,
    render_base: str,
    *,
    resolution: str = "720p",
    method_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    Re-render the SAME world states with another render base, so the methods are compared on identical scenes.

    Copies `source_branch_id` (scene 1 keeps its opening clip), settles the world rules on every state, then
    renders scenes 2..N one after another with `config.RENDER_BASE = render_base`. The director is not called.
    """
    from backend.director.world_rules import apply_world_rules
    from backend.media.video_renderer import render_scene_media
    from backend.schemas import BranchState, SceneMedia

    src = get_branch(source_branch_id)
    if not src:
        raise SystemExit(f"Branch not found: {source_branch_id}")
    method = method_name or RENDER_BASE_LABELS.get(render_base, render_base)
    if resolution != "720p":
        method = f"{method}_{resolution}"
    branch = BranchState(**json.loads(src.model_dump_json()))
    branch.branch_id = f"{source_branch_id}_{method}"
    branch.branch_label = f"{method} on the states of {source_branch_id}"
    branch.parent_branch_id = source_branch_id
    scenes = sorted(branch.scenes, key=lambda sc: sc.scene_id)
    for prev, nxt in zip(scenes, scenes[1:]):
        apply_world_rules(prev, nxt)

    refs = [str(config.SHARED_ASSETS_DIR / "lia.png"), str(config.SHARED_ASSETS_DIR / "nyx.png")] + [
        p.image_url for p in branch.props_registry.values()
    ]
    prop_descriptions = {
        pid: ((getattr(p, "appearance_summary", "") or "").strip() or p.name) for pid, p in branch.props_registry.items()
    }
    prop_heights = {pid: float(p.height_m) for pid, p in branch.props_registry.items() if p.height_m}

    old_base, old_res = config.RENDER_BASE, config.SPLIT_RESOLUTION
    config.RENDER_BASE, config.SPLIT_RESOLUTION = render_base, resolution
    step_timings: list[dict[str, Any]] = []
    try:
        for prev, scene in zip(scenes, scenes[1:]):
            scene.media = SceneMedia()
            t0 = time.perf_counter()
            scene.media = await render_scene_media(
                scene,
                refs,
                prev_tail_a=prev.media.cam_a_tail_frame_url,
                prev_tail_b=prev.media.cam_b_tail_frame_url,
                fast_live_mode=True,
                prop_descriptions=prop_descriptions,
                start_state=prev,
                prop_heights=prop_heights,
            )
            ren_sec = round(time.perf_counter() - t0, 2)
            step_timings.append(
                {
                    "step": scene.scene_id - 1,
                    "key": SIX_SCENE_SCRIPT[scene.scene_id - 2]["key"] if scene.scene_id - 2 < len(SIX_SCENE_SCRIPT) else f"scene_{scene.scene_id}",
                    "scene_id": scene.scene_id,
                    "extend_sec": 0.0,
                    "render_sec": ren_sec,
                    "total_sec": ren_sec,
                    "fallback": bool(scene.media.fallback_to_preview),
                }
            )
            print(f"[{method}] S{scene.scene_id} render={ren_sec}s cam_a={scene.media.cam_a_final_url} cam_b={scene.media.cam_b_final_url}")
            save_branch(branch)
    finally:
        config.RENDER_BASE, config.SPLIT_RESOLUTION = old_base, old_res
    return {"branch": json.loads(branch.model_dump_json()), "method": method, "step_timings": step_timings}


def write_comparison(eval_dir: Path = EVAL_DIR) -> dict[str, Any]:
    """Collect every `video_consistency*.json` report into one comparison table (JSON + printed Markdown)."""
    rows: list[dict[str, Any]] = []
    for path in sorted(eval_dir.glob("video_consistency*.json")):
        if path.name == "video_consistency_compare.json":
            continue
        rep = json.loads(path.read_text(encoding="utf-8"))
        timings = [sc["timings"]["render_sec"] for sc in rep.get("per_scene", []) if sc.get("timings")]
        rows.append(
            {
                "method": rep.get("method"),
                "branch_id": rep.get("branch_id"),
                "report": path.name,
                "timestamp": rep.get("timestamp"),
                "question_set": rep.get("question_set", "v1"),
                "overall": rep.get("overall"),
                "categories": {c: rep["category_summary"][c] for c in CATEGORY_ORDER if c in rep.get("category_summary", {})},
                "avg_render_sec": round(sum(timings) / len(timings), 2) if timings else None,
            }
        )
    out = {"timestamp": datetime.now(timezone.utc).isoformat(), "methods": rows}
    (eval_dir / "video_consistency_compare.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    header = "| Method | " + " | ".join(CATEGORY_ORDER) + " | Overall | Render s/scene | Questions |"
    print(header)
    print("|" + "---|" * (len(CATEGORY_ORDER) + 4))
    for r in rows:
        cells = [f"{r['categories'][c]['passed']}/{r['categories'][c]['total']}" if c in r["categories"] else "-" for c in CATEGORY_ORDER]
        ov = r["overall"] or {}
        print(
            f"| {r['method']} | " + " | ".join(cells)
            + f" | {ov.get('passed')}/{ov.get('total')} ({round(100 * ov.get('pass_rate', 0), 1)}%) | {r['avg_render_sec'] or '-'} | {r['question_set']} |"
        )
    return out


async def _main() -> int:
    parser = argparse.ArgumentParser(description="StageGenesis Stage 1 clip-level video consistency benchmark")
    parser.add_argument("--branch-id", type=str, default=None, help="Evaluate an existing branch instead of running the 6-scene script")
    parser.add_argument("--method", type=str, default="A_baseline_prev_tail", help="Method label recorded in the report")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    parser.add_argument("--states-from", type=str, default=None, help="Re-render this branch's world states with --render-base (the director is not called)")
    parser.add_argument("--render-base", type=str, default="prev_tail", choices=sorted(RENDER_BASE_LABELS), help="What each clip is generated from")
    parser.add_argument("--resolution", type=str, default="720p", choices=["720p", "1080p"], help="Resolution of the split-screen generation")
    parser.add_argument("--compare", action="store_true", help="Only collect the existing reports into the comparison table")
    args = parser.parse_args()

    if args.compare:
        write_comparison()
        return 0

    if args.states_from:
        gen = await rerender_fixed_states(args.states_from, args.render_base, resolution=args.resolution)
        branch_dict = gen["branch"]
        step_timings = gen["step_timings"]
        args.method = gen["method"]
        if not args.output:
            args.output = str(EVAL_DIR / f"video_consistency_{args.method}.json")
    elif args.branch_id:
        br = get_branch(args.branch_id)
        if not br:
            raise SystemExit(f"Branch not found: {args.branch_id}")
        branch_dict = json.loads(br.model_dump_json())
        step_timings = None
    else:
        gen = await generate_six_scene_branch(method_name=args.method)
        branch_dict = gen["branch"]
        step_timings = gen["step_timings"]

    await evaluate_branch_clips(
        branch_dict,
        method_name=args.method,
        step_timings=step_timings,
        output_path=Path(args.output or DEFAULT_OUTPUT_PATH),
    )
    write_comparison()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
