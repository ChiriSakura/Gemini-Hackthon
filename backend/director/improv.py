"""
Improvised mode: the actors decide for themselves.

In directed mode the director model writes the whole next scene. In improvised mode the user only gives a
situation ("the lights go out", "a loud bang outside"). Each actor, with its own personality and its own memory
of the story so far, says what it wants to do. An arbiter then turns those wishes into ONE new world state:
it applies them in a drawn order, refuses what is no longer possible (the vase someone wanted to pick up was
just knocked off the table), and runs the world rules, so the result is always a legal world.

The actors' wishes come from the judge model (one short call per actor, in parallel). When no model can be
reached they come from a small seeded persona sampler instead, so the mode still works and stays testable.
"""

from __future__ import annotations

import asyncio
import random
from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend import config
from backend.director.timeline import prop_words, stage_spot
from backend.director.world_rules import apply_world_rules, inventory
from backend.schemas import Scene, SceneMedia, StagePropState

Verb = Literal["stay", "go_to", "approach", "pick_up", "put_down", "jump_on_table", "jump_down", "knock_off", "hide"]

PERSONAS: dict[str, dict] = {
    "lia": {
        "name": "Lia",
        "who": "a gentle, careful girl who loves the blue rose in the crystal vase and looks after the cat Nyx. "
               "When something startles her she protects what is fragile first, then comforts Nyx.",
        "verbs": ["stay", "go_to", "approach", "pick_up", "put_down", "hide"],
    },
    "nyx": {
        "name": "Nyx",
        "who": "a curious, mischievous black cat. Nyx is drawn to anything on the table, bats at small things, "
               "bolts and hides when frightened, and comes back to Lia when things are calm.",
        "verbs": ["stay", "go_to", "approach", "jump_on_table", "jump_down", "knock_off", "hide"],
    },
}
SPOTS = {"left": [0.0, 1.0], "right": [2.0, 1.0], "front": [1.0, 1.9], "back": [1.0, 0.1],
         "front left": [0.3, 1.8], "front right": [1.7, 1.8], "back left": [0.2, 0.2], "back right": [1.8, 0.2]}
FRIGHT_WORDS = ("bang", "thunder", "loud", "crash", "dark", "lights go out", "storm", "scream", "stranger", "knock")
CALM_WORDS = ("calm", "quiet", "music", "warm", "sun", "morning", "gentle", "sleep")


class ActorIntent(BaseModel):
    """What one actor wants to do next."""

    actor_id: Literal["lia", "nyx"]
    verb: Verb = "stay"
    target: Optional[str] = Field(default=None, description="a spot name, an object id, 'table'/'floor', or the other actor's id")
    feeling: str = "calm"
    line: str = Field(default="", description="one short third-person sentence of what the actor does")


def actor_memory(actor_id: str, prior_scenes: list[Scene], limit: int = 4) -> list[str]:
    """What this actor remembers: the last few things that happened that involved it."""
    name = PERSONAS[actor_id]["name"].lower()
    words = {name, actor_id} | ({"cat"} if actor_id == "nyx" else {"girl"})
    lines = [s.narration for s in prior_scenes if any(w in s.narration.lower() for w in words)]
    return lines[-limit:]


def describe_world(scene: Scene) -> str:
    parts = []
    for actor in scene.actors:
        where = "on top of the round table" if getattr(actor, "elevation", "floor") == "table" else stage_spot(actor.grid)
        holding = f", holding the {prop_words(actor.holding_prop)}" if actor.holding_prop else ""
        parts.append(f"{actor.name} is {where}{holding}.")
    broken = {p.id for p in scene.stage_props if p.state == "broken"}
    for pid, place in sorted(inventory(scene).items()):
        if place.startswith("held:"):
            continue
        parts.append(f"The {prop_words(pid)} [{pid}] is on the {place}{' (broken)' if pid in broken else ''}.")
    return " ".join(parts)


INTENT_PROMPT = """
You play ONE character on a small stage and decide what that character does next. You are {name}: {who}
You only decide for {name}. Answer with strict JSON for ActorIntent.

Allowed `verb` values for {name}: {verbs}.
- go_to: `target` is one of {spots}.
- approach: `target` is the other character's id ("lia" or "nyx").
- pick_up: `target` is the id in [brackets] of an object that is on the table or the floor and not broken.
- put_down: `target` is "table" or "floor" (only when holding something).
- jump_on_table / jump_down / hide / stay: no target.
- knock_off: `target` is the id of an unbroken object on the table.
`line` is ONE short third-person sentence in plain English describing what {name} does. `feeling` is one word.
""".strip()


async def propose_intent(actor_id: str, situation: str, state: Scene, memory: list[str]) -> ActorIntent:
    """Ask the model what this actor wants to do (temperature is high on purpose: actors may surprise)."""
    from backend.director.genai_client import generate_structured

    persona = PERSONAS[actor_id]
    contents = [
        f"What {persona['name']} remembers: " + (" | ".join(memory) if memory else "nothing special yet."),
        f"The stage right now: {describe_world(state)}",
        f"What just happened: {situation}",
        f"What does {persona['name']} do?",
    ]
    intent = await generate_structured(
        model=config.MODEL_JUDGE,
        system_instruction=INTENT_PROMPT.format(name=persona["name"], who=persona["who"], verbs=persona["verbs"], spots=sorted(SPOTS)),
        contents=contents,
        response_schema=ActorIntent,
        temperature=0.95,
        timeout_sec=20.0,
        max_retries=0,
    )
    intent.actor_id = actor_id  # type: ignore[assignment]
    return intent


def sample_intent(actor_id: str, situation: str, state: Scene, rng: random.Random) -> ActorIntent:
    """The persona sampler used when no model can be reached: weighted choices that fit the character."""
    low = situation.lower()
    scared = any(w in low for w in FRIGHT_WORDS)
    calm = any(w in low for w in CALM_WORDS)
    me = next(a for a in state.actors if a.id == actor_id)
    table_objects = [p.id for p in state.stage_props if not p.on_floor and p.state != "broken"]
    floor_objects = [p.id for p in state.stage_props if p.on_floor and p.state != "broken" and p.id != "red_umbrella"]
    options: list[tuple[float, ActorIntent]] = []

    def add(weight: float, verb: str, target: Optional[str], feeling: str, line: str) -> None:
        if weight > 0:
            options.append((weight, ActorIntent(actor_id=actor_id, verb=verb, target=target, feeling=feeling, line=line)))  # type: ignore[arg-type]

    if actor_id == "lia":
        fragile = next((p for p in table_objects if p == "crystal_vase"), table_objects[0] if table_objects else None)
        if me.holding_prop:
            add(1.5 if calm else 0.6, "put_down", "table", "relieved", f"Lia sets the {prop_words(me.holding_prop)} back down on the table.")
            add(2.0 if scared else 0.5, "go_to", "back left", "protective", f"Lia backs away into the corner, holding the {prop_words(me.holding_prop)} close.")
        elif fragile:
            add(3.0 if scared else 0.8, "pick_up", fragile, "protective", f"Lia hurries to the table and takes the {prop_words(fragile)} into her hands.")
        if floor_objects and not me.holding_prop:
            add(0.7, "pick_up", floor_objects[0], "tidy", f"Lia bends down and picks up the {prop_words(floor_objects[0])}.")
        add(2.0 if scared else 1.2, "approach", "nyx", "caring", "Lia goes over to Nyx and crouches down beside the cat.")
        add(0.6, "go_to", rng.choice(["front", "right", "left"]), "curious", "Lia walks across the room to look around.")
        add(0.8 if calm else 0.3, "stay", None, "calm", "Lia stays where she is and listens.")
    else:
        on_table = getattr(me, "elevation", "floor") == "table"
        if on_table:
            if table_objects:
                add(2.2 if scared else 1.0, "knock_off", rng.choice(table_objects), "startled" if scared else "playful",
                    "Nyx bats at it with a paw and it goes over the edge of the table.")
            add(2.0 if scared else 0.8, "jump_down", None, "startled" if scared else "calm", "Nyx jumps down off the table.")
        else:
            add(0.4 if scared else 1.6, "jump_on_table", None, "curious", "Nyx springs up onto the round table.")
            add(3.0 if scared else 0.2, "hide", None, "frightened", "Nyx bolts to the back corner and hides.")
        add(1.5 if calm else 0.7, "approach", "lia", "affectionate", "Nyx trots over to Lia and rubs against her legs.")
        add(0.5, "stay", None, "watchful", "Nyx sits still, tail flicking.")
    total = sum(w for w, _ in options)
    pick = rng.random() * total
    for weight, intent in options:
        pick -= weight
        if pick <= 0:
            return intent
    return options[-1][1]


def _apply_intent(scene: Scene, intent: ActorIntent) -> Optional[str]:
    """Apply one intent to `scene` in place. Returns None when done, or a reason why it is not possible."""
    me = next((a for a in scene.actors if a.id == intent.actor_id), None)
    other = next((a for a in scene.actors if a.id != intent.actor_id), None)
    if me is None:
        return "that actor is not on stage"
    if intent.verb not in PERSONAS[intent.actor_id]["verbs"]:
        return f"{me.name} cannot do that"
    on_table = getattr(me, "elevation", "floor") == "table"
    if intent.verb == "stay":
        me.action = "sit" if me.id == "nyx" else "stand"
    elif intent.verb == "go_to":
        if intent.target not in SPOTS:
            return "there is no such spot"
        me.grid, me.elevation, me.action = list(SPOTS[intent.target]), "floor", "walk"
    elif intent.verb == "hide":
        me.grid, me.elevation, me.action = list(SPOTS["back right" if me.id == "nyx" else "back left"]), "floor", "flee" if me.id == "nyx" else "crouch"
    elif intent.verb == "approach":
        if other is None:
            return "there is nobody to go to"
        if getattr(other, "elevation", "floor") == "table":
            me.grid = [0.5, 1.0]
        else:
            me.grid = [round(min(2.0, max(0.0, other.grid[0] + (-0.35 if other.grid[0] > 1 else 0.35))), 2), round(other.grid[1], 2)]
        me.elevation, me.action = "floor", "nuzzle" if me.id == "nyx" else "kneel"
    elif intent.verb == "pick_up":
        prop = next((p for p in scene.stage_props if p.id == intent.target), None)
        if prop is None:
            return f"the {prop_words(intent.target or 'object')} is not there to pick up"
        if prop.state == "broken":
            return f"the {prop_words(prop.id)} is broken"
        if me.holding_prop:
            scene.stage_props.append(StagePropState(id=me.holding_prop, grid=[round(max(0.0, me.grid[0] - 0.3), 2), round(me.grid[1], 2)], on_floor=True))
        if not prop.on_floor:
            me.grid = [0.5, 1.0] if me.grid[0] <= 1.0 else [1.5, 1.0]
        else:
            me.grid = [prop.grid[0], prop.grid[1]]
        scene.stage_props = [p for p in scene.stage_props if p.id != prop.id]
        me.holding_prop, me.action = prop.id, "hold_up"
    elif intent.verb == "put_down":
        if not me.holding_prop:
            return f"{me.name} is not holding anything"
        on_floor = intent.target == "floor"
        if not on_floor:
            me.grid = [0.5, 1.0] if me.grid[0] <= 1.0 else [1.5, 1.0]
        grid = [round(min(2.0, me.grid[0] + 0.25), 2), round(me.grid[1], 2)] if on_floor else [1.0, 1.0]
        scene.stage_props.append(StagePropState(id=me.holding_prop, grid=grid, on_floor=on_floor))
        me.holding_prop, me.action = None, "stand"
    elif intent.verb == "jump_on_table":
        if on_table:
            return f"{me.name} is already on the table"
        me.grid, me.elevation, me.action = [1.0, 1.0], "table", "jump"
    elif intent.verb == "jump_down":
        if not on_table:
            return f"{me.name} is not on the table"
        me.grid, me.elevation, me.action = [1.75, 1.45], "floor", "walk"
    elif intent.verb == "knock_off":
        prop = next((p for p in scene.stage_props if p.id == intent.target and not p.on_floor), None)
        if prop is None:
            return f"the {prop_words(intent.target or 'object')} is not on the table any more"
        if prop.state == "broken":
            return f"the {prop_words(prop.id)} is already broken"
        me.grid, me.elevation, me.action = [1.0, 1.0], "table", "reach"
        prop.on_floor, prop.grid = True, [1.0, 1.55]
        prop.state = "broken" if prop.id == "crystal_vase" or "glass" in prop.id or "mug" in prop.id or "cup" in prop.id else prop.state
    return None


def arbitrate(prev: Scene, intents: list[ActorIntent], situation: str, rng: random.Random) -> tuple[Scene, dict]:
    """
    Turn the actors' wishes into one legal world state.

    The wishes are applied in a drawn order (whoever is faster this time). A wish that is no longer possible is
    dropped and noted. The world rules run last, so the result can never hold two of an object or a girl on the table.
    """
    scene = prev.model_copy(deep=True)
    scene.scene_id = prev.scene_id + 1
    scene.parent_scene_id = prev.scene_id
    scene.media = SceneMedia()
    scene.needs_rerender = True
    scene.reused_from_branch = None
    scene.title = "Improvised"
    for actor in scene.actors:
        actor.action = "sit" if actor.id == "nyx" else "stand"
    order = list(intents)
    rng.shuffle(order)
    sentences: list[str] = [situation.strip().rstrip(".") + "."] if situation.strip() else []
    applied: list[dict] = []
    for intent in order:
        why_not = _apply_intent(scene, intent)
        name = PERSONAS[intent.actor_id]["name"]
        if why_not is None:
            line = intent.line.strip() or f"{name} reacts."
            if intent.verb == "knock_off" and intent.target:
                thing = prop_words(intent.target)
                broke = any(p.id == intent.target and p.state == "broken" for p in scene.stage_props)
                line = f"{name} bats the {thing} off the table and it {'breaks on the floor' if broke else 'lands on the floor'}."
            sentences.append(line if line.endswith((".", "!", "?")) else line + ".")
            applied.append({"actor": intent.actor_id, "verb": intent.verb, "target": intent.target, "feeling": intent.feeling, "applied": True})
        else:
            sentences.append(f"{name} wants to act, but {why_not}.")
            applied.append({"actor": intent.actor_id, "verb": intent.verb, "target": intent.target, "feeling": intent.feeling,
                            "applied": False, "why_not": why_not})
    scene.narration = " ".join(sentences)
    scene.qa_questions = [f"Does the clip show: {s}" for s in sentences[1:4]] or [f"Does the clip show: {scene.narration}"]
    apply_world_rules(prev, scene)
    return scene, {"order": [i.actor_id for i in order], "intents": applied}


async def improvise_scene(
    situation: str, prior_scenes: list[Scene], seed: Optional[int] = None, use_model: Optional[bool] = None
) -> tuple[Scene, dict]:
    """One improvised scene after `prior_scenes[-1]`. Returns the new scene and a report of who wanted what."""
    prev = prior_scenes[-1]
    rng = random.Random(seed)
    if use_model is None:
        use_model = bool(config.GEMINI_API_KEY) and not config.DEMO_MODE
    source = "model"
    intents: list[ActorIntent] = []
    if use_model:
        results = await asyncio.gather(
            *(propose_intent(a.id, situation, prev, actor_memory(a.id, prior_scenes)) for a in prev.actors),
            return_exceptions=True,
        )
        if any(isinstance(r, Exception) for r in results):
            use_model = False  # the model could not be reached: fall back to the persona sampler for this scene
        else:
            intents = list(results)  # type: ignore[arg-type]
    if not use_model:
        source = "persona_sampler"
        intents = [sample_intent(a.id, situation, prev, rng) for a in prev.actors]
    scene, report = arbitrate(prev, intents, situation, rng)
    report["intent_source"] = source
    return scene, report
