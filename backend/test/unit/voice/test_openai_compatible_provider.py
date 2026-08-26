from __future__ import annotations

import httpx
import pytest

from yuxi.voice.providers import openai_compatible
from yuxi.voice.providers.openai_compatible import OpenAICompatibleVoiceProvider, VoiceProviderRequestError

pytestmark = pytest.mark.unit


def build_provider() -> OpenAICompatibleVoiceProvider:
    """创建仅用于 HTTP 协议测试的供应商实例。"""

    return OpenAICompatibleVoiceProvider(
        name="compatible",
        asr_base_url="https://voice.example/v1",
        asr_api_key="asr-key",
        asr_model="asr-model",
        asr_endpoint="audio/transcriptions",
        asr_timeout_seconds=1,
        tts_base_url="https://voice.example/v1",
        tts_api_key="tts-key",
        tts_model="tts-model",
        tts_endpoint="audio/speech",
        tts_timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_provider_maps_transcription_protocol(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://voice.example/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer asr-key"
        assert b"asr-model" in await request.aread()
        return httpx.Response(200, json={"text": "识别文字", "language": "zh"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(openai_compatible, "_http_client", client)

    result = await build_provider().transcribe(
        audio=b"audio",
        filename="recording.wav",
        content_type="audio/wav",
        language="zh",
    )

    assert result.transcript == "识别文字"
    assert result.language == "zh"
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_maps_rate_limit_without_exposing_body(monkeypatch):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429, json={"secret": "do-not-return"}))
    )
    monkeypatch.setattr(openai_compatible, "_http_client", client)

    with pytest.raises(VoiceProviderRequestError) as error:
        await build_provider().synthesize(text="回答", voice="default", speed=1.0, audio_format="mp3")

    assert error.value.code == "rate_limited"
    assert "do-not-return" not in str(error.value)
    await client.aclose()
