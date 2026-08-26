from __future__ import annotations

import io
import asyncio
import wave
from dataclasses import replace

import pytest

from yuxi.models.providers.cache import ModelInfo
from yuxi.voice.providers.base import SpeechResult, TranscriptionResult, VoiceProvider
from yuxi.voice.service import (
    VoiceService,
    VoiceServiceError,
    VoiceSettings,
    _prepare_qwen_audio,
    _speech_cache_key,
)

pytestmark = pytest.mark.unit


class FakeRedis:
    """提供语音服务单元测试需要的最小 Redis 行为。"""

    def __init__(self):
        self.values: dict[str, str | int] = {}
        self.hashes: dict[str, dict[str, int]] = {}

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, _: int) -> None:
        return None

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        fields = self.hashes.setdefault(key, {})
        fields[field] = fields.get(field, 0) + amount
        return fields[field]


class FakeProvider(VoiceProvider):
    """记录调用参数并返回固定语音结果。"""

    def __init__(self, transcript: str = "识别结果"):
        self.transcript = transcript
        self.transcribe_calls = []
        self.synthesize_calls = []

    async def transcribe(self, *, audio, filename, content_type, language):
        self.transcribe_calls.append((audio, filename, content_type, language))
        return TranscriptionResult(
            transcript=self.transcript,
            language=language or "zh-CN",
            provider="fake-provider",
        )

    async def synthesize(self, *, text, voice, speed, audio_format):
        self.synthesize_calls.append((text, voice, speed, audio_format))
        return SpeechResult(content=b"audio", media_type="audio/mpeg", provider="fake-provider")


def build_settings(**overrides) -> VoiceSettings:
    """创建默认启用且限制较小的测试配置。"""

    settings = VoiceSettings(
        asr_enabled=True,
        tts_enabled=True,
        provider_name="fake-provider",
        asr_base_url="https://voice.invalid/v1",
        asr_api_key="asr-secret",
        asr_model="asr-model",
        asr_protocol="qwen_asr",
        asr_headers={},
        asr_timeout_seconds=1,
        tts_base_url="https://voice.invalid/v1",
        tts_api_key="tts-secret",
        tts_model="tts-model",
        tts_protocol="qwen_tts",
        tts_headers={},
        tts_timeout_seconds=1,
        tts_language_type="Chinese",
        max_audio_bytes=1024 * 1024,
        max_audio_seconds=2,
        max_tts_characters=20,
        rate_limit_per_minute=10,
        cache_ttl_seconds=60,
        voices=("default", "female"),
        default_voice="default",
        default_speed=1.0,
        speed_min=0.5,
        speed_max=2.0,
        formats=("mp3", "wav"),
        default_format="mp3",
    )
    return replace(settings, **overrides)


def wav_bytes(duration_seconds: float = 0.1) -> bytes:
    """生成无需外部音频依赖即可解析的测试 WAV。"""

    buffer = io.BytesIO()
    frame_rate = 8000
    with wave.open(buffer, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(frame_rate)
        audio_file.writeframes(b"\x00\x00" * round(frame_rate * duration_seconds))
    return buffer.getvalue()


def test_capabilities_report_disabled_without_raising():
    service = VoiceService(
        build_settings(asr_enabled=False, tts_enabled=True, tts_base_url="", tts_model=""),
        FakeProvider(),
    )

    capabilities = service.capabilities()

    assert capabilities.asr.enabled is False
    assert capabilities.asr.reason == "disabled"
    assert capabilities.tts.enabled is False
    assert capabilities.tts.reason == "missing_configuration"


def test_settings_reuse_model_provider_credentials(monkeypatch):
    models = {
        "asr": [
            ModelInfo(
                provider_id="alibaba-cn",
                model_id="qwen3-asr-flash",
                model_type="asr",
                display_name="Qwen ASR",
                api_key="shared-key",
                base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                provider_type="openai",
                protocol_override="qwen_asr",
            )
        ],
        "tts": [
            ModelInfo(
                provider_id="alibaba-cn",
                model_id="qwen3-tts-flash",
                model_type="tts",
                display_name="Qwen TTS",
                api_key="shared-key",
                base_url="https://dashscope.aliyuncs.com/api/v1",
                provider_type="openai",
                protocol_override="qwen_tts",
                model_extra={
                    "voices": ["Cherry", "Serena"],
                    "default_voice": "Serena",
                    "language_type": "Chinese",
                },
            )
        ],
    }
    monkeypatch.setattr(
        "yuxi.voice.service.model_cache.get_all_specs",
        lambda model_type: models[model_type],
    )

    settings = VoiceSettings.from_env()

    assert settings.provider_name == "alibaba-cn"
    assert settings.asr_api_key == settings.tts_api_key == "shared-key"
    assert settings.asr_configured is True
    assert settings.tts_configured is True
    assert settings.voices == ("Cherry", "Serena")
    assert settings.default_voice == "Serena"
    assert settings.default_format == "wav"
    assert settings.speed_min == settings.speed_max == 1.0


@pytest.mark.asyncio
async def test_transcription_returns_draft_without_persistence(monkeypatch):
    redis = FakeRedis()
    provider = FakeProvider()
    monkeypatch.setattr("yuxi.voice.service.get_async_redis_client", lambda: _async_value(redis))
    service = VoiceService(build_settings(), provider)

    response = await service.transcribe(
        audio=wav_bytes(),
        filename="recording.wav",
        content_type="audio/wav",
        language="zh-CN",
        user_id="7",
    )

    assert response.transcript == "识别结果"
    assert response.duration_ms == 100
    assert response.provider == "fake-provider"
    assert provider.transcribe_calls[0][1:] == ("recording.wav", "audio/wav", "zh-CN")


@pytest.mark.asyncio
async def test_transcription_rejects_audio_over_duration(monkeypatch):
    monkeypatch.setattr("yuxi.voice.service.get_async_redis_client", lambda: _async_value(FakeRedis()))
    service = VoiceService(build_settings(max_audio_seconds=1), FakeProvider())

    with pytest.raises(VoiceServiceError) as error:
        await service.transcribe(
            audio=wav_bytes(1.2),
            filename="recording.wav",
            content_type="audio/wav",
            language=None,
            user_id="7",
        )

    assert error.value.code == "audio_too_long"


@pytest.mark.asyncio
async def test_transcription_rejects_empty_provider_result(monkeypatch):
    monkeypatch.setattr("yuxi.voice.service.get_async_redis_client", lambda: _async_value(FakeRedis()))
    service = VoiceService(build_settings(), FakeProvider(transcript="  "))

    with pytest.raises(VoiceServiceError) as error:
        await service.transcribe(
            audio=wav_bytes(),
            filename="recording.wav",
            content_type="audio/wav",
            language=None,
            user_id="7",
        )

    assert error.value.code == "empty_transcript"


@pytest.mark.asyncio
async def test_speech_uses_short_term_cache(monkeypatch):
    redis = FakeRedis()
    provider = FakeProvider()
    monkeypatch.setattr("yuxi.voice.service.get_async_redis_client", lambda: _async_value(redis))
    service = VoiceService(build_settings(), provider)

    first, _, first_cache_hit = await service.synthesize(
        text="已有助手回答",
        voice="default",
        speed=1.0,
        audio_format="mp3",
        user_id="7",
    )
    second, _, second_cache_hit = await service.synthesize(
        text="已有助手回答",
        voice="default",
        speed=1.0,
        audio_format="mp3",
        user_id="7",
    )

    assert first.content == second.content == b"audio"
    assert first_cache_hit is False
    assert second_cache_hit is True
    assert len(provider.synthesize_calls) == 1

    metric_keys = [key for key in redis.hashes if key.startswith("voice:metrics:")]
    assert metric_keys
    assert any(redis.hashes[key].get("cache_hit_count") == 1 for key in metric_keys)


@pytest.mark.asyncio
async def test_client_metric_event_only_writes_fixed_aggregate(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("yuxi.voice.service.get_async_redis_client", lambda: _async_value(redis))
    service = VoiceService(build_settings(), FakeProvider())

    await service.record_client_event("speech_selection_used")

    metric_key = next(key for key in redis.hashes if key.startswith("voice:metrics:") and key.endswith(":client"))
    assert redis.hashes[metric_key]["requests"] == 1
    assert redis.hashes[metric_key]["outcome_speech_selection_used"] == 1


@pytest.mark.asyncio
async def test_speech_rejects_long_text_before_provider(monkeypatch):
    monkeypatch.setattr("yuxi.voice.service.get_async_redis_client", lambda: _async_value(FakeRedis()))
    provider = FakeProvider()
    service = VoiceService(build_settings(max_tts_characters=4), provider)

    with pytest.raises(VoiceServiceError) as error:
        await service.synthesize(
            text="这段文字超过限制",
            voice="default",
            speed=1.0,
            audio_format="mp3",
            user_id="7",
        )

    assert error.value.code == "speech_text_too_long"
    assert provider.synthesize_calls == []


def test_speech_cache_key_does_not_contain_credentials():
    key = _speech_cache_key("provider", "model", "已有回答", "default", 1.0, "mp3")

    assert key.startswith("voice:speech:")
    assert "已有回答" not in key
    assert "secret" not in key


@pytest.mark.asyncio
async def test_qwen_audio_conversion_outputs_wav(tmp_path):
    source_path = tmp_path / "recording.webm"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:duration=0.1",
        str(source_path),
    )
    await process.communicate()
    assert process.returncode == 0

    converted, filename, content_type = await _prepare_qwen_audio(
        source_path.read_bytes(),
        source_path.name,
        "audio/webm",
        "request-1",
    )

    assert converted.startswith(b"RIFF")
    assert filename == "recording.wav"
    assert content_type == "audio/wav"


async def _async_value(value):
    return value
