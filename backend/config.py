"""
Centralized Configuration Module for StageState.

Feature & Use Cases:
- Single source of truth for all Gemini 4 model identifiers (Neon, Hydrogen,
  Omni Flash, Image models), shared asset paths, video rendering settings,
  QA thresholds, and retry limits.
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

# Centralized Gemini 4 Model Registry (verify exact API model IDs at T0)
MODEL_DIRECTOR: str = os.getenv("MODEL_DIRECTOR", "gemini-4-neon")
MODEL_JUDGE: str = os.getenv("MODEL_JUDGE", "gemini-4-hydrogen")
MODEL_VIDEO: str = os.getenv("MODEL_VIDEO", "omni-flash")
MODEL_IMAGE: str = os.getenv("MODEL_IMAGE", "gemini-3.1-flash-image-preview")
USE_DECISIONS_API: bool = os.getenv("USE_DECISIONS_API", "false").lower() == "true"

# Stage & Video Defaults (tuned for 5-hour hackathon live demo speed)
DEFAULT_SCENE_COUNT: int = int(os.getenv("DEFAULT_SCENE_COUNT", "4"))
VIDEO_RESOLUTION: str = os.getenv("VIDEO_RESOLUTION", "360p")
VIDEO_DURATION_SEC: float = float(os.getenv("VIDEO_DURATION_SEC", "4.0"))
VIDEO_ASPECT_RATIO: str = "16:9"
DUAL_TAKE_COUNT: int = 2

# Quality Assurance & Auto-Repair Thresholds
QA_PASS_THRESHOLD: float = float(os.getenv("QA_PASS_THRESHOLD", "0.75"))
MAX_QA_RETRIES: int = int(os.getenv("MAX_QA_RETRIES", "2"))
MAX_ACTOR_REFERENCE_IMAGES: int = int(os.getenv("MAX_ACTOR_REFERENCE_IMAGES", "2"))
