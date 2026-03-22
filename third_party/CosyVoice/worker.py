#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import traceback
from pathlib import Path

OUT_DIR = os.environ.get("AIVTUBER_OUT_DIR", str(Path(__file__).resolve().parents[2] / "ai_audio"))
os.makedirs(OUT_DIR, exist_ok=True)
TMP_WAV = os.path.join(OUT_DIR, "test.tmp.wav")
FINAL_WAV = os.path.join(OUT_DIR, "test.wav")
DONE_PATH = os.path.join(OUT_DIR, "test.done")
DONE_TMP = os.path.join(OUT_DIR, "test.done.tmp")

COSYVOICE_DIR = Path(__file__).resolve().parent  # aivtuber/CosyVoice
MATCHA_DIR = COSYVOICE_DIR / "third_party" / "Matcha-TTS"
sys.path.insert(0, str(MATCHA_DIR))  # 用 insert(0) 优先级更高
MODEL_DIR = (COSYVOICE_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B").resolve()
PROMPT_WAV = (COSYVOICE_DIR / "asset" / "lulu_tk_prompt.wav").resolve()
PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>哟哦，小贱狗，在这儿干嘛呢？想妈妈了吗？想不想让妈妈挠一挠你的大臭脚？"


def log_err(msg: str):
    print(msg, file=sys.stderr, flush=True)


def atomic_write_text(path: str, tmp: str, text: str):
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    last_error = None
    for _ in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.02)
    if last_error is not None:
        raise last_error
    os.replace(tmp, path)


def save_wav_atomically(torchaudio, wav, sr: int):
    torchaudio.save(TMP_WAV, wav, sr)
    os.replace(TMP_WAV, FINAL_WAV)


def main():
    import soundfile as sf
    from cosyvoice.cli.cosyvoice import AutoModel

    log_err(f"[BOOT] loading model: {MODEL_DIR}")
    log_err(f"[BOOT] prompt_wav = {PROMPT_WAV}")
    cosyvoice = AutoModel(model_dir=str(MODEL_DIR))
    log_err("[BOOT] model loaded, ready.")

    counter = 0

    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue

        counter += 1
        token = str(counter)
        t0 = time.time()

        try:
            log_err(f"[REQ] #{token} len={len(text)}")

            gen = cosyvoice.inference_zero_shot(
                text,
                PROMPT_TEXT,
                PROMPT_WAV,
                stream=False,
            )
            result = next(gen)

            wav = result.get("tts_speech")
            sr = int(result.get("sample_rate", 24000))
            if wav is None:
                raise RuntimeError("tts_speech missing in result")

            wav_np = wav.detach().cpu().numpy()
            if wav_np.ndim == 2:
                wav_np = wav_np.T
            tmp_wav = os.path.join(OUT_DIR, f"{token}.tmp.wav")
            out_wav = os.path.join(OUT_DIR, f"{token}.wav")
            sf.write(tmp_wav, wav_np, sr)
            os.replace(tmp_wav, out_wav)
            atomic_write_text(DONE_PATH, DONE_TMP, token)

            log_err(f"[OK] #{token} saved test.wav ({time.time()-t0:.2f}s)")

        except Exception as e:
            log_err(f"[ERR] #{token} failed: {e}")
            log_err(traceback.format_exc())

    log_err("[EXIT] stdin closed, worker exit")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_err("[FATAL] worker crashed")
        log_err(traceback.format_exc())
        raise
