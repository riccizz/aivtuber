import os
import shutil
import sys
import time
import wave
import threading
import shlex
import subprocess
from collections import deque
from pathlib import Path
from typing import Optional


class CosyVoiceTTS:
    """
    驱动 CosyVoice 常驻 worker（方案 A）：

    worker.py:
      - stdin: 每行一个 text
      - 输出固定 wav: <out_dir>/<wav_name>  (OBS 读)
      - 完成信号:     <out_dir>/<done_name> (主工程检测内容变化)

    本类：
      - 不读 stdout（避免第三方打印污染）
      - 成功判定：done 内容变化 + wav 存在且 size > 44
      - 失败时读取 stderr tail 方便定位
    """

    def __init__(
        self,
        worker_py: str,
        python_bin: str | None = None,
        backend: str = "cosy", # cosy or edge
        playback_backend: str = "obs",  # obs or local
        edge_voice: str = "zh-CN-XiaoxiaoNeural",
        ffmpeg_bin: str = "ffmpeg",
        out_dir: str = "ai_audio",
        wav_name: str = "test.wav",
        done_name: str = "test.done",
        tts_timeout_s: float = 20.0,
        stderr_tail_lines: int = 200,
    ):
        self.worker_py = worker_py
        self.python_bin = python_bin or sys.executable
        self.backend = backend
        self.playback_backend = playback_backend
        self.out_dir = out_dir
        self.wav_path = os.path.join(out_dir, wav_name)
        self.done_path = os.path.join(out_dir, done_name)
        self.tmp_wav_path = os.path.join(out_dir, "test.tmp.wav")
        self.done_tmp_path = os.path.join(out_dir, "test.done.tmp")
        self.edge_voice = edge_voice
        self.ffmpeg_bin = self._resolve_ffmpeg_bin(ffmpeg_bin)

        self.tts_timeout_s = float(tts_timeout_s)
        self._stderr_tail = deque(maxlen=int(stderr_tail_lines))

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

        self._token_counter = 0

        os.makedirs(self.out_dir, exist_ok=True)

    @staticmethod
    def _resolve_ffmpeg_bin(ffmpeg_bin: str) -> str:
        if ffmpeg_bin and shutil.which(ffmpeg_bin):
            return ffmpeg_bin
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return ffmpeg_bin

    # ---------------- public ----------------

    def ensure_started(self) -> None:
        """确保 worker 正在运行。"""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start_locked()

    def restart(self) -> None:
        """强制重启 worker。"""
        with self._lock:
            self._stop_locked()
            self._start_locked()

    def stop(self) -> None:
        """停止 worker。"""
        with self._lock:
            self._stop_locked()

    def update_runtime(
        self,
        *,
        backend: Optional[str] = None,
        playback_backend: Optional[str] = None,
        edge_voice: Optional[str] = None,
        out_dir: Optional[str] = None,
    ) -> None:
        restart_worker = False
        old_backend = self.backend
        out_dir_changed = False
        if backend and backend != self.backend:
            self.backend = backend
        if playback_backend:
            self.playback_backend = playback_backend
        if edge_voice:
            self.edge_voice = edge_voice
        if out_dir and out_dir != self.out_dir:
            restart_worker = True
            out_dir_changed = True
            self.out_dir = out_dir
            self.wav_path = os.path.join(out_dir, os.path.basename(self.wav_path))
            self.done_path = os.path.join(out_dir, os.path.basename(self.done_path))
            self.tmp_wav_path = os.path.join(out_dir, os.path.basename(self.tmp_wav_path))
            self.done_tmp_path = os.path.join(out_dir, os.path.basename(self.done_tmp_path))
            os.makedirs(self.out_dir, exist_ok=True)
        if old_backend == "cosy" and self.backend == "edge" and not out_dir_changed:
            restart_worker = False
        if restart_worker:
            self.stop()

    def cleanup_temp_audio(self) -> None:
        """清理输出目录下的临时音频和完成标记。"""
        out_dir = Path(self.out_dir)
        if not out_dir.exists():
            return

        keep_names = {
            Path(self.wav_path).name,
            Path(self.done_path).name,
            Path(self.tmp_wav_path).name,
            Path(self.done_tmp_path).name,
        }
        for path in out_dir.iterdir():
            if not path.is_file():
                continue
            if path.name in keep_names:
                continue
            if path.suffix not in {".wav", ".done", ".tmp", ".mp3"}:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    def speak(
        self,
        text: str,
        ws=None,
        media_source_name: Optional[str] = None,
        restart_action: str = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
        extra_delay_s: float = 0.15,
        timeout_s: Optional[float] = None,
        retry_once: bool = False,
        sleep_until_done: bool = True,
    ) -> bool:
        tmp_wav_path = self.generate_wav(
            text,
            timeout_s=timeout_s,
            retry_once=retry_once,
        )
        if not tmp_wav_path:
            return False
        return self.play_wav(
            tmp_wav_path,
            ws=ws,
            media_source_name=media_source_name,
            restart_action=restart_action,
            extra_delay_s=extra_delay_s,
            sleep_until_done=sleep_until_done,
        )


    def generate_wav(
        self,
        text: str,
        timeout_s: Optional[float] = None,
        retry_once: bool = False,
    ) -> Optional[str]:
        """直接生成 wav 文件（不触发 OBS）。返回 wav 路径或 None。"""
        text = (text or "").replace("\n", " ").strip()
        if not text:
            return None
        
        self._token_counter += 1
        token = str(self._token_counter)
        if self.backend == "edge":
            try:
                out_wav = self._edge_generate_wav(text, token)
                return out_wav
            except Exception as e:
                print("[EDGE] error:", e)
                return None
        else:
            self.ensure_started()
            prev = self._read_done()
            self._send_text(text)
            token = self._wait_done_change(prev, timeout_s=timeout_s or self.tts_timeout_s)
            if token is None:
                tail = self.stderr_tail(120)
                if tail: 
                    print("\n--- cosyvoice worker stderr tail ---\n" + tail)
                if retry_once:
                    try:
                        self.restart()
                    except Exception as e:
                        print("worker restart error:", e)
                        return None
                    return self.generate_wav(
                        text,
                        timeout_s=timeout_s,
                        retry_once=False,
                    )
                return None
            return os.path.join(self.out_dir, f"{token}.wav")
    
    def play_wav(
        self,
        tmp_wav_path: str,
        ws=None,
        media_source_name: Optional[str] = None,
        restart_action: str = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
        extra_delay_s: float = 0.15,
        sleep_until_done: bool = True,
    ) -> bool:
        """播放当前 wav 文件，可走 OBS 媒体源或本地音频设备。"""
        if not tmp_wav_path:
            return False
        if not os.path.exists(tmp_wav_path):
            print("tmp wav missing:", tmp_wav_path)
            return False
        self._replace_with_retry(tmp_wav_path, self.wav_path)
        if self.playback_backend == "local":
            return self._play_wav_local(self.wav_path, extra_delay_s=extra_delay_s, sleep_until_done=sleep_until_done)
        if self.playback_backend == "obs":
            if ws is None or not media_source_name:
                print("OBS playback unavailable: client not connected or media source name missing")
                return False
            try:
                ws.trigger_media_input_action(media_source_name, restart_action)
            except Exception as e:
                print("OBS trigger error:", e)
                return False
        if not sleep_until_done:
            return True
        try:
            dur = self.wav_duration_seconds(self.wav_path) + float(extra_delay_s)
        except Exception as e:
            print("wav duration error:", e)
            dur = 2.0

        time.sleep(dur)
        return True

    def stderr_tail(self, last_n: int = 120) -> str:
        """取 stderr 尾巴（最近 N 行）"""
        tail = list(self._stderr_tail)[-max(1, int(last_n)) :]
        return "\n".join(tail)

    # ---------------- internals ----------------

    def _start_locked(self) -> None:
        self._stderr_tail.clear()

        # stdout 丢弃；stderr 采集
        self._proc = subprocess.Popen(
            [self.python_bin, "-u", self.worker_py],
            env={**os.environ, "AIVTUBER_OUT_DIR": self.out_dir},
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def _stderr_reader(p: subprocess.Popen):
            assert p.stderr is not None
            for line in p.stderr:
                self._stderr_tail.append(line.rstrip("\n"))

        threading.Thread(target=_stderr_reader, args=(self._proc,), daemon=True).start()

        # 不等 ready 信号（我们不依赖它）；只做一个“秒退检测”
        time.sleep(0.1)
        if self._proc.poll() is not None:
            raise RuntimeError(
                f"CosyVoice worker exited early, code={self._proc.poll()}\n"
                f"stderr tail:\n{self.stderr_tail(120)}"
            )

    def _stop_locked(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass

        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()

        self._proc = None

    def _send_text(self, text: str) -> None:
        self.ensure_started()
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(text.replace("\n", " ") + "\n")
        self._proc.stdin.flush()

    def _read_done(self) -> Optional[str]:
        try:
            with open(self.done_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return None

    def _wait_done_change(self, prev: Optional[str], timeout_s: float) -> Optional[str]:
        self.ensure_started()
        assert self._proc is not None
        deadline = time.time() + float(timeout_s)

        while time.time() < deadline:
            # worker 死了 => 失败
            if self._proc.poll() is not None:
                return None

            cur = self._read_done()
            if cur and cur != prev:
                # wav 轻量校验：存在 + 非空头（原子 replace 下足够稳）
                try:
                    if os.path.exists(self.wav_path) and os.path.getsize(self.wav_path) > 44:
                        return cur
                except Exception:
                    pass

            time.sleep(0.01)

        return None
    
    def _edge_generate_wav(self, text: str, token: str):
        """
        用 edge-tts 生成 wav，并写入 self.wav_path / self.done_path
        输出仍然是同一个固定 wav 文件给 OBS 读。
        """
        os.makedirs(self.out_dir, exist_ok=True)

        tmp_audio = os.path.join(self.out_dir, f"{token}.edge.tmp.mp3")  # 中间文件
        out_wav = os.path.join(self.out_dir, f"{token}.wav")  # 最终输出文件
        cmd_edge = [
            self.python_bin,
            "-m",
            "edge_tts",
            "--rate=+5%",
            "--voice",
            self.edge_voice,
            "--text",
            text,
            "--write-media",
            tmp_audio,
        ]
        edge_result = subprocess.run(
            cmd_edge,
            check=False,
            capture_output=True,
            text=True,
        )
        if edge_result.returncode != 0 or not os.path.exists(tmp_audio):
            raise RuntimeError(
                "edge-tts generation failed: "
                f"returncode={edge_result.returncode}, "
                f"stderr={edge_result.stderr.strip() or '<empty>'}"
            )

        # 转 wav：24k mono s16，和 CosyVoice 统一
        cmd_ff = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            tmp_audio,
            "-ac",
            "1",
            "-ar",
            "24000",
            "-sample_fmt",
            "s16",
            out_wav,
        ]
        ff_result = subprocess.run(
            cmd_ff,
            check=False,
            capture_output=True,
            text=True,
        )
        if ff_result.returncode != 0 or not os.path.exists(out_wav):
            raise RuntimeError(
                "ffmpeg conversion failed: "
                f"returncode={ff_result.returncode}, "
                f"stderr={ff_result.stderr.strip() or '<empty>'}"
            )

        # # 用重试 replace，避免 OBS 锁住 test.wav 时失败
        # self._replace_with_retry(out_wav, self.wav_path)

        # # 更新 done（edge 也统一写 done，让主工程检测逻辑一致）
        # self._atomic_write_text(self.done_path, self.done_tmp_path, token)

        try:
            os.remove(tmp_audio)
        except Exception:
            pass
        return out_wav

    def _atomic_write_text(self, path: str, tmp: str, text: str):
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _replace_with_retry(self, src: str, dst: str, retries: int = 30, delay: float = 0.1):
        last = None
        for _ in range(retries):
            try:
                os.replace(src, dst)
                return
            except PermissionError as e:
                last = e
                time.sleep(delay)
        raise last

    def _play_wav_local(self, path: str, *, extra_delay_s: float, sleep_until_done: bool) -> bool:
        try:
            if sys.platform.startswith("win"):
                import winsound

                flags = winsound.SND_FILENAME
                if sleep_until_done:
                    winsound.PlaySound(path, flags)
                else:
                    winsound.PlaySound(path, flags | winsound.SND_ASYNC)
                if sleep_until_done and extra_delay_s > 0:
                    time.sleep(float(extra_delay_s))
                return True

            cmd = None
            if shutil.which("ffplay"):
                cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
            elif shutil.which("aplay"):
                cmd = ["aplay", path]

            if cmd is None:
                print("local playback backend unavailable: need winsound, ffplay, or aplay")
                return False

            if sleep_until_done:
                result = subprocess.run(cmd, check=False)
                if result.returncode != 0:
                    return False
                if extra_delay_s > 0:
                    time.sleep(float(extra_delay_s))
            else:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            print("local playback error:", e)
            return False
    
    @staticmethod
    def wav_duration_seconds(path: str) -> float:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
