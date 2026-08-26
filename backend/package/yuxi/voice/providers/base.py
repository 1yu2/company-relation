"""语音供应商的最小协议边界。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class TranscriptionResult:
    """供应商返回的音频转写结果。"""

    transcript: str
    language: str | None
    provider: str


@dataclass(slots=True)
class SpeechResult:
    """供应商返回的语音音频。"""

    content: bytes
    media_type: str
    provider: str


class VoiceProvider(ABC):
    """隔离供应商协议，禁止语音层触碰聊天和 AgentRun。"""

    @abstractmethod
    async def transcribe(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult:
        """将单个音频文件转换为文字。"""

    @abstractmethod
    async def synthesize(self, *, text: str, voice: str, speed: float, audio_format: str) -> SpeechResult:
        """将已有文字转换为音频。"""
