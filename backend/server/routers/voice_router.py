"""平台级语音输入输出接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from server.utils.auth_middleware import get_required_user
from yuxi.voice.models import (
    SpeechRequest,
    TranscriptionResponse,
    VoiceCapabilitiesResponse,
    VoiceErrorResponse,
    VoiceMetricEvent,
)
from yuxi.voice.service import VoiceService, VoiceServiceError, get_voice_service

voice_router = APIRouter(prefix="/voice", tags=["voice"])


@voice_router.get("/capabilities", response_model=VoiceCapabilitiesResponse)
async def get_voice_capabilities(
    _: Annotated[object, Depends(get_required_user)],
    service: Annotated[VoiceService, Depends(get_voice_service)],
) -> VoiceCapabilitiesResponse:
    """返回当前平台实际启用的 ASR/TTS 能力。"""

    return service.capabilities()


@voice_router.post(
    "/transcriptions",
    response_model=TranscriptionResponse,
    responses={
        400: {"model": VoiceErrorResponse},
        413: {"model": VoiceErrorResponse},
        415: {"model": VoiceErrorResponse},
    },
)
async def transcribe_audio(
    file: Annotated[UploadFile, File(...)],
    user: Annotated[object, Depends(get_required_user)],
    service: Annotated[VoiceService, Depends(get_voice_service)],
    language: Annotated[str | None, Form()] = None,
) -> TranscriptionResponse:
    """转写一次临时录音，仅返回草稿文字。"""

    try:
        audio = await file.read(service.settings.max_audio_bytes + 1)
        return await service.transcribe(
            audio=audio,
            filename=file.filename or "recording",
            content_type=file.content_type or "",
            language=language,
            user_id=str(getattr(user, "id", "unknown")),
        )
    except VoiceServiceError as exc:
        await service.record_error("asr", exc.code)
        raise _http_error(exc) from exc
    finally:
        await file.close()


@voice_router.post(
    "/speech",
    responses={200: {"content": {"audio/mpeg": {}}}, 413: {"model": VoiceErrorResponse}},
)
async def synthesize_speech(
    payload: SpeechRequest,
    user: Annotated[object, Depends(get_required_user)],
    service: Annotated[VoiceService, Depends(get_voice_service)],
) -> Response:
    """朗读已有助手文字或合法选区，不执行 Agent。"""

    try:
        result, request_id, cache_hit = await service.synthesize(
            text=payload.text,
            voice=payload.voice,
            speed=payload.speed,
            audio_format=payload.format,
            user_id=str(getattr(user, "id", "unknown")),
        )
    except VoiceServiceError as exc:
        await service.record_error("tts", exc.code)
        raise _http_error(exc) from exc

    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Cache-Control": "private, max-age=0, no-store",
            "X-Voice-Cache": "HIT" if cache_hit else "MISS",
            "X-Voice-Provider": result.provider,
            "X-Voice-Request-ID": request_id,
        },
    )


@voice_router.post("/metrics", status_code=204)
async def record_voice_metric(
    payload: VoiceMetricEvent,
    _: Annotated[object, Depends(get_required_user)],
    service: Annotated[VoiceService, Depends(get_voice_service)],
) -> None:
    """记录不含正文、音频和选区内容的语音交互事件。"""

    await service.record_client_event(payload.event)


def _http_error(error: VoiceServiceError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message, "request_id": error.request_id},
    )
