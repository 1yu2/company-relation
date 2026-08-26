"""平台语音接口的数据模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class VoiceCapability(BaseModel):
    """描述单项语音能力是否可用。"""

    enabled: bool
    provider: str | None = None
    reason: str | None = None


class AudioLimits(BaseModel):
    """描述语音输入允许的格式与资源限制。"""

    mime_types: list[str]
    max_bytes: int
    max_duration_seconds: int


class SpeechLimits(BaseModel):
    """描述语音输出允许的文本和播放参数。"""

    max_characters: int
    voices: list[str]
    formats: list[str]
    speed_min: float
    speed_max: float
    default_voice: str
    default_speed: float
    default_format: str


class VoiceCapabilitiesResponse(BaseModel):
    """返回前端渲染语音入口所需的平台能力。"""

    asr: VoiceCapability
    tts: VoiceCapability
    audio: AudioLimits
    speech: SpeechLimits


class TranscriptionResponse(BaseModel):
    """返回一次音频转写的文字和脱敏元数据。"""

    transcript: str
    language: str | None = None
    duration_ms: int
    provider: str
    request_id: str


class SpeechRequest(BaseModel):
    """请求将已有文字转换为音频。"""

    text: str
    voice: str = ""
    speed: float = 1.0
    format: str = "wav"


class VoiceErrorDetail(BaseModel):
    """统一语音错误响应，避免暴露供应商内部信息。"""

    code: str
    message: str
    request_id: str


class VoiceErrorResponse(BaseModel):
    """FastAPI 错误响应外层结构。"""

    detail: VoiceErrorDetail


class VoiceMetricEvent(BaseModel):
    """允许前端上报的脱敏语音交互事件。"""

    event: Literal[
        "transcription_started",
        "transcription_stopped",
        "transcription_cancelled",
        "transcription_completed",
        "speech_started",
        "speech_paused",
        "speech_resumed",
        "speech_stopped",
        "speech_completed",
        "speech_selection_used",
    ]

    model_config = {"extra": "forbid"}
