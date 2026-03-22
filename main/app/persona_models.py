from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IdleProfile:
    topic_pool: dict[str, list[str]]
    topic_cooldown: dict[str, int]
    recent_topic_limit: int = 2
    prefix_task: bool = True


@dataclass(frozen=True)
class PersonaDefinition:
    key: str
    label: str
    persona_dir: str
    system_prompt_file: str
    idle_profile_file: str
    idle_profile: IdleProfile
