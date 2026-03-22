# -*- coding: utf-8 -*-
"""Shared helpers/utilities for the AIVtuber project.

Design goals:
- Keep this module free of project runtime state (no globals that change at runtime).
- Put pure functions + small data containers here.
- Keep start.py focused on orchestration (threads, queues, clients, etc.).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
import json
import re

import requests
from app.persona_registry import PERSONA_DEFINITIONS
from app.settings import (
    ACCESS_KEY_ID,
    ACCESS_KEY_SECRET,
    APP_ID,
    IDLE_COOLDOWN_S,
    IDLE_ENTER_S,
    MEDIA_SOURCE_NAME,
    OBS_HOST,
    OBS_PASSWORD,
    OBS_PORT,
    OUT_DIR,
    PLAYBACK_BACKEND,
    ROOM_CTX_MAX,
    ROOM_OWNER_AUTH_CODE,
)


VOICE_OPTIONS: Dict[int, str] = {
    1: "zh-CN-XiaoxiaoNeural",
    2: "zh-CN-XiaoyiNeural",
    3: "zh-CN-YunjianNeural",
    4: "zh-CN-YunxiNeural",
    5: "zh-CN-YunxiaNeural",
    6: "zh-CN-YunyangNeural",
    7: "zh-CN-liaoning-XiaobeiNeural",
    8: "zh-CN-shaanxi-XiaoniNeural",
}


RUNTIME_SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "voice_choice",
        "label": "声音",
        "control": "select",
        "options": [
            {"value": "cosy_lulu", "label": "鹭鹭"},
            {"value": "edge_7", "label": "东北"},
            {"value": "edge_8", "label": "陕西"},
        ],
    },
    {"key": "room_ctx_max", "label": "上下文条数", "control": "range", "min": 1, "max": 100, "step": 1},
    {
        "key": "idle_enabled",
        "label": "自言自语",
        "control": "select",
        "options": [
            {"value": "true", "label": "开启"},
            {"value": "false", "label": "关闭"},
        ],
    },
    {
        "key": "idle_enter_s",
        "label": "进入自言自语秒数",
        "control": "range",
        "min": 0,
        "max": 20,
        "step": 1,
        "disabled_if": {"idle_enabled": "false"},
    },
    {
        "key": "idle_cooldown_s",
        "label": "自言自语冷却秒数",
        "control": "range",
        "min": 0,
        "max": 10,
        "step": 1,
        "disabled_if": {"idle_enabled": "false"},
    },
    {"key": "access_key_id", "label": "直播 Access Key ID", "control": "text"},
    {"key": "access_key_secret", "label": "直播 Access Key Secret", "control": "password"},
    {"key": "app_id", "label": "直播 App ID", "control": "text"},
    {"key": "room_owner_auth_code", "label": "房主授权码", "control": "text"},
    {
        "key": "playback_backend",
        "label": "播放后端",
        "control": "select",
        "options": [
            {"value": "obs", "label": "OBS"},
            {"value": "local", "label": "本地播放"},
        ],
    },
    {"key": "out_dir", "label": "音频输出目录", "control": "text", "visible_if": {"playback_backend": "obs"}},
    {"key": "obs_host", "label": "OBS Host", "control": "text", "visible_if": {"playback_backend": "obs"}},
    {"key": "obs_port", "label": "OBS Port", "control": "text", "visible_if": {"playback_backend": "obs"}},
    {"key": "obs_password", "label": "OBS Password", "control": "password", "visible_if": {"playback_backend": "obs"}},
    {"key": "media_source_name", "label": "OBS 媒体源名称", "control": "text", "visible_if": {"playback_backend": "obs"}},
]


def load_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def build_messages(sys_prompt: str, context_msgs: List[dict], user_text: str) -> List[dict]:
    """Build the OpenAI/DeepSeek style messages list."""
    return (
        [{"role": "system", "content": sys_prompt}]
        + list(context_msgs)
        + [{"role": "user", "content": user_text}]
    )


def compile_banned_pattern(banned: Optional[List[str]]) -> Optional[re.Pattern]:
    if not banned:
        return None
    return re.compile("|".join(map(re.escape, banned)))


def system_prompt_for_mode(
    uname: str,
    base_system_prompt: str,
    gift_append_prompt: str,
    idle_append_prompt: str,
    *,
    gift_uname: str,
    idle_uname: str,
) -> str:
    """Return system prompt with mode-specific append prompt."""
    if uname == idle_uname and idle_append_prompt:
        return f"{base_system_prompt}\n\n{idle_append_prompt}"
    elif uname == gift_uname and gift_append_prompt:
        return f"{base_system_prompt}\n\n{gift_append_prompt}"
    return base_system_prompt


def format_spoken_text(
    uname: str,
    user_text: str,
    answer: str,
    *,
    gift_uname: str,
    idle_uname: str,
    local_uname: str,
    local_cmd_uname: str,
) -> str:
    """Format final text that will be fed into TTS."""
    if uname == idle_uname or uname == local_uname or uname == local_cmd_uname:
        return answer
    if uname == gift_uname:
        return f"{user_text}。{answer}"
    return f"{uname}说，{user_text}。{answer}"


def build_local_command_text(command: str) -> str:
    """Wrap operator input so the model treats it as a control instruction."""
    command = (command or "").strip()
    return (
        "以下内容不是观众弹幕，而是后台操作员给你的直接指令。"
        "你要直接执行，不要把它当成聊天对象，不要说“你说”“主人说”之类的转述。"
        "直接输出适合主播当下立刻播报的内容。\n\n"
        f"指令：{command}"
    )


def call_deepseek_chat_completions(
    messages: List[dict],
    *,
    api_key: str,
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 512,
    timeout_s: float = 20.0,
) -> str:
    """Call DeepSeek Chat Completions API.

    Keep this in utils so start.py doesn't mix orchestration with HTTP details.
    """
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


DEFAULT_GIFT_APPEND_PROMPT = (
    "【当前事件：收到观众送礼】\n"
    "请在感谢之后，自然地接一到两句话，\n"
    "可以表达开心、惊喜，或顺着当前话题延展，\n"
    "不要自我介绍，不要提到系统或提示词。"
)

DEFAULT_IDLE_APPEND_PROMPT = (
    "【当前事件：直播间一段时间没有弹幕了】\n"
    "- 根据接下来的任务输出30-60字\n"
    "- 不连续两次使用同一类型\n"
    "- 不复述最近说过的内容\n"
    "- 避免使用重复句式"
)


@dataclass
class RuntimeConfig:
    out_dir: str
    voice_choice: str
    playback_backend: str
    obs_host: str
    obs_port: str
    obs_password: str
    media_source_name: str
    room_ctx_max: int
    idle_enabled: str
    idle_enter_s: int
    idle_cooldown_s: int
    access_key_id: str
    access_key_secret: str
    app_id: str
    room_owner_auth_code: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def voice_backend(self) -> str:
        return "cosy" if self.voice_choice == "cosy_lulu" else "edge"

    @property
    def edge_voice(self) -> str:
        if self.voice_choice == "edge_8":
            return VOICE_OPTIONS[8]
        return VOICE_OPTIONS[7]


def default_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        out_dir=OUT_DIR,
        voice_choice="edge_7",
        playback_backend=PLAYBACK_BACKEND,
        obs_host=OBS_HOST,
        obs_port=str(OBS_PORT),
        obs_password=OBS_PASSWORD,
        media_source_name=MEDIA_SOURCE_NAME,
        room_ctx_max=ROOM_CTX_MAX,
        idle_enabled="true",
        idle_enter_s=IDLE_ENTER_S,
        idle_cooldown_s=IDLE_COOLDOWN_S,
        access_key_id=ACCESS_KEY_ID,
        access_key_secret=ACCESS_KEY_SECRET,
        app_id=str(APP_ID),
        room_owner_auth_code=ROOM_OWNER_AUTH_CODE,
    )


def build_runtime_config(raw_runtime: Optional[dict[str, Any]]) -> RuntimeConfig:
    raw_runtime = dict(raw_runtime or {})
    defaults = default_runtime_config()

    voice_choice = str(raw_runtime.get("voice_choice", "")).strip().lower()
    if not voice_choice:
        legacy_voice_backend = str(raw_runtime.get("voice_backend", defaults.voice_backend)).strip().lower()
        voice_choice = "cosy_lulu" if legacy_voice_backend == "cosy" else "edge_7"
    if voice_choice not in {"cosy_lulu", "edge_7", "edge_8"}:
        raise ValueError(f"unsupported voice choice: {voice_choice}")

    playback_backend = str(raw_runtime.get("playback_backend", defaults.playback_backend)).strip().lower()
    if playback_backend not in {"obs", "local"}:
        raise ValueError(f"unsupported playback backend: {playback_backend}")

    idle_enabled = str(raw_runtime.get("idle_enabled", defaults.idle_enabled)).strip().lower()
    if idle_enabled not in {"true", "false"}:
        raise ValueError("自言自语开关只能是 true 或 false")

    obs_port = str(raw_runtime.get("obs_port", defaults.obs_port)).strip() or defaults.obs_port
    if not obs_port.isdigit():
        raise ValueError("OBS Port 必须是纯数字字符串")

    app_id = str(raw_runtime.get("app_id", defaults.app_id)).strip() or defaults.app_id
    if not app_id.isdigit():
        raise ValueError("直播 App ID 必须是纯数字字符串")

    return RuntimeConfig(
        out_dir=str(raw_runtime.get("out_dir", defaults.out_dir)).strip() or defaults.out_dir,
        voice_choice=voice_choice,
        playback_backend=playback_backend,
        obs_host=str(raw_runtime.get("obs_host", defaults.obs_host)).strip() or defaults.obs_host,
        obs_port=obs_port,
        obs_password=str(raw_runtime.get("obs_password", defaults.obs_password)),
        media_source_name=str(raw_runtime.get("media_source_name", defaults.media_source_name)).strip()
        or defaults.media_source_name,
        room_ctx_max=max(1, min(100, int(raw_runtime.get("room_ctx_max", defaults.room_ctx_max)))),
        idle_enabled=idle_enabled,
        idle_enter_s=max(0, min(20, int(raw_runtime.get("idle_enter_s", defaults.idle_enter_s)))),
        idle_cooldown_s=max(0, min(10, int(raw_runtime.get("idle_cooldown_s", defaults.idle_cooldown_s)))),
        access_key_id=str(raw_runtime.get("access_key_id", defaults.access_key_id)).strip(),
        access_key_secret=str(raw_runtime.get("access_key_secret", defaults.access_key_secret)).strip(),
        app_id=app_id,
        room_owner_auth_code=str(raw_runtime.get("room_owner_auth_code", defaults.room_owner_auth_code)).strip(),
    )


def save_runtime_config(config_path: str, runtime: RuntimeConfig) -> None:
    path = Path(config_path).resolve()
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    raw["runtime"] = runtime.to_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")


@dataclass
class AppConfig:
    config_path: str
    room_id: int
    speaker: str
    banned: List[str]
    deepseek_api_key: str
    local_only: bool
    persona: str
    runtime: RuntimeConfig

    persona_system_prompts: Dict[str, str]
    gift_append_prompt: str
    idle_append_prompt: str

    @classmethod
    def from_json(cls, path: str) -> "AppConfig":
        config_path = Path(path).resolve()
        config_dir = config_path.parent

        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        persona = str(raw.get("persona", "")).strip().lower()
        if persona not in PERSONA_DEFINITIONS:
            raise ValueError(f"unsupported persona: {persona}")

        local_only = bool(raw.get("local_only", False))
        runtime = build_runtime_config(raw.get("runtime"))
        persona_system_prompts = {
            key: load_text_file(defn.system_prompt_file)
            for key, defn in PERSONA_DEFINITIONS.items()
        }

        return cls(
            config_path=str(config_path),
            room_id=int(raw["room_id"]),
            speaker=str(raw.get("speaker", "")),
            banned=list(raw.get("banned", []) or []),
            deepseek_api_key=str(raw["deepseek_api_key"]),
            local_only=local_only,
            persona=persona,
            runtime=runtime,
            persona_system_prompts=persona_system_prompts,
            gift_append_prompt=None if local_only else DEFAULT_GIFT_APPEND_PROMPT,
            idle_append_prompt=None if local_only else DEFAULT_IDLE_APPEND_PROMPT,
        )
