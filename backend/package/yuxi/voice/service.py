"""平台级语音用例服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiofiles

from yuxi.models.providers.cache import ModelInfo, model_cache
from yuxi.storage.redis import get_async_redis_client
from yuxi.utils.logging_config import logger

from .models import (
    AudioLimits,
    SpeechLimits,
    TranscriptionResponse,
    VoiceCapabilitiesResponse,
    VoiceCapability,
)
from .providers import QwenVoiceProvider, SpeechResult, VoiceProvider
from .providers.openai_compatible import VoiceProviderRequestError

_ALLOWED_AUDIO_MIME_TYPES = (
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
)
_ALLOWED_SPEECH_FORMATS = ("aac", "flac", "mp3", "opus", "pcm", "wav")
_QWEN_MAX_SOURCE_AUDIO_BYTES = 7 * 1024 * 1024
_AUDIO_SUFFIXES = {
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-wav": ".wav",
}
_METRIC_RETENTION_SECONDS = 7 * 24 * 60 * 60
_METRIC_BUCKETS = {
    "latency_ms": (100, 250, 500, 1000, 3000, 10000, 30000, 60000),
    "audio_duration_ms": (1000, 5000, 10000, 30000, 60000, 120000),
    "text_characters": (20, 100, 500, 1000, 4000),
}


class VoiceServiceError(Exception):
    """表示可安全返回给前端的语音业务错误。"""

    def __init__(self, code: str, message: str, status_code: int, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id or uuid4().hex


@dataclass(slots=True)
class VoiceSettings:
    """读取语音开关、资源限制和模型供应商缓存配置。"""

    asr_enabled: bool
    tts_enabled: bool
    provider_name: str
    asr_base_url: str
    asr_api_key: str
    asr_model: str
    asr_protocol: str
    asr_headers: dict[str, str]
    asr_timeout_seconds: float
    tts_base_url: str
    tts_api_key: str
    tts_model: str
    tts_protocol: str
    tts_headers: dict[str, str]
    tts_timeout_seconds: float
    tts_language_type: str
    max_audio_bytes: int
    max_audio_seconds: int
    max_tts_characters: int
    rate_limit_per_minute: int
    cache_ttl_seconds: int
    voices: tuple[str, ...]
    default_voice: str
    default_speed: float
    speed_min: float
    speed_max: float
    formats: tuple[str, ...]
    default_format: str

    @classmethod
    def from_env(cls) -> VoiceSettings:
        """构造使用安全默认值的语音配置。"""

        asr_models = model_cache.get_all_specs("asr")
        tts_models = model_cache.get_all_specs("tts")
        asr_model = asr_models[0] if len(asr_models) == 1 else None
        tts_model = tts_models[0] if len(tts_models) == 1 else None
        tts_extra = tts_model.model_extra if tts_model else {}
        voices = _voice_list(tts_extra.get("voices")) or ("Cherry",)
        default_voice = str(tts_extra.get("default_voice") or voices[0]).strip()
        tts_protocol = _model_protocol(tts_model)
        qwen_tts = tts_protocol == "qwen_tts"
        max_tts_characters = int(tts_extra.get("max_characters") or (600 if qwen_tts else 4000))
        return cls(
            asr_enabled=bool(asr_models),
            tts_enabled=bool(tts_models),
            provider_name=_provider_name(asr_model, tts_model),
            asr_base_url=asr_model.base_url if asr_model else "",
            asr_api_key=asr_model.api_key if asr_model else "",
            asr_model=asr_model.model_id if asr_model else "",
            asr_protocol=_model_protocol(asr_model),
            asr_headers=dict(asr_model.headers) if asr_model else {},
            asr_timeout_seconds=float(os.getenv("VOICE_ASR_TIMEOUT_SECONDS", "60")),
            tts_base_url=tts_model.base_url if tts_model else "",
            tts_api_key=tts_model.api_key if tts_model else "",
            tts_model=tts_model.model_id if tts_model else "",
            tts_protocol=tts_protocol,
            tts_headers=dict(tts_model.headers) if tts_model else {},
            tts_timeout_seconds=float(os.getenv("VOICE_TTS_TIMEOUT_SECONDS", "60")),
            tts_language_type=str(tts_extra.get("language_type") or "Auto"),
            max_audio_bytes=min(
                int(os.getenv("VOICE_MAX_AUDIO_BYTES", str(_QWEN_MAX_SOURCE_AUDIO_BYTES))),
                _QWEN_MAX_SOURCE_AUDIO_BYTES,
            ),
            max_audio_seconds=int(os.getenv("VOICE_MAX_AUDIO_SECONDS", "120")),
            max_tts_characters=max_tts_characters,
            rate_limit_per_minute=int(os.getenv("VOICE_RATE_LIMIT_PER_MINUTE", "10")),
            cache_ttl_seconds=int(os.getenv("VOICE_CACHE_TTL_SECONDS", "300")),
            voices=voices,
            default_voice=default_voice,
            default_speed=1.0,
            speed_min=1.0 if qwen_tts else 0.5,
            speed_max=1.0 if qwen_tts else 2.0,
            formats=("wav",) if qwen_tts else _ALLOWED_SPEECH_FORMATS,
            default_format="wav" if qwen_tts else "mp3",
        )

    @property
    def asr_configured(self) -> bool:
        return self.asr_enabled and bool(
            self.asr_base_url and self.asr_api_key and self.asr_model and self.asr_protocol == "qwen_asr"
        )

    @property
    def tts_configured(self) -> bool:
        return self.tts_enabled and bool(
            self.tts_base_url and self.tts_api_key and self.tts_model and self.tts_protocol == "qwen_tts"
        )


class VoiceService:
    """校验并协调语音供应商，不创建消息、请求或 AgentRun。"""

    def __init__(self, settings: VoiceSettings | None = None, provider: VoiceProvider | None = None):
        self.settings = settings or VoiceSettings.from_env()
        self.provider = provider or self._build_provider()

    def capabilities(self) -> VoiceCapabilitiesResponse:
        """返回当前部署实际可用的语音能力。"""

        return VoiceCapabilitiesResponse(
            asr=_capability(self.settings.asr_enabled, self.settings.asr_configured, self.settings.provider_name),
            tts=_capability(self.settings.tts_enabled, self.settings.tts_configured, self.settings.provider_name),
            audio=AudioLimits(
                mime_types=list(_ALLOWED_AUDIO_MIME_TYPES),
                max_bytes=self.settings.max_audio_bytes,
                max_duration_seconds=self.settings.max_audio_seconds,
            ),
            speech=SpeechLimits(
                max_characters=self.settings.max_tts_characters,
                voices=list(self.settings.voices),
                formats=list(self.settings.formats),
                speed_min=self.settings.speed_min,
                speed_max=self.settings.speed_max,
                default_voice=self.settings.default_voice,
                default_speed=self.settings.default_speed,
                default_format=self.settings.default_format,
            ),
        )

    async def transcribe(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
        language: str | None,
        user_id: str,
    ) -> TranscriptionResponse:
        """校验临时音频并返回文字，不写入任何对话事实。"""

        request_id = uuid4().hex
        if not self.settings.asr_configured:
            raise VoiceServiceError(
                "voice_not_configured", "平台尚未配置语音转写能力，请使用文字输入。", 503, request_id
            )

        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if normalized_type not in _ALLOWED_AUDIO_MIME_TYPES:
            raise VoiceServiceError("unsupported_audio_type", "当前音频格式不受支持，请重新录音。", 415, request_id)
        if not audio:
            raise VoiceServiceError("empty_transcript", "未检测到可转写的音频内容，请重新录音。", 422, request_id)
        if len(audio) > self.settings.max_audio_bytes:
            raise VoiceServiceError("audio_too_large", "录音文件超过平台大小限制，请缩短录音。", 413, request_id)

        await self._enforce_rate_limit(user_id, "asr", request_id)
        duration_ms = await _probe_audio_duration(audio, normalized_type, request_id)
        if duration_ms > self.settings.max_audio_seconds * 1000:
            raise VoiceServiceError("audio_too_long", "录音时长超过平台限制，请分段录音。", 413, request_id)

        started_at = time.perf_counter()
        provider_audio, provider_filename, provider_content_type = await _prepare_qwen_audio(
            audio,
            filename,
            normalized_type,
            request_id,
        )
        try:
            result = await self.provider.transcribe(
                audio=provider_audio,
                filename=provider_filename,
                content_type=provider_content_type,
                language=language,
            )
        except VoiceProviderRequestError as exc:
            raise _provider_error(exc.code, request_id) from exc

        transcript = result.transcript.strip()
        if not transcript:
            raise VoiceServiceError(
                "empty_transcript", "没有识别到有效文字，请重新录音或改用文字输入。", 422, request_id
            )

        latency_ms = round((time.perf_counter() - started_at) * 1000)
        logger.info(
            "Voice ASR completed request_id={} duration_ms={} latency_ms={} transcript_chars={}",
            request_id,
            duration_ms,
            latency_ms,
            len(transcript),
        )
        await self._record_metric(
            "asr",
            "success",
            latency_ms=latency_ms,
            audio_duration_ms=duration_ms,
            text_characters=len(transcript),
        )
        return TranscriptionResponse(
            transcript=transcript,
            language=result.language,
            duration_ms=duration_ms,
            provider=result.provider,
            request_id=request_id,
        )

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        speed: float,
        audio_format: str,
        user_id: str,
    ) -> tuple[SpeechResult, str, bool]:
        """将已有文字转换为短期音频响应。"""

        request_id = uuid4().hex
        if not self.settings.tts_configured:
            raise VoiceServiceError("voice_not_configured", "平台尚未配置语音朗读能力。", 503, request_id)

        normalized_text = text.strip()
        if not normalized_text:
            raise VoiceServiceError("empty_transcript", "没有可朗读的文字。", 422, request_id)
        if len(normalized_text) > self.settings.max_tts_characters:
            raise VoiceServiceError(
                "speech_text_too_long",
                "朗读文字超过平台限制，请选择部分内容后重试。",
                413,
                request_id,
            )

        normalized_voice = voice.strip() or self.settings.default_voice
        if normalized_voice not in self.settings.voices:
            raise VoiceServiceError("invalid_voice_parameter", "所选声音不可用，请刷新能力配置。", 422, request_id)
        if not self.settings.speed_min <= speed <= self.settings.speed_max:
            raise VoiceServiceError("invalid_voice_parameter", "语速超出平台允许范围。", 422, request_id)

        normalized_format = audio_format.strip().lower() or self.settings.default_format
        if normalized_format not in self.settings.formats:
            raise VoiceServiceError("invalid_voice_parameter", "所选音频格式不可用。", 422, request_id)

        await self._enforce_rate_limit(user_id, "tts", request_id)
        cache_key = _speech_cache_key(
            self.settings.provider_name,
            self.settings.tts_model,
            normalized_text,
            normalized_voice,
            speed,
            normalized_format,
        )
        cached = await self._get_cached_speech(cache_key, request_id)
        if cached is not None:
            await self._record_metric("tts", "success", cache_hit=True)
            return cached, request_id, True

        started_at = time.perf_counter()
        try:
            result = await self.provider.synthesize(
                text=normalized_text,
                voice=normalized_voice,
                speed=speed,
                audio_format=normalized_format,
            )
        except VoiceProviderRequestError as exc:
            raise _provider_error(exc.code, request_id) from exc

        if not result.content:
            raise VoiceServiceError("provider_unavailable", "语音供应商未返回有效音频，请稍后重试。", 502, request_id)

        latency_ms = round((time.perf_counter() - started_at) * 1000)
        await self._cache_speech(cache_key, result, request_id)
        await self._record_metric(
            "tts",
            "success",
            latency_ms=latency_ms,
            text_characters=len(normalized_text),
            cache_hit=False,
        )
        logger.info(
            "Voice TTS completed request_id={} latency_ms={} text_chars={} audio_bytes={}",
            request_id,
            latency_ms,
            len(normalized_text),
            len(result.content),
        )
        return result, request_id, False

    async def record_error(self, action: str, error_code: str) -> None:
        """记录脱敏错误计数，不影响原始请求的错误响应。"""

        await self._record_metric(action, "error", error_code=error_code)

    async def record_client_event(self, event: str) -> None:
        """记录前端固定枚举事件，不接收正文、选区或音频数据。"""

        await self._record_metric("client", event)

    async def _record_metric(
        self,
        action: str,
        outcome: str,
        *,
        latency_ms: int | None = None,
        audio_duration_ms: int | None = None,
        text_characters: int | None = None,
        error_code: str | None = None,
        cache_hit: bool | None = None,
    ) -> None:
        """将有限字段写入 Redis 短期聚合指标，不保存敏感内容。"""

        date_key = datetime.now(tz=UTC).strftime("%Y%m%d")
        key = f"voice:metrics:{date_key}:{action}"
        try:
            redis = await get_async_redis_client()
            await redis.hincrby(key, "requests", 1)
            await redis.hincrby(key, f"outcome_{outcome}", 1)
            if error_code:
                await redis.hincrby(key, f"error_{error_code}", 1)
            if cache_hit is not None:
                await redis.hincrby(key, "cache_hit_count" if cache_hit else "cache_miss_count", 1)
            for field, value in (
                ("latency_ms", latency_ms),
                ("audio_duration_ms", audio_duration_ms),
                ("text_characters", text_characters),
            ):
                if value is None:
                    continue
                await redis.hincrby(key, f"{field}_sum", value)
                await redis.hincrby(key, f"{field}_count", 1)
                for bucket in _METRIC_BUCKETS[field]:
                    if value <= bucket:
                        await redis.hincrby(key, f"{field}_le_{bucket}", 1)
            await redis.expire(key, _METRIC_RETENTION_SECONDS)
        except Exception as exc:
            logger.warning("Voice metrics write failed action={} error={}", action, type(exc).__name__)

    async def _enforce_rate_limit(self, user_id: str, action: str, request_id: str) -> None:
        if self.settings.rate_limit_per_minute <= 0:
            return

        minute = datetime.now(tz=UTC).strftime("%Y%m%d%H%M")
        key = f"voice:rate:{action}:{user_id}:{minute}"
        try:
            redis = await get_async_redis_client()
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 120)
        except Exception as exc:
            logger.warning("Voice rate limiter unavailable request_id={}: {}", request_id, type(exc).__name__)
            raise VoiceServiceError(
                "provider_unavailable", "语音服务暂时不可用，请稍后重试。", 503, request_id
            ) from exc

        if count > self.settings.rate_limit_per_minute:
            raise VoiceServiceError("rate_limited", "语音请求过于频繁，请稍后重试。", 429, request_id)

    async def _get_cached_speech(self, key: str, request_id: str) -> SpeechResult | None:
        if self.settings.cache_ttl_seconds <= 0:
            return None
        try:
            redis = await get_async_redis_client()
            raw = await redis.get(key)
        except Exception as exc:
            logger.warning("Voice cache unavailable request_id={}: {}", request_id, type(exc).__name__)
            raise VoiceServiceError(
                "provider_unavailable", "语音服务暂时不可用，请稍后重试。", 503, request_id
            ) from exc

        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return SpeechResult(
                content=bytes.fromhex(payload["content_hex"]),
                media_type=payload["media_type"],
                provider=payload["provider"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await redis.delete(key)
            return None

    async def _cache_speech(self, key: str, result: SpeechResult, request_id: str) -> None:
        if self.settings.cache_ttl_seconds <= 0:
            return
        payload = json.dumps(
            {
                "content_hex": result.content.hex(),
                "media_type": result.media_type,
                "provider": result.provider,
            }
        )
        try:
            redis = await get_async_redis_client()
            await redis.set(key, payload, ex=self.settings.cache_ttl_seconds)
        except Exception as exc:
            logger.warning("Voice cache write failed request_id={}: {}", request_id, type(exc).__name__)

    def _build_provider(self) -> VoiceProvider:
        return QwenVoiceProvider(
            name=self.settings.provider_name,
            asr_base_url=self.settings.asr_base_url,
            asr_api_key=self.settings.asr_api_key,
            asr_model=self.settings.asr_model,
            asr_timeout_seconds=self.settings.asr_timeout_seconds,
            asr_headers=self.settings.asr_headers,
            tts_base_url=self.settings.tts_base_url,
            tts_api_key=self.settings.tts_api_key,
            tts_model=self.settings.tts_model,
            tts_timeout_seconds=self.settings.tts_timeout_seconds,
            tts_headers=self.settings.tts_headers,
            tts_language_type=self.settings.tts_language_type,
        )


def get_voice_service() -> VoiceService:
    """为路由创建读取当前部署配置的轻量服务实例。"""

    return VoiceService()


def _model_protocol(model: ModelInfo | None) -> str:
    return str(model.protocol_override or "").strip().lower() if model else ""


def _provider_name(asr_model: ModelInfo | None, tts_model: ModelInfo | None) -> str:
    providers = [model.provider_id for model in (asr_model, tts_model) if model]
    return "+".join(dict.fromkeys(providers)) or "model-provider"


def _voice_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _capability(enabled: bool, configured: bool, provider: str) -> VoiceCapability:
    if configured:
        return VoiceCapability(enabled=True, provider=provider)
    reason = "disabled" if not enabled else "missing_configuration"
    return VoiceCapability(enabled=False, reason=reason)


def _provider_error(code: str, request_id: str) -> VoiceServiceError:
    if code == "provider_timeout":
        return VoiceServiceError(code, "语音供应商响应超时，请稍后重试。", 504, request_id)
    if code == "rate_limited":
        return VoiceServiceError(code, "语音供应商请求已达到限制，请稍后重试。", 429, request_id)
    return VoiceServiceError("provider_unavailable", "语音供应商暂时不可用，请稍后重试。", 502, request_id)


async def _probe_audio_duration(audio: bytes, content_type: str, request_id: str) -> int:
    suffix = _AUDIO_SUFFIXES[content_type]
    descriptor, path_value = tempfile.mkstemp(prefix="yuxi-voice-", suffix=suffix)
    os.close(descriptor)
    path = Path(path_value)
    try:
        async with aiofiles.open(path, "wb") as file:
            await file.write(audio)

        if suffix == ".wav":
            try:
                return await asyncio.to_thread(_wav_duration_ms, path)
            except (wave.Error, EOFError):
                pass
        return await _ffprobe_duration_ms(path, request_id)
    finally:
        path.unlink(missing_ok=True)


async def _prepare_qwen_audio(
    audio: bytes,
    filename: str,
    content_type: str,
    request_id: str,
) -> tuple[bytes, str, str]:
    """将浏览器常见录音格式转换为 Qwen-ASR 稳定支持的 WAV。"""

    if content_type in {"audio/wav", "audio/x-wav", "audio/mpeg"}:
        suffix = _AUDIO_SUFFIXES[content_type]
        return audio, filename or f"recording{suffix}", "audio/wav" if suffix == ".wav" else "audio/mpeg"

    input_descriptor, input_path_value = tempfile.mkstemp(
        prefix="yuxi-voice-input-",
        suffix=_AUDIO_SUFFIXES[content_type],
    )
    output_descriptor, output_path_value = tempfile.mkstemp(prefix="yuxi-voice-output-", suffix=".wav")
    os.close(input_descriptor)
    os.close(output_descriptor)
    input_path = Path(input_path_value)
    output_path = Path(output_path_value)
    try:
        async with aiofiles.open(input_path, "wb") as file:
            await file.write(audio)

        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError as exc:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise VoiceServiceError("unsupported_audio_type", "无法转换该录音，请重新录音。", 415, request_id) from exc

        if process.returncode != 0:
            raise VoiceServiceError("unsupported_audio_type", "无法转换该录音，请重新录音。", 415, request_id)
        async with aiofiles.open(output_path, "rb") as file:
            converted = await file.read()
        if not converted:
            raise VoiceServiceError("unsupported_audio_type", "无法转换该录音，请重新录音。", 415, request_id)
        return converted, "recording.wav", "audio/wav"
    except FileNotFoundError as exc:
        logger.error("ffmpeg is required for Qwen voice conversion request_id={}", request_id)
        raise VoiceServiceError(
            "provider_unavailable",
            "服务器无法转换浏览器录音，请联系管理员检查语音运行环境。",
            503,
            request_id,
        ) from exc
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def _wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio_file:
        frame_rate = audio_file.getframerate()
        if frame_rate <= 0:
            raise wave.Error("invalid frame rate")
        return round(audio_file.getnframes() / frame_rate * 1000)


async def _ffprobe_duration_ms(path: Path, request_id: str) -> int:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        logger.error("ffprobe is required for voice duration validation request_id={}", request_id)
        raise VoiceServiceError(
            "provider_unavailable",
            "服务器无法校验录音时长，请联系管理员检查语音运行环境。",
            503,
            request_id,
        ) from exc

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError as exc:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise VoiceServiceError("unsupported_audio_type", "无法读取该音频，请重新录音。", 415, request_id) from exc

    if process.returncode != 0:
        raise VoiceServiceError("unsupported_audio_type", "无法读取该音频，请重新录音。", 415, request_id)
    try:
        duration = float(stdout.decode().strip())
    except ValueError as exc:
        raise VoiceServiceError("unsupported_audio_type", "无法读取该音频，请重新录音。", 415, request_id) from exc
    if duration <= 0:
        raise VoiceServiceError("unsupported_audio_type", "无法读取该音频，请重新录音。", 415, request_id)
    return round(duration * 1000)


def _speech_cache_key(
    provider: str,
    model: str,
    text: str,
    voice: str,
    speed: float,
    audio_format: str,
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "provider": provider,
                "model": model,
                "text": text,
                "voice": voice,
                "speed": speed,
                "format": audio_format,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"voice:speech:{digest}"
