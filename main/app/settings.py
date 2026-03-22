from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MAIN_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = MAIN_DIR.parent
THIRD_PARTY_DIR = ROOT_DIR / "third_party"
UI_DIR = MAIN_DIR / "ui"
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"
PERSONAS_DIR = MAIN_DIR / "personas"

CONFIG_PATH = MAIN_DIR / "config.json"
CONFIG_EXAMPLE_PATH = MAIN_DIR / "config.example.json"

OUT_DIR = str(ROOT_DIR / "ai_audio")
DONE_NAME = "test.done"
WAV_NAME = "test.wav"
VOICE_BACKEND = "cosy"  # "cosy" or "edge"
PLAYBACK_BACKEND = "obs"  # "obs" or "local"

OBS_HOST = "172.19.144.1"
OBS_PORT = 4455
OBS_PASSWORD = "xieyi123"
MEDIA_SOURCE_NAME = "ai_audio"

WEB_UI_HOST = "127.0.0.1"
WEB_UI_PORT = 8765

ROOM_CTX_MAX = 20
IDLE_ENTER_S = 8
IDLE_COOLDOWN_S = 5

ACCESS_KEY_ID = "7L67KVtZCgtyjHYuuOEgkfiB"
ACCESS_KEY_SECRET = "cnoBMPNzdnkUyRQIhKxtcnNIYRNoNx"
APP_ID = 1776967369800
ROOM_OWNER_AUTH_CODE = "FD2J7FSQWS1S5"

GIFT_UNAME = "__gift__"
IDLE_UNAME = "__idle__"
LOCAL_UNAME = "__local__"
LOCAL_CMD_UNAME = "__local_cmd__"


@dataclass
class ObsConfig:
    host: str = OBS_HOST
    port: int = OBS_PORT
    password: str = OBS_PASSWORD
    media_source_name: str = MEDIA_SOURCE_NAME
