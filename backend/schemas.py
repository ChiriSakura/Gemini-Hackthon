"""
Pydantic Data Contract Schemas for StageState.

Feature & Use Cases:
- Mirrors `shared/schema.json` strictly so P1 (Director), P2 (Media), and P3
  (Frontend) communicate over a typed, validated contract with zero drift.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


LightingPreset = Literal["warm_dim", "cold_blue", "warm_transition", "warm_gold_snow"]
VocalEmotion = Literal["neutral", "gentle_whisper", "urgent_emotional", "angry_shout", "joyful", "sad_soft"]
GestureIntent = Literal[
    "none",
    "point_left",
    "point_right",
    "point_center",
    "stop_rewind",
    "beckon_soothe",
    "show_physical_prop",
]


class DirectorInputParsed(BaseModel):
    """Output of Hydrogen Hear/See Agent from a webcam+mic .webm clip or text."""

    target_scene_id: int = Field(default=2, ge=1)
    instruction: str
    vocal_emotion: VocalEmotion = "neutral"
    gesture_intent: GestureIntent = "none"
    direction_hint: Literal["none", "left", "center", "right", "front", "back"] = "none"
    has_physical_prop: bool = False
    prop_name: Optional[str] = None
    prop_description: Optional[str] = None
    prop_frame_timestamp_sec: Optional[float] = None
    mode: Literal["extend", "fork"] = "extend"
    needs_prop_prompt: bool = False
    prop_prompt_hint: Optional[str] = None


class JudgeItem(BaseModel):
    """Single yes/no evaluation item returned by unified `judge()`."""

    question: str
    passed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class JudgeResult(BaseModel):
    """Unified `judge()` output (Hydrogen structured output; swappable with Decisions API)."""

    overall_score: float = Field(ge=0.0, le=1.0)
    all_passed: bool
    items: list[JudgeItem] = Field(default_factory=list)


class ActorState(BaseModel):
    """Represents Lia or Nyx grid coordinate [col, row], facing, action, and held prop."""

    id: Literal["lia", "nyx"]
    name: str
    asset_url: str = ""
    grid: list[float] = Field(min_length=2, max_length=2)
    facing: Literal["left", "right", "front", "center"] = "front"
    action: Literal[
        "stand",
        "walk",
        "kneel",
        "reach",
        "sit",
        "jump",
        "crouch",
        "flee",
        "nuzzle",
        "shared_umbrella",
        "catch_from_camera",
    ] = "stand"
    holding_prop: Optional[str] = None
    height_m: Optional[float] = Field(default=None, ge=0.05, le=3.0)


class StagePropState(BaseModel):
    """Represents a prop positioned on the 3x3 stage grid."""

    id: str
    grid: list[float] = Field(min_length=2, max_length=2)
    on_floor: bool = False
    state: Literal["intact", "broken", "held", "glowing"] = "intact"
    entry_motion: Literal["place", "throw_arc", "held"] = "place"
    entry_origin: Optional[list[float]] = None


class SceneMedia(BaseModel):
    """Stores dual-camera previews, final renders, tail frames, and `judge()` QA metadata."""

    cam_a_preview_url: Optional[str] = None
    cam_b_preview_url: Optional[str] = None
    cam_a_final_url: Optional[str] = None
    cam_b_final_url: Optional[str] = None
    cam_a_tail_frame_url: Optional[str] = None
    cam_b_tail_frame_url: Optional[str] = None
    qa_result: Optional[JudgeResult] = None
    qa_score: float = 0.0
    qa_retries: int = 0
    selected_candidate_index: int = Field(default=0, ge=0, le=1)
    fallback_to_preview: bool = False


class Scene(BaseModel):
    """Complete world-state snapshot and rendered artifacts for a single scene."""

    scene_id: int = Field(ge=1)
    title: str
    duration_sec: float = 4.0
    narration: str
    vocal_emotion: VocalEmotion = "neutral"
    gesture_intent: GestureIntent = "none"
    direction_hint: str = "none"
    lighting: LightingPreset = "warm_dim"
    camera_shake: bool = False
    audio_mood: str = ""
    environment_notes: str = ""
    needs_rerender: bool = True
    reused_from_branch: Optional[str] = None
    end_freeze_mode: Literal["sculpture", "none"] = "sculpture"
    parent_scene_id: Optional[int] = None
    actors: list[ActorState] = Field(default_factory=list)
    stage_props: list[StagePropState] = Field(default_factory=list)
    qa_questions: list[str] = Field(default_factory=list)
    media: SceneMedia = Field(default_factory=SceneMedia)


class PropRegistryItem(BaseModel):
    """Registered prop asset entry in `props_registry`."""

    name: str
    image_url: str
    state: str = "intact"
    origin: Literal["preset", "text_generated", "camera_physical"] = "preset"
    height_m: Optional[float] = Field(default=None, ge=0.05, le=3.0)
    appearance_summary: str = ""


class BranchState(BaseModel):
    """Top-level branch contract matching `shared/schema.json`."""

    branch_id: str
    branch_label: str
    parent_branch_id: Optional[str] = None
    forked_from_scene: Optional[int] = None
    rewrite_summary: Optional[str] = None
    contradiction_warning: Optional[str] = None
    props_registry: dict[str, PropRegistryItem] = Field(default_factory=dict)
    scenes: list[Scene] = Field(default_factory=list)
