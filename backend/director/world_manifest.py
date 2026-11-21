"""
World Pack Manifest Abstraction (Productization Foundation).

Feature & Use Cases:
- Decouples the active stage world (style anchor, fixed room camera refs, cast
  metadata & SI heights, lighting presets, and fast-recolor prop templates) from
  hardcoded prompt strings so StageState can evolve cleanly into multi-world
  creator packs via `GET /api/world/manifest`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend import config


class WorldActorSpec(BaseModel):
    id: str
    name: str
    height_m: float = Field(gt=0.0, le=3.0)
    asset_url: str
    visual_anchor: str


class WorldManifest(BaseModel):
    world_id: str
    title: str
    version: str = "1.0.0"
    style_anchor: str
    grid_size: tuple[int, int] = (3, 3)
    table_height_m: float = 0.75
    room_refs: dict[str, str] = Field(default_factory=dict)
    lighting_presets: list[str] = Field(default_factory=list)
    actors: list[WorldActorSpec] = Field(default_factory=list)
    fast_prop_templates: list[str] = Field(default_factory=list)
    protected_branches: list[str] = Field(default_factory=list)


def get_active_world_manifest() -> WorldManifest:
    """Build the active StageState WorldManifest from disk assets and config."""
    stages_dir = config.ASSETS_DIR / "stages"
    room_refs: dict[str, str] = {}
    for cam in ("cam_a", "cam_b"):
        candidate = stages_dir / f"conservatory_{cam}.png"
        if candidate.is_file():
            room_refs[cam] = f"/assets/stages/{candidate.name}"

    templates_dir = config.PROPS_DIR / "templates"
    fast_templates = (
        sorted(p.stem for p in templates_dir.glob("*.png"))
        if templates_dir.is_dir()
        else []
    )

    protected = sorted(
        p.stem
        for p in config.BRANCHES_DIR.glob("*.json")
        if p.stem == "branch_v1" or p.stem.endswith("_cached")
    )

    return WorldManifest(
        world_id="glass_conservatory_v1",
        title="Midnight Glass Conservatory",
        style_anchor=config.STYLE_ANCHOR_PROMPT,
        room_refs=room_refs,
        lighting_presets=["warm_dim", "cold_blue", "warm_transition", "warm_gold_snow"],
        actors=[
            WorldActorSpec(
                id="lia",
                name="Lia",
                height_m=1.62,
                asset_url="/assets/actors/lia.png",
                visual_anchor="silver-haired anime girl in starry midnight-blue cloak",
            ),
            WorldActorSpec(
                id="nyx",
                name="Nyx",
                height_m=0.30,
                asset_url="/assets/actors/nyx.png",
                visual_anchor="small realistic domestic black cat with golden moon collar",
            ),
        ],
        fast_prop_templates=fast_templates,
        protected_branches=protected,
    )
