"""平台语音供应商适配器。"""

from .base import SpeechResult, TranscriptionResult, VoiceProvider
from .openai_compatible import OpenAICompatibleVoiceProvider, close_voice_http_client
from .qwen import QwenVoiceProvider, close_qwen_http_client

__all__ = [
    "OpenAICompatibleVoiceProvider",
    "QwenVoiceProvider",
    "SpeechResult",
    "TranscriptionResult",
    "VoiceProvider",
    "close_voice_http_client",
    "close_qwen_http_client",
]
