"""
Centralized Configuration Module for StageState.

Feature & Use Cases:
- Single source of truth for all Gemini 4 model identifiers (Neon, Hydrogen,
  Decisions API `gemini-decision-1`, Omni Flash, Image models), shared asset
  paths, video rendering settings, QA thresholds, and fixed style anchors.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base paths
BASE_DIR: Path = Path(__file__).resolve().parent.parent
SHARED_DIR: Path = BASE_DIR / "shared"
SHARED_ASSETS_DIR: Path = SHARED_DIR / "assets"
SHARED_MOCK_BRANCH_1: Path = SHARED_DIR / "mock_branch_1.json"
SHARED_MOCK_BRANCH_2: Path = SHARED_DIR / "mock_branch_2.json"

BACKEND_DIR: Path = BASE_DIR / "backend"
ASSETS_DIR: Path = BACKEND_DIR / "assets"
ACTORS_DIR: Path = ASSETS_DIR / "actors"
PROPS_DIR: Path = ASSETS_DIR / "props"
PREVIEWS_DIR: Path = ASSETS_DIR / "previews"
FINALS_DIR: Path = ASSETS_DIR / "finals"
TAILS_DIR: Path = ASSETS_DIR / "tails"
BRANCHES_DIR: Path = ASSETS_DIR / "branches"

for _dir in (ASSETS_DIR, ACTORS_DIR, PROPS_DIR, PREVIEWS_DIR, FINALS_DIR, TAILS_DIR, BRANCHES_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# API Credentials & Mode
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
DEMO_MODE: bool = os.getenv("STAGE_STATE_DEMO_MODE", "false").lower() == "true"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# Centralized Gemini 4 Model Registry (live-verified via client.models.list)
MODEL_DIRECTOR: str = os.getenv("MODEL_DIRECTOR", "gemini-4-neon")
MODEL_JUDGE: str = os.getenv("MODEL_JUDGE", "gemini-4-hydrogen")
MODEL_DECISION: str = os.getenv("MODEL_DECISION", "models/gemini-decision-1")
MODEL_VIDEO: str = os.getenv("MODEL_VIDEO", "gemini-omni-flash-preview")
MODEL_IMAGE: str = os.getenv("MODEL_IMAGE", "gemini-3.1-flash-image")
USE_DECISIONS_API: bool = os.getenv("USE_DECISIONS_API", "true").lower() == "true"
# Derive Camera B's first and last frames from Camera A's rendered clip (same instant, +45 degree orbit),
# then interpolate between them, so both cameras show the same room, poses and sizes.
CAM_B_KEYFRAMES_FROM_A: bool = os.getenv("CAM_B_KEYFRAMES_FROM_A", "true").lower() == "true"

# Stage & Video Defaults (tuned for 5-hour hackathon live demo speed)
DEFAULT_SCENE_COUNT: int = int(os.getenv("DEFAULT_SCENE_COUNT", "4"))
VIDEO_RESOLUTION: str = os.getenv("VIDEO_RESOLUTION", "360p")
VIDEO_DURATION_SEC: float = float(os.getenv("VIDEO_DURATION_SEC", "4.0"))
VIDEO_ASPECT_RATIO: str = "16:9"
DUAL_TAKE_COUNT: int = 2

# Consistency Anchor (Layer 3): fixed style text + 2.5D preview start frame (not prev tail frame)
STYLE_ANCHOR_PROMPT: str = (
    "2.5D stylized anime rooftop glass conservatory stage, clean cel-shaded lines, "
    "consistent silver-haired girl Lia in starry dark blue cloak with red umbrella, "
    "and black cat Nyx with golden moon bell collar, crisp spatial framing."
)

# Quality Assurance & Decision Thresholds
QA_PASS_THRESHOLD: float = float(os.getenv("QA_PASS_THRESHOLD", "0.75"))
DECISION_LOW_CONFIDENCE_THRESHOLD: float = float(os.getenv("DECISION_LOW_CONFIDENCE_THRESHOLD", "0.70"))
MAX_QA_RETRIES: int = int(os.getenv("MAX_QA_RETRIES", "2"))
MAX_ACTOR_REFERENCE_IMAGES: int = int(os.getenv("MAX_ACTOR_REFERENCE_IMAGES", "2"))

# Product Security, Upload & Runtime Guardrails
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:4173,http://127.0.0.1:4173,"
    "http://localhost:8000,http://127.0.0.1:8000"
)
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("STAGE_STATE_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",") if o.strip()
]
MAX_CLIP_UPLOAD_BYTES: int = int(os.getenv("MAX_CLIP_UPLOAD_BYTES", str(15 * 1024 * 1024)))
MAX_IMAGE_UPLOAD_BYTES: int = int(os.getenv("MAX_IMAGE_UPLOAD_BYTES", str(5 * 1024 * 1024)))
MAX_PREVIEW_UPLOAD_BYTES: int = int(os.getenv("MAX_PREVIEW_UPLOAD_BYTES", str(20 * 1024 * 1024)))
RENDER_SCENE_TIMEOUT_SEC: float = float(os.getenv("RENDER_SCENE_TIMEOUT_SEC", "95.0"))
DIRECTOR_TIMEOUT_SEC: float = float(os.getenv("DIRECTOR_TIMEOUT_SEC", "28.0"))
DIRECTOR_MAX_RETRIES: int = int(os.getenv("DIRECTOR_MAX_RETRIES", "1"))

# Send the grey 3D layout sketch to the video model as a second image. Off: it pulled clips away from the start frame.
SPLIT_ATTACH_LAYOUT_GUIDE: bool = os.getenv("SPLIT_ATTACH_LAYOUT_GUIDE", "false").lower() == "true"

# What each scene's clip is generated from (see docs/3d_first_experiment.md):
#   prev_tail          - the previous clip's last frames plus text (the original pipeline)
#   stage3d            - a start frame composited from the 3D stage state (positions and counts are computed)
#   stage3d_first_last - start AND end frame composited from the 3D stage state; the video model fills the motion
#   stage3d_video      - the 3D blocking clip is handed to the video model to repaint as anime
RENDER_BASE: str = os.getenv("RENDER_BASE", "prev_tail").strip().lower()
# Resolution of the one split-screen generation. 720p gives 640x360 per camera, 1080p gives 960x540.
SPLIT_RESOLUTION: str = os.getenv("SPLIT_RESOLUTION", "720p").strip().lower()
