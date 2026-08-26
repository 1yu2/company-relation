from __future__ import annotations

import base64
import json

import httpx
import pytest

from yuxi.voice.providers import qwen
from yuxi.voice.providers.openai_compatible import VoiceProviderRequestError
from yuxi.voice.providers.qwen import QwenVoiceProvider

pytestmark = pytest.mark.unit


def build_provider() -> QwenVoiceProvider:
    """创建只用于验证百炼 HTTP 协议映射的 Qwen 供应商。"""

    return QwenVoiceProvider(
        name="alibaba-cn",
        asr_base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        asr_api_key="shared-key",
        asr_model="qwen3-asr-flash",
        asr_timeout_seconds=1,
        tts_base_url="https://dashscope.aliyuncs.com/api/v1",
        tts_api_key="shared-key",
        tts_model="qwen3-tts-flash",
        tts_timeout_seconds=1,
        tts_language_type="Chinese",
    )


@pytest.mark.asyncio
async def test_qwen_asr_sends_data_url_to_chat_completions(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == ("https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions")
        assert request.headers["authorization"] == "Bearer shared-key"
        payload = json.loads(await request.aread())
        audio_data = payload["messages"][0]["content"][0]["input_audio"]["data"]
        assert audio_data == f"data:audio/wav;base64,{base64.b64encode(b'audio').decode()}"
        assert payload["asr_options"] == {"enable_itn": False, "language": "zh"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "识别文字",
                            "annotations": [{"type": "audio_info", "language": "zh"}],
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(qwen, "_http_client", client)

    result = await build_provider().transcribe(
        audio=b"audio",
        filename="recording.wav",
        content_type="audio/wav",
        language="zh-CN",
    )

    assert result.transcript == "识别文字"
    assert result.language == "zh"
    await client.aclose()


@pytest.mark.asyncio
async def test_qwen_tts_downloads_temporary_aliyun_audio(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url == (
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
            )
            payload = json.loads(await request.aread())
            assert payload == {
                "model": "qwen3-tts-flash",
                "input": {"text": "已有回答", "voice": "Cherry", "language_type": "Chinese"},
            }
            return httpx.Response(
                200,
                json={"output": {"audio": {"url": "https://dashscope-result.oss-cn-beijing.aliyuncs.com/result.wav"}}},
            )

        assert request.headers.get("authorization") is None
        return httpx.Response(200, content=b"wave", headers={"Content-Type": "audio/wav"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(qwen, "_http_client", client)

    result = await build_provider().synthesize(
        text="已有回答",
        voice="Cherry",
        speed=1.0,
        audio_format="wav",
    )

    assert result.content == b"wave"
    assert result.media_type == "audio/wav"
    await client.aclose()


@pytest.mark.asyncio
async def test_qwen_tts_upgrades_aliyun_http_audio_url(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"audio": {"url": "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/result.wav"}}
                },
            )

        assert request.url == "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/result.wav"
        return httpx.Response(200, content=b"wave", headers={"Content-Type": "audio/wav"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(qwen, "_http_client", client)

    result = await build_provider().synthesize(
        text="已有回答",
        voice="Cherry",
        speed=1.0,
        audio_format="wav",
    )

    assert result.content == b"wave"
    await client.aclose()


@pytest.mark.asyncio
async def test_qwen_tts_accepts_inline_base64_audio(monkeypatch):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"output": {"audio": {"data": base64.b64encode(b"wave").decode()}}},
            )
        )
    )
    monkeypatch.setattr(qwen, "_http_client", client)

    result = await build_provider().synthesize(
        text="已有回答",
        voice="Cherry",
        speed=1.0,
        audio_format="wav",
    )

    assert result.content == b"wave"
    assert result.media_type == "audio/wav"
    await client.aclose()


@pytest.mark.asyncio
async def test_qwen_tts_rejects_non_aliyun_audio_url(monkeypatch):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"output": {"audio": {"url": "https://internal.example/audio.wav"}}},
            )
        )
    )
    monkeypatch.setattr(qwen, "_http_client", client)

    with pytest.raises(VoiceProviderRequestError) as error:
        await build_provider().synthesize(
            text="已有回答",
            voice="Cherry",
            speed=1.0,
            audio_format="wav",
        )

    assert error.value.code == "provider_unavailable"
    await client.aclose()
