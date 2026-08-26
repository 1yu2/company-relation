"""OpenAI-compatible ASR/TTS HTTP 协议适配器。"""

from __future__ import annotations

from dataclasses import dataclass
import httpx

from .base import SpeechResult, TranscriptionResult, VoiceProvider

_http_client: httpx.AsyncClient | None = None

_MEDIA_TYPES = {
    "aac": "audio/aac",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "pcm": "audio/L16",
    "wav": "audio/wav",
}


class VoiceProviderRequestError(Exception):
    """表示已脱敏的供应商请求失败。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        )
    return _http_client


async def close_voice_http_client() -> None:
    """关闭平台共享的语音供应商 HTTP 连接池。"""

    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


@dataclass(slots=True)
class OpenAICompatibleVoiceProvider(VoiceProvider):
    """调用 OpenAI-compatible 音频转写与合成接口。"""

    name: str
    asr_base_url: str
    asr_api_key: str
    asr_model: str
    asr_endpoint: str
    asr_timeout_seconds: float
    tts_base_url: str
    tts_api_key: str
    tts_model: str
    tts_endpoint: str
    tts_timeout_seconds: float

    async def transcribe(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult:
        """按 multipart 协议请求兼容的音频转写接口。"""

        data = {"model": self.asr_model}
        if language:
            data["language"] = language

        try:
            response = await _get_http_client().post(
                _build_url(self.asr_base_url, self.asr_endpoint),
                headers=_build_headers(self.asr_api_key),
                data=data,
                files={"file": (filename, audio, content_type)},
                timeout=self.asr_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise VoiceProviderRequestError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise VoiceProviderRequestError("provider_unavailable") from exc

        _raise_for_provider_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoiceProviderRequestError("provider_unavailable") from exc

        transcript = str(payload.get("text") or payload.get("transcript") or "").strip()
        language_value = payload.get("language")
        return TranscriptionResult(
            transcript=transcript,
            language=str(language_value) if language_value else language,
            provider=self.name,
        )

    async def synthesize(self, *, text: str, voice: str, speed: float, audio_format: str) -> SpeechResult:
        """按 JSON 协议请求兼容的语音合成接口。"""

        try:
            response = await _get_http_client().post(
                _build_url(self.tts_base_url, self.tts_endpoint),
                headers=_build_headers(self.tts_api_key),
                json={
                    "model": self.tts_model,
                    "input": text,
                    "voice": voice,
                    "speed": speed,
                    "response_format": audio_format,
                },
                timeout=self.tts_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise VoiceProviderRequestError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise VoiceProviderRequestError("provider_unavailable") from exc

        _raise_for_provider_status(response)
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        return SpeechResult(
            content=response.content,
            media_type=media_type or _MEDIA_TYPES[audio_format],
            provider=self.name,
        )


def _build_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _build_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _raise_for_provider_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise VoiceProviderRequestError("rate_limited")
    if response.is_error:
        raise VoiceProviderRequestError("provider_unavailable")
