from __future__ import annotations
import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from typing import Deque

import blivedm
import blivedm.models.web as web_models
from obsws_python import ReqClient

from app.tts import CosyVoiceTTS
from app.idle_scheduler import IdleScheduler
from app.persona_registry import PERSONA_DEFINITIONS
from app.settings import (
    DONE_NAME,
    GIFT_UNAME,
    IDLE_UNAME,
    LOCAL_CMD_UNAME,
    LOCAL_UNAME,
    THIRD_PARTY_DIR,
    WAV_NAME,
)
from app.config_utils import (
    AppConfig,
    RuntimeConfig,
    build_messages,
    build_local_command_text,
    call_deepseek_chat_completions,
    compile_banned_pattern,
    format_spoken_text,
    load_text_file,
    save_runtime_config,
    system_prompt_for_mode,
)


log = logging.getLogger("aivtuber")


class AIVtuberApp:
    def __init__(self, config: AppConfig):
        self.config = config
        self.runtime = config.runtime
        self.personas = PERSONA_DEFINITIONS

        os.makedirs(self.runtime.out_dir, exist_ok=True)
        self.ws = self._build_obs_client()

        self.last_chat_ts = 0
        self.last_idle_ts = 0
        self.gen = 0
        self.inflight = 0
        self.inflight_lock = threading.Lock()
        self.is_initializing = True
        self.is_ready = False
        self.init_error = ""
        self.runtime_started = False
        self.runtime_ready = False
        self.cosy_ready = False
        self.cosy_loading = False
        self.cosy_error = ""
        self._cosy_init_thread: threading.Thread | None = None

        self.room_context: Deque[dict] = deque(maxlen=self.runtime.room_ctx_max)
        self.ui_events: Deque[dict] = deque(maxlen=60)
        self.ui_lock = threading.Lock()
        self.in_queue = queue.Queue(maxsize=20)
        self.llm_queue = queue.Queue(maxsize=10)
        self.play_queue = queue.Queue(maxsize=5)

        self.banned_pattern = compile_banned_pattern(config.banned)
        self.persona_key = config.persona
        self.persona = self.personas[self.persona_key]
        self.idle_scheduler = IdleScheduler(self.persona)
        self._persona_prompt_mtimes = {
            key: self._safe_mtime(persona.system_prompt_file)
            for key, persona in self.personas.items()
        }

        self.tts = CosyVoiceTTS(
            worker_py=str(THIRD_PARTY_DIR / "CosyVoice" / "worker.py"),
            python_bin=sys.executable,
            backend=self.runtime.voice_backend,
            playback_backend=self.runtime.playback_backend,
            edge_voice=self.runtime.edge_voice,
            out_dir=self.runtime.out_dir,
            wav_name=WAV_NAME,
            done_name=DONE_NAME,
            tts_timeout_s=20.0,
        )

        self.local_only = getattr(config, "local_only", False)
        if not self.local_only:
            self.handler = BiliHandler(self)

    def initialize_runtime(self) -> None:
        self.is_initializing = True
        self.is_ready = False
        self.init_error = ""
        self.runtime_ready = False
        try:
            self.tts.cleanup_temp_audio()
            if self.runtime.voice_backend == "cosy":
                self.tts.ensure_started()
            warmup_wav = self.tts.generate_wav(
                "鹭神启动",
                retry_once=False,
                timeout_s=60,
            )
            if not warmup_wav:
                raise RuntimeError("TTS 预热失败")
            if self.runtime.voice_backend == "cosy":
                self.cosy_ready = True
                self.cosy_error = ""
            if not self.runtime_started:
                threading.Thread(target=self.llm_worker_loop, daemon=True).start()
                threading.Thread(target=self.tts_generate_loop, daemon=True).start()
                threading.Thread(target=self.play_worker_loop, daemon=True).start()
                threading.Thread(target=self.idle_llm_loop, daemon=True).start()
                self.runtime_started = True
            self.runtime_ready = True
            self._sync_input_gate()
            if self.runtime.voice_backend != "cosy":
                self._start_cosy_warmup(lock_input=False)
        except Exception as exc:
            self.init_error = str(exc)
            log.exception("runtime init failed: %s", exc)
        finally:
            if not self.runtime_ready:
                self.is_initializing = False

    def _sync_input_gate(self) -> None:
        if not self.runtime_ready:
            self.is_ready = False
            return

        if self.runtime.voice_backend == "cosy":
            if self.cosy_ready:
                self.is_initializing = False
                self.is_ready = True
                self.init_error = ""
            else:
                self.is_initializing = self.cosy_loading
                self.is_ready = False
                self.init_error = self.cosy_error
            return

        self.is_initializing = False
        self.is_ready = True
        self.init_error = ""

    def _start_cosy_warmup(self, *, lock_input: bool) -> None:
        if self.cosy_ready:
            self._sync_input_gate()
            return
        if self._cosy_init_thread and self._cosy_init_thread.is_alive():
            if lock_input:
                self._sync_input_gate()
            return

        self.cosy_loading = True
        self.cosy_error = ""
        if lock_input:
            self._sync_input_gate()

        def runner() -> None:
            try:
                self.tts.ensure_started()
                warmup_wav = self.tts.generate_wav(
                    "鹭神启动",
                    retry_once=False,
                    timeout_s=60,
                )
                if not warmup_wav:
                    raise RuntimeError("CosyVoice 预热失败")
                self.cosy_ready = True
                self.cosy_error = ""
                self.add_ui_event("system", "CosyVoice 初始化完成")
            except Exception as exc:
                self.cosy_ready = False
                self.cosy_error = str(exc)
                log.exception("cosy warmup failed: %s", exc)
            finally:
                self.cosy_loading = False
                self._sync_input_gate()

        self._cosy_init_thread = threading.Thread(target=runner, daemon=True)
        self._cosy_init_thread.start()

    def _build_obs_client(self) -> ReqClient | None:
        if self.runtime.playback_backend != "obs":
            return None
        try:
            return ReqClient(
                host=self.runtime.obs_host,
                port=int(self.runtime.obs_port),
                password=self.runtime.obs_password,
                timeout=2,
            )
        except Exception as exc:
            message = (
                "OBS connection failed: host=%s port=%s error=%s. "
                "OBS playback backend requires OBS to be running and reachable."
            )
            log.error(
                message,
                self.runtime.obs_host,
                self.runtime.obs_port,
                exc,
            )
            raise RuntimeError(
                "OBS 未连接成功。当前播放后端为 obs，请先打开 OBS 并确认 WebSocket 配置正确。"
            ) from exc

    def _update_room_context_limit(self, maxlen: int) -> None:
        self.room_context = deque(list(self.room_context)[-maxlen:], maxlen=maxlen)

    @staticmethod
    def _safe_mtime(path: str) -> float | None:
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _refresh_system_prompt_if_needed(self, persona_key: str) -> bool:
        persona = self.personas[persona_key]
        latest_mtime = self._safe_mtime(persona.system_prompt_file)
        previous_mtime = self._persona_prompt_mtimes.get(persona_key)
        if latest_mtime is None or latest_mtime == previous_mtime:
            return False

        prompt_text = load_text_file(persona.system_prompt_file)
        if not prompt_text:
            log.warning("Skipping system prompt reload for %s: empty or unreadable file", persona_key)
            self._persona_prompt_mtimes[persona_key] = latest_mtime
            return False

        self.config.persona_system_prompts[persona_key] = prompt_text
        self._persona_prompt_mtimes[persona_key] = latest_mtime

        if persona_key == self.persona_key:
            self.room_context.clear()
            self.last_idle_ts = 0
            self.add_ui_event("system", f"{persona.label} 的 system prompt 已热更新，上下文已重置")
        else:
            log.info("Reloaded system prompt for persona: %s", persona_key)
        return True

    def update_runtime_settings(self, raw_runtime: dict) -> None:
        runtime = RuntimeConfig(**raw_runtime)
        obs_changed = (
            runtime.playback_backend != self.runtime.playback_backend
            or runtime.obs_host != self.runtime.obs_host
            or runtime.obs_port != self.runtime.obs_port
            or runtime.obs_password != self.runtime.obs_password
        )
        room_ctx_changed = runtime.room_ctx_max != self.runtime.room_ctx_max
        out_dir_changed = runtime.out_dir != self.runtime.out_dir
        self.runtime = runtime
        self.config.runtime = runtime
        save_runtime_config(self.config.config_path, runtime)
        self.tts.update_runtime(
            backend=runtime.voice_backend,
            playback_backend=runtime.playback_backend,
            edge_voice=runtime.edge_voice,
            out_dir=runtime.out_dir,
        )
        if room_ctx_changed:
            self._update_room_context_limit(runtime.room_ctx_max)
        if obs_changed:
            self.ws = self._build_obs_client()
        if out_dir_changed:
            self.cosy_ready = False
            self.cosy_error = ""
        if not self.cosy_ready:
            self._start_cosy_warmup(lock_input=(runtime.voice_backend == "cosy"))
        else:
            self._sync_input_gate()

    def _current_system_prompt(self, persona_key: str) -> str:
        self._refresh_system_prompt_if_needed(persona_key)
        return self.config.persona_system_prompts[persona_key]

    def _apply_persona_switch(self, persona_key: str) -> None:
        self.persona_key = persona_key
        self.persona = self.personas[persona_key]
        self.idle_scheduler.set_persona(self.persona)
        self.room_context.clear()
        self.last_idle_ts = 0
        self.add_ui_event("system", f"人格已切换为 {self.persona.label}")

    def request_persona_switch(self, persona_key: str) -> str:
        persona_key = (persona_key or "").strip().lower()
        if persona_key not in self.personas:
            raise ValueError(f"unsupported persona: {persona_key}")
        if persona_key == self.persona_key:
            return "noop"

        self._refresh_system_prompt_if_needed(persona_key)
        self._apply_persona_switch(persona_key)
        return "switched"

    def add_ui_event(self, role: str, text: str, *, uname: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        with self.ui_lock:
            self.ui_events.append(
                {
                    "ts": time.time(),
                    "role": role,
                    "uname": uname,
                    "text": text,
                }
            )

    def ui_state(self) -> dict:
        self._refresh_system_prompt_if_needed(self.persona_key)
        with self.ui_lock:
            events = list(self.ui_events)
        with self.inflight_lock:
            inflight = self.inflight
        return {
            "server_time": time.strftime("%H:%M:%S"),
            "is_initializing": self.is_initializing,
            "is_ready": self.is_ready,
            "init_error": self.init_error,
            "current_persona": {
                "key": self.persona.key,
                "label": self.persona.label,
            },
            "personas": [
                {"key": persona.key, "label": persona.label}
                for persona in self.personas.values()
            ],
            "runtime_settings": self.runtime.to_dict(),
            "inflight": inflight,
            "context_size": len(self.room_context),
            "in_queue_size": self.in_queue.qsize(),
            "llm_queue_size": self.llm_queue.qsize(),
            "play_queue_size": self.play_queue.qsize(),
            "events": [
                {
                    "role": item["role"],
                    "role_label": self._ui_role_label(item["role"], item.get("uname", "")),
                    "ts_label": time.strftime("%H:%M:%S", time.localtime(item["ts"])),
                    "text": item["text"],
                }
                for item in events
            ],
        }

    @staticmethod
    def _ui_role_label(role: str, uname: str) -> str:
        if role == "user":
            return f"用户 {uname or ''}".strip()
        if role == "cmd":
            return "直接指令"
        if role == "assistant":
            return "AI 回复"
        if role == "gift":
            return "礼物事件"
        if role == "idle":
            return "自嗨触发"
        if role == "system":
            return "系统提示"
        return role

    def submit_local_text(self, mode: str, text: str) -> None:
        if not self.is_ready:
            raise RuntimeError(self.init_error or "CosyVoice 正在初始化，请稍后再发送")
        text = (text or "").strip()
        if not text:
            return
        if mode == "cmd":
            self.add_ui_event("cmd", text)
            self.enqueue_text(LOCAL_CMD_UNAME, build_local_command_text(text))
        else:
            self.add_ui_event("user", text, uname="local")
            self.enqueue_text(LOCAL_UNAME, text)

    def _finish_item(self) -> None:
        with self.inflight_lock:
            self.inflight -= 1

    def enqueue_text(self, uname: str, user_text: str) -> None:
        if uname != IDLE_UNAME:
            self.gen += 1
        gen = self.gen
        context_snapshot = tuple(dict(msg) for msg in self.room_context)
        item = (time.time(), gen, uname, user_text, self.persona_key, context_snapshot)
        with self.in_queue.mutex:
            if self.in_queue.maxsize > 0 and len(self.in_queue.queue) >= self.in_queue.maxsize:
                self.in_queue.queue.popleft()

            if uname == GIFT_UNAME:
                self.in_queue.queue.appendleft(item)
            else:
                self.in_queue.queue.append(item)

            self.in_queue.unfinished_tasks += 1
            self.in_queue.not_empty.notify()
            log.debug("queue snapshot: %s", list(self.in_queue.queue))
        with self.inflight_lock:
            self.inflight += 1

    def append_room_context(self, uname: str, user_text: str, assistant_text: str) -> None:
        self.room_context.append({"role": "user", "content": f"{uname}: {user_text}"})
        self.room_context.append({"role": "assistant", "content": assistant_text})

    def debug_print_room_context(self) -> None:
        if not self.room_context:
            log.info("[context] <empty>")
            return

        log.info("[context]:")
        for i, msg in enumerate(self.room_context):
            log.info("  %s. (%s): %s", i, msg.get("role"), msg.get("content"))

    def _call_llm(self, uname: str, user_text: str, *, persona_key: str, context_msgs: list[dict]) -> str:
        sys_prompt = system_prompt_for_mode(
            uname,
            self._current_system_prompt(persona_key),
            self.config.gift_append_prompt,
            self.config.idle_append_prompt,
            gift_uname=GIFT_UNAME,
            idle_uname=IDLE_UNAME,
        )
        messages = build_messages(sys_prompt, context_msgs, user_text)
        return call_deepseek_chat_completions(messages, api_key=self.config.deepseek_api_key)

    def llm_worker_loop(self) -> None:
        while True:
            ts, gen, uname, user_text, persona_key, context_snapshot = self.in_queue.get()
            try:
                if uname != GIFT_UNAME:
                    if time.time() - ts > 60:
                        log.info("skipping stale item in llm_worker_loop: %s", user_text)
                        self._finish_item()
                        continue
                    if uname == IDLE_UNAME and gen != self.gen:
                        self._finish_item()
                        continue
                try:
                    answer = self._call_llm(
                        uname,
                        user_text,
                        persona_key=persona_key,
                        context_msgs=list(context_snapshot),
                    )
                except Exception as exc:
                    log.exception("deepseek error: %s", exc)
                    self._finish_item()
                    continue
                answer = answer.replace("\n", " ").strip()
                if not answer:
                    self._finish_item()
                    continue
                if persona_key == self.persona_key:
                    self.append_room_context(uname, user_text, answer)
                self.add_ui_event("assistant", answer)
                # self.debug_print_room_context()
                spoken = format_spoken_text(
                    uname,
                    user_text,
                    answer,
                    gift_uname=GIFT_UNAME,
                    idle_uname=IDLE_UNAME,
                    local_uname=LOCAL_UNAME,
                    local_cmd_uname=LOCAL_CMD_UNAME,
                )
                with self.llm_queue.mutex:
                    if self.llm_queue.maxsize > 0 and len(self.llm_queue.queue) >= self.llm_queue.maxsize:
                        self.llm_queue.queue.popleft()
                    self.llm_queue.queue.append((ts, gen, uname, user_text, persona_key, spoken))
                    self.llm_queue.unfinished_tasks += 1
                    self.llm_queue.not_empty.notify()
            finally:
                self.in_queue.task_done()

    def tts_generate_loop(self) -> None:
        while True:
            ts, gen, uname, user_text, persona_key, spoken = self.llm_queue.get()
            try:
                if uname == IDLE_UNAME and gen != self.gen:
                    self._finish_item()
                    continue
                if uname != GIFT_UNAME and time.time() - ts > 60:
                    log.info("skipping stale item in tts_generate_loop: %s", user_text)
                    self._finish_item()
                    continue
                wav_path = self.tts.generate_wav(spoken, timeout_s=60, retry_once=True)
                if not wav_path:
                    log.error("TTS generate failed")
                    log.error(self.tts.stderr_tail(200))
                    self._finish_item()
                    continue
                with self.play_queue.mutex:
                    if self.play_queue.maxsize > 0 and len(self.play_queue.queue) >= self.play_queue.maxsize:
                        self.play_queue.queue.popleft()
                    self.play_queue.queue.append((ts, gen, uname, user_text, persona_key, wav_path))
                    self.play_queue.unfinished_tasks += 1
                    self.play_queue.not_empty.notify()
            except Exception as exc:
                log.exception("tts_generate_loop error: %s", exc)
            finally:
                self.llm_queue.task_done()

    def play_worker_loop(self) -> None:
        while True:
            ts, gen, uname, user_text, persona_key, wav_path = self.play_queue.get()
            try:
                if uname == IDLE_UNAME and gen != self.gen:
                    self._finish_item()
                    continue
                if uname != GIFT_UNAME and time.time() - ts > 60:
                    log.info("skipping stale item in play_worker_loop: %s", user_text)
                    self._finish_item()
                    continue
                ok = self.tts.play_wav(
                    wav_path,
                    ws=self.ws,
                    media_source_name=self.runtime.media_source_name,
                    sleep_until_done=True,
                )
                if not ok:
                    log.error("play_wav failed")
                if uname == IDLE_UNAME:
                    self.last_idle_ts = time.time()
                else:
                    self.last_chat_ts = time.time()
                self._finish_item()
            except Exception as exc:
                log.exception("play_worker_loop error: %s", exc)
                self._finish_item()
            finally:
                self.play_queue.task_done()

    def idle_llm_loop(self) -> None:
        while True:
            try:
                with self.inflight_lock:
                    if self.inflight != 0:
                        continue
                now = time.time()
                if not (
                    self.runtime.idle_enabled == "true"
                    and
                    now - self.last_chat_ts > self.runtime.idle_enter_s
                    and now - self.last_idle_ts > self.runtime.idle_cooldown_s
                    and len(self.room_context) > 0
                ):
                    time.sleep(0.5)
                    continue
                idle_user_text = self.idle_scheduler.make_idle_user_text()
                self.add_ui_event("idle", idle_user_text)
                self.enqueue_text(IDLE_UNAME, idle_user_text)
                time.sleep(0.5)
            except Exception as exc:
                log.exception("[idle_llm_loop] error: %s", exc)
                time.sleep(1.0)

    def shutdown(self) -> None:
        try:
            self.tts.stop()
        finally:
            self.tts.cleanup_temp_audio()

    async def run_single_client(self) -> None:
        print("Starting Bilibili live client...")
        client = blivedm.OpenLiveClient(
            access_key_id=self.runtime.access_key_id,
            access_key_secret=self.runtime.access_key_secret,
            app_id=int(self.runtime.app_id),
            room_owner_auth_code=self.runtime.room_owner_auth_code,
        )
        client.set_handler(self.handler)
        client.start()
        try:
            await client.join()
        finally:
            await client.stop_and_close()


class BiliHandler(blivedm.BaseHandler):
    def __init__(self, app: AIVtuberApp):
        self.app = app

    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage) -> None:
        log.info("[%s] 当前人气值：%s", client.room_id, message.popularity)

    def _on_open_live_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage) -> None:
        self.app.add_ui_event("gift", f"感谢{message.uname}老板送的{message.gift_name}")
        self.app.enqueue_text(GIFT_UNAME, f"感谢{message.uname}老板送的{message.gift_name}")
        print(f"[{message.room_id}] {message.uname} 赠送{message.gift_name}x{message.gift_num} 总价 {message.price}")

    def _on_open_live_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage) -> None:
        log.info("[%s] %s：%s", client.room_id, message.uname, message.msg)
        if self.app.banned_pattern and self.app.banned_pattern.search(message.msg):
            log.info("banned word detected")
            return

        user_text = message.msg.strip()
        if not user_text:
            return
        self.app.add_ui_event("user", user_text, uname=message.uname)
        self.app.enqueue_text(message.uname, user_text)

    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage) -> None:
        self._on_open_live_danmaku(client, message)
