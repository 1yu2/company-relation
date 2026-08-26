"""平台级语音输入输出能力。"""

from .service import VoiceService, VoiceServiceError, get_voice_service

__all__ = ["VoiceService", "VoiceServiceError", "get_voice_service"]
