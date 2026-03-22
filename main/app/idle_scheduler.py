from __future__ import annotations

import random
import time
from collections import deque

from app.persona_models import PersonaDefinition


class IdleScheduler:
    def __init__(self, persona: PersonaDefinition):
        self._topic_last_used: dict[str, float] = {}
        self._recent_topics: deque[str] = deque(maxlen=persona.idle_profile.recent_topic_limit)
        self.persona = persona

    def set_persona(self, persona: PersonaDefinition) -> None:
        self.persona = persona
        self._topic_last_used.clear()
        self._recent_topics = deque(maxlen=persona.idle_profile.recent_topic_limit)

    def pick_idle_topic(self) -> str:
        now = time.time()
        candidates: list[str] = []

        for topic in self.persona.idle_profile.topic_pool:
            last = self._topic_last_used.get(topic, 0.0)
            cooldown = self.persona.idle_profile.topic_cooldown.get(topic, 300)
            if now - last >= cooldown:
                candidates.append(topic)

        if not candidates:
            candidates = list(self.persona.idle_profile.topic_pool)

        recent = list(self._recent_topics)
        filtered = [topic for topic in candidates if topic not in recent]
        if filtered:
            candidates = filtered

        return random.choice(candidates)

    def make_idle_user_text(self) -> str:
        topic = self.pick_idle_topic()
        task = random.choice(self.persona.idle_profile.topic_pool[topic])
        self._topic_last_used[topic] = time.time()
        self._recent_topics.append(topic)
        if self.persona.idle_profile.prefix_task:
            return f"任务：{task}。"
        return task
