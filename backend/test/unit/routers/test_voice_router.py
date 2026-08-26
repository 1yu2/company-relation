from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.voice_router import voice_router
from server.utils.auth_middleware import get_required_user
from yuxi.voice.models import (
    AudioLimits,
    SpeechLimits,
    TranscriptionResponse,
    VoiceCapabilitiesResponse,
    VoiceCapability,
)
from yuxi.voice.providers.base import SpeechResult
from yuxi.voice.service import get_voice_service

pytestmark = pytest.mark.unit


class StubVoiceService:
    """隔离路由测试与外部供应商、Redis 和数据库。"""

    settings = SimpleNamespace(max_audio_bytes=1024)

    def capabilities(self):
        return VoiceCapabilitiesResponse(
            asr=VoiceCapability(enabled=True, provider="stub"),
            tts=VoiceCapability(enabled=True, provider="stub"),
            audio=AudioLimits(mime_types=["audio/wav"], max_bytes=1024, max_duration_seconds=60),
            speech=SpeechLimits(
                max_characters=100,
                voices=["default"],
                formats=["mp3"],
                speed_min=0.5,
                speed_max=2.0,
                default_voice="default",
                default_speed=1.0,
                default_format="mp3",
            ),
        )

    async def transcribe(self, **kwargs):
        assert kwargs["audio"] == b"wave"
        assert kwargs["user_id"] == "9"
        return TranscriptionResponse(
            transcript="草稿文字",
            language="zh-CN",
            duration_ms=120,
            provider="stub",
            request_id="request-1",
        )

    async def synthesize(self, **kwargs):
        assert kwargs["text"] == "已有回答"
        assert kwargs["user_id"] == "9"
        return SpeechResult(content=b"audio", media_type="audio/mpeg", provider="stub"), "request-2", False

    async def record_client_event(self, event):
        assert event == "speech_paused"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(voice_router, prefix="/api")
    app.dependency_overrides[get_required_user] = lambda: SimpleNamespace(id=9)
    app.dependency_overrides[get_voice_service] = StubVoiceService
    return TestClient(app)


def test_capabilities_require_existing_auth_dependency(client):
    response = client.get("/api/voice/capabilities")

    assert response.status_code == 200
    assert response.json()["asr"]["enabled"] is True


def test_transcription_only_returns_draft(client):
    response = client.post(
        "/api/voice/transcriptions",
        files={"file": ("recording.wav", b"wave", "audio/wav")},
        data={"language": "zh-CN"},
    )

    assert response.status_code == 200
    assert response.json()["transcript"] == "草稿文字"


def test_speech_returns_audio_stream(client):
    response = client.post(
        "/api/voice/speech",
        json={"text": "已有回答", "voice": "default", "speed": 1.0, "format": "mp3"},
    )

    assert response.status_code == 200
    assert response.content == b"audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-voice-request-id"] == "request-2"


def test_metrics_accepts_only_fixed_event_names(client):
    response = client.post("/api/voice/metrics", json={"event": "speech_paused"})

    assert response.status_code == 204


def test_metrics_rejects_text_payload(client):
    response = client.post("/api/voice/metrics", json={"event": "speech_paused", "text": "敏感内容"})

    assert response.status_code == 422
