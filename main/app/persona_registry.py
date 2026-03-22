from __future__ import annotations

import json
from pathlib import Path

from app.persona_models import IdleProfile, PersonaDefinition
from app.settings import PERSONAS_DIR


def _load_idle_profile(path: Path) -> tuple[str, IdleProfile]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    label = str(raw["label"]).strip()
    if not label:
        raise ValueError(f"empty persona label in {path}")

    topic_pool = {
        str(topic): [str(item) for item in items]
        for topic, items in dict(raw["topic_pool"]).items()
    }
    topic_cooldown = {
        str(topic): int(seconds)
        for topic, seconds in dict(raw["topic_cooldown"]).items()
    }

    profile = IdleProfile(
        topic_pool=topic_pool,
        topic_cooldown=topic_cooldown,
        recent_topic_limit=int(raw.get("recent_topic_limit", 2)),
        prefix_task=bool(raw.get("prefix_task", True)),
    )
    return label, profile


def _build_persona_definitions() -> dict[str, PersonaDefinition]:
    definitions: dict[str, PersonaDefinition] = {}

    for persona_dir in sorted(PERSONAS_DIR.iterdir()):
        if not persona_dir.is_dir():
            continue

        key = persona_dir.name
        system_prompt_file = persona_dir / "system.txt"
        idle_profile_file = persona_dir / "idle.json"
        if not system_prompt_file.is_file() or not idle_profile_file.is_file():
            continue

        label, idle_profile = _load_idle_profile(idle_profile_file)
        definitions[key] = PersonaDefinition(
            key=key,
            label=label,
            persona_dir=str(persona_dir),
            system_prompt_file=str(system_prompt_file),
            idle_profile_file=str(idle_profile_file),
            idle_profile=idle_profile,
        )

    return definitions


PERSONA_DEFINITIONS = _build_persona_definitions()
