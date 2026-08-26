"""Qwen-ASR 与 Qwen-TTS 的百炼 HTTP 协议适配器。"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .base import SpeechResult, TranscriptionResult, VoiceProvider
from .openai_compatible import VoiceProviderRequestError

_http_client: httpx.AsyncClient | None = None


@dataclass(slots=True)
class QwenVoiceProvider(VoiceProvider):
    """调用百炼 Qwen-ASR/TTS 接口，不保存音频或供应商响应。"""

    name: str
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = ""
    asr_timeout_seconds: float = 60
    asr_headers: dict[str, str] = field(default_factory=dict)
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = ""
    tts_timeout_seconds: float = 60
    tts_headers: dict[str, str] = field(default_factory=dict)
    tts_language_type: str = "Auto"

    async def transcribe(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult:
        """使用 Qwen-ASR 的 OpenAI 兼容协议识别 Base64 音频。"""

        del filename
        data_uri = f"data:{content_type};base64,{base64.b64encode(audio).decode('ascii')}"
        asr_options: dict[str, str | bool] = {"enable_itn": False}
        if language:
            asr_options["language"] = language.split("-", 1)[0].lower()

        payload = {
            "model": self.asr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}],
                }
            ],
            "stream": False,
            "asr_options": asr_options,
        }
        response = await _post_json(
            _build_url(self.asr_base_url, "/chat/completions"),
            payload,
            self.asr_api_key,
            self.asr_headers,
            self.asr_timeout_seconds,
        )
        try:
            message = response["choices"][0]["message"]
            transcript = _content_to_text(message.get("content"))
            language_value = _annotation_language(message.get("annotations"))
        except (KeyError, IndexError, TypeError) as exc:
            raise VoiceProviderRequestError("provider_unavailable") from exc

        return TranscriptionResult(
            transcript=transcript,
            language=language_value or language,
            provider=self.name,
        )

    async def synthesize(self, *, text: str, voice: str, speed: float, audio_format: str) -> SpeechResult:
        """调用 Qwen-TTS，并读取百炼返回的内联音频或临时 URL。"""

        del speed, audio_format
        response = await _post_json(
            _build_url(self.tts_base_url, "/services/aigc/multimodal-generation/generation"),
            {
                "model": self.tts_model,
                "input": {
                    "text": text,
                    "voice": voice,
                    "language_type": self.tts_language_type,
                },
            },
            self.tts_api_key,
            self.tts_headers,
            self.tts_timeout_seconds,
        )
        try:
            audio = response["output"]["audio"]
        except (KeyError, TypeError) as exc:
            raise VoiceProviderRequestError("provider_unavailable") from exc

        audio_data = audio.get("data") if isinstance(audio, dict) else None
        if isinstance(audio_data, str) and audio_data:
            try:
                content = base64.b64decode(audio_data, validate=True)
            except ValueError as exc:
                raise VoiceProviderRequestError("provider_unavailable") from exc
            if not content:
                raise VoiceProviderRequestError("provider_unavailable")
            return SpeechResult(content=content, media_type="audio/wav", provider=self.name)

        audio_url = audio.get("url") if isinstance(audio, dict) else None

        parsed_audio_url = urlparse(audio_url) if isinstance(audio_url, str) else None
        if (
            parsed_audio_url is None
            or parsed_audio_url.scheme not in {"http", "https"}
            or not parsed_audio_url.hostname
            or not parsed_audio_url.hostname.endswith(".aliyuncs.com")
        ):
            raise VoiceProviderRequestError("provider_unavailable")

        download_url = parsed_audio_url._replace(scheme="https").geturl()

        try:
            audio_response = await _get_http_client().get(download_url, timeout=self.tts_timeout_seconds)
        except httpx.TimeoutException as exc:
            raise VoiceProviderRequestError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise VoiceProviderRequestError("provider_unavailable") from exc

        if audio_response.is_error:
            raise VoiceProviderRequestError("provider_unavailable")
        media_type = audio_response.headers.get("content-type", "").split(";", 1)[0].strip()
        return SpeechResult(
            content=audio_response.content,
            media_type=media_type or "audio/wav",
            provider=self.name,
        )


async def _post_json(
    url: str,
    payload: dict,
    api_key: str,
    extra_headers: dict[str, str],
    timeout: float,
) -> dict:
    """发送 JSON 请求并将供应商错误统一脱敏。"""

    headers = {**extra_headers, "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = await _get_http_client().post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise VoiceProviderRequestError("provider_timeout") from exc
    except httpx.HTTPError as exc:
        raise VoiceProviderRequestError("provider_unavailable") from exc

    if response.status_code == 429:
        raise VoiceProviderRequestError("rate_limited")
    if response.is_error:
        raise VoiceProviderRequestError("provider_unavailable")
    try:
        data = response.json()
    except ValueError as exc:
        raise VoiceProviderRequestError("provider_unavailable") from exc
    if not isinstance(data, dict):
        raise VoiceProviderRequestError("provider_unavailable")
    return data


def _get_http_client() -> httpx.AsyncClient:
    """返回进程级 HTTP 连接池。"""

    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        )
    return _http_client


async def close_qwen_http_client() -> None:
    """关闭 Qwen 供应商共享连接池。"""

    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _build_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts).strip()


def _annotation_language(annotations: object) -> str | None:
    if not isinstance(annotations, list):
        return None
    for annotation in annotations:
        if isinstance(annotation, dict) and annotation.get("language"):
            return str(annotation["language"])
    return None
