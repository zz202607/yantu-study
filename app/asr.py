"""Local speech-to-text for the "hold to talk" button.

Audio comes from the phone as 16 kHz mono 16-bit WAV, is transcribed on this
PC with SenseVoice (sherpa-onnx), and is never written to disk or uploaded.
The model lives under `.cache/asr/sensevoice/` (gitignored); the rest of the
app works without it, only this button reports "not ready".

`YANTU_ASR_MODEL` / `YANTU_ASR_TOKENS` point at another model pair.
"""

from __future__ import annotations

import io
import os
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / ".cache" / "asr" / "sensevoice"
MODEL_PATH = Path(os.environ.get("YANTU_ASR_MODEL") or MODEL_DIR / "model.int8.onnx")
TOKENS_PATH = Path(os.environ.get("YANTU_ASR_TOKENS") or MODEL_DIR / "tokens.txt")

SAMPLE_RATE = 16000
MAX_SECONDS = 45
MAX_BYTES = 8 * 1024 * 1024
MIN_SECONDS = 0.25

# SenseVoice is trained on general Mandarin; these are the confusions the
# geology vocabulary produces most often.  Keep the list short and exact —
# over-correcting would be worse than the user editing one word.
CORRECTIONS = (
    ("父母阻力", "负摩阻力"),
    ("父魔阻力", "负摩阻力"),
    ("覆膜阻力", "负摩阻力"),
    ("复摩阻力", "负摩阻力"),
    ("负模阻力", "负摩阻力"),
    ("负魔阻力", "负摩阻力"),
    ("覆膜阻", "负摩阻"),
    ("负模阻", "负摩阻"),
)

_TAG_RE = re.compile(r"<\|[^|]*\|>")
_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_DECODE_LOCK = threading.Lock()


class ASRError(RuntimeError):
    """Bad audio or an empty transcript; the user can retry."""


class ASRUnavailable(ASRError):
    """The local model or sherpa-onnx is missing."""


def normalize_text(text):
    """Strip SenseVoice control tags and repair the common geology misses."""
    value = _TAG_RE.sub("", str(text or ""))
    value = " ".join(value.split())
    for wrong, right in CORRECTIONS:
        value = value.replace(wrong, right)
    value = re.sub(r"[？?]{2,}", "？", value)
    value = re.sub(r"[！!]{2,}", "！", value)
    value = re.sub(r"[。.]{2,}", "。", value)
    return value.strip(" ，,。.")


def parse_wav(data):
    """Validate one WAV blob and return (float32 mono samples, seconds).

    The browser sends 16 kHz mono, but tolerate stereo and other rates so an
    older phone or a desktop test clip still works.  Linear resampling is
    enough for speech at these lengths.
    """
    if not data:
        raise ASRError("没有收到录音")
    if len(data) > MAX_BYTES:
        raise ASRError("录音太长（上限 %d MB）" % (MAX_BYTES // 1024 // 1024))
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ASRError("录音格式不是 WAV")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.getnframes()
            raw = wav.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ASRError("WAV 读不了：%s" % exc)
    if channels not in (1, 2):
        raise ASRError("只支持单声道或双声道录音")
    if width != 2:
        raise ASRError("只支持 16 位 PCM 录音")
    if not 8000 <= rate <= 48000:
        raise ASRError("采样率不支持：%d" % rate)
    seconds = frames / float(rate or 1)
    if seconds < MIN_SECONDS:
        raise ASRError("录音太短，按住多说一句")
    if seconds > MAX_SECONDS:
        raise ASRError("录音超过 %d 秒，分段说" % MAX_SECONDS)

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)
    if rate != SAMPLE_RATE and samples.size:
        target = max(1, int(round(seconds * SAMPLE_RATE)))
        source = np.arange(samples.size, dtype=np.float64)
        wanted = np.linspace(0, samples.size - 1, target)
        samples = np.interp(wanted, source, samples).astype(np.float32)
    return samples, seconds


def model_files():
    return MODEL_PATH, TOKENS_PATH


def status():
    """Cheap readiness check for the page; never loads the 230 MB model."""
    model, tokens = model_files()
    if not model.exists() or not tokens.exists():
        return {
            "ready": False,
            "engine": "sensevoice",
            "detail": "本机还没有语音模型；先运行 tools/fetch_asr_model.py",
        }
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return {
            "ready": False,
            "engine": "sensevoice",
            "detail": "缺少 sherpa-onnx；pip install -r requirements.txt",
        }
    return {"ready": True, "engine": "sensevoice", "detail": "本机识别"}


def _recognizer():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        ready = status()
        if not ready["ready"]:
            raise ASRUnavailable(ready["detail"])
        import sherpa_onnx

        model, tokens = model_files()
        _ENGINE = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=4,
            use_itn=True,
            language="zh",
            debug=False,
        )
        return _ENGINE


def transcribe(data, recognizer=None):
    """Turn one WAV blob into text.  No audio leaves this function."""
    samples, seconds = parse_wav(data)
    engine = recognizer if recognizer is not None else _recognizer()
    started = time.monotonic()
    with _DECODE_LOCK:
        stream = engine.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        engine.decode_stream(stream)
    text = normalize_text(stream.result.text)
    if not text:
        raise ASRError("没听清；再说一遍，或用键盘麦克风")
    return {
        "text": text,
        "duration": round(seconds, 2),
        "seconds": round(time.monotonic() - started, 2),
        "engine": "sensevoice",
    }
