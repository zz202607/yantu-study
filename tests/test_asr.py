"""Local push-to-talk: WAV validation, text cleanup, SenseVoice smoke test."""

import io
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from app import asr


def make_wav(seconds=1.0, rate=16000, channels=1, width=2):
    frames = int(rate * seconds)
    raw = np.zeros(frames * channels, dtype=np.int16)
    if width == 2:
        raw = (np.sin(np.linspace(0, 40, frames * channels)) * 3000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        if width == 2:
            wav.writeframes(raw.tobytes())
        else:
            wav.writeframes(bytes(len(raw)))
    return buf.getvalue()


def test_parse_wav_returns_16k_float_samples():
    samples, seconds = asr.parse_wav(make_wav(1.0))
    assert seconds == pytest.approx(1.0, abs=0.01)
    assert samples.dtype == np.float32
    assert len(samples) == 16000
    assert np.abs(samples).max() <= 1.0


def test_parse_wav_resamples_stereo_and_other_rates():
    samples, seconds = asr.parse_wav(make_wav(0.5, rate=8000, channels=2))
    assert seconds == pytest.approx(0.5, abs=0.01)
    assert len(samples) == pytest.approx(8000, abs=2)


def test_parse_wav_rejects_bad_audio():
    with pytest.raises(asr.ASRError):
        asr.parse_wav(b"")
    with pytest.raises(asr.ASRError):
        asr.parse_wav(b"not a wav")
    with pytest.raises(asr.ASRError):
        asr.parse_wav(make_wav(0.05))  # too short
    with pytest.raises(asr.ASRError):
        asr.parse_wav(make_wav(46))  # too long
    with pytest.raises(asr.ASRError):
        asr.parse_wav(make_wav(1.0, width=1))  # 8-bit


def test_normalize_strips_tags_and_repairs_geology_terms():
    assert asr.normalize_text("<|zh|><|NEUTRAL|>父母阻力取这个值") == "负摩阻力取这个值"
    assert asr.normalize_text("覆膜阻力怎么办？") == "负摩阻力怎么办？"
    assert asr.normalize_text("父魔阻力取这个值？？") == "负摩阻力取这个值？"
    assert asr.normalize_text("  这道题 第三步 为什么 ") == "这道题 第三步 为什么"


def test_transcribe_uses_the_recognizer_and_discards_audio():
    class Stream:
        result = types.SimpleNamespace(text="父母阻力取多少")

        def accept_waveform(self, rate, samples):
            self.rate = rate
            self.samples = samples

    class FakeRecognizer:
        def create_stream(self):
            return Stream()

        def decode_stream(self, stream):
            pass

    out = asr.transcribe(make_wav(0.5), recognizer=FakeRecognizer())
    assert out["text"] == "负摩阻力取多少"
    assert out["engine"] == "sensevoice"
    assert 0.4 <= out["duration"] <= 0.6


def test_status_reports_missing_model(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "MODEL_PATH", tmp_path / "missing.onnx")
    monkeypatch.setattr(asr, "TOKENS_PATH", tmp_path / "missing.txt")
    status = asr.status()
    assert status["ready"] is False
    assert "fetch_asr_model" in status["detail"]


READY = asr.model_files()[0].exists() and asr.model_files()[1].exists()


@pytest.mark.skipif(not READY, reason="local ASR model is not downloaded")
def test_sensevoice_reads_the_bundled_chinese_sample():
    sample = Path(asr.MODEL_DIR) / "test_wavs" / "zh.wav"
    if not sample.exists():
        pytest.skip("sample wav is not downloaded")
    out = asr.transcribe(sample.read_bytes())
    assert out["text"]
    assert "9" in out["text"] or "九" in out["text"]
