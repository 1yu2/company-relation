import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { transcribeVoice } from '@/apis/voice_api'
import { permissionMessage, resolveRecordingMimeType } from '@/utils/voice_recording'

/** 管理浏览器非实时录音、临时上传和资源释放。 */
export function useVoiceRecorder({ capabilities, threadId, onTranscript }) {
  const status = ref('idle')
  const elapsedSeconds = ref(0)
  const error = ref(null)

  let mediaRecorder = null
  let mediaStream = null
  let chunks = []
  let timer = null
  let activeSession = 0
  let cancelledSession = 0

  const isRecording = computed(() => status.value === 'recording')
  const isBusy = computed(() => ['requesting_permission', 'stopping', 'transcribing'].includes(status.value))
  const maxDurationSeconds = computed(() => capabilities.value?.audio?.max_duration_seconds || 120)

  const start = async () => {
    if (isRecording.value || isBusy.value) return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('当前浏览器不支持麦克风录音，请使用文字输入。')
      return
    }

    const mimeType = resolveRecordingMimeType(
      capabilities.value?.audio?.mime_types || [],
      (candidate) => MediaRecorder.isTypeSupported(candidate)
    )
    if (!mimeType) {
      setError('当前浏览器没有平台支持的录音格式，请更换浏览器。')
      return
    }

    const session = ++activeSession
    cancelledSession = 0
    error.value = null
    status.value = 'requesting_permission'

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (session !== activeSession) {
        releaseStream()
        return
      }

      chunks = []
      elapsedSeconds.value = 0
      mediaRecorder = new MediaRecorder(mediaStream, { mimeType })
      mediaRecorder.addEventListener('dataavailable', (event) => {
        if (event.data?.size) chunks.push(event.data)
      })
      mediaRecorder.addEventListener('stop', () => void handleStopped(session, mimeType), {
        once: true
      })
      mediaRecorder.start()
      status.value = 'recording'
      timer = window.setInterval(() => {
        elapsedSeconds.value += 1
        if (elapsedSeconds.value >= maxDurationSeconds.value) {
          stop()
        }
      }, 1000)
    } catch (requestError) {
      releaseResources()
      setError(permissionMessage(requestError))
    }
  }

  const stop = () => {
    if (status.value !== 'recording' || !mediaRecorder) return
    status.value = 'stopping'
    clearTimer()
    if (mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop()
    }
  }

  const cancel = () => {
    const session = activeSession
    cancelledSession = session
    activeSession += 1
    clearTimer()
    chunks = []

    if (mediaRecorder?.state && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop()
    }
    releaseResources()
    elapsedSeconds.value = 0
    error.value = null
    status.value = 'idle'
  }

  const reset = () => {
    if (isRecording.value || isBusy.value) return
    error.value = null
    status.value = 'idle'
  }

  const handleStopped = async (session, mimeType) => {
    clearTimer()
    releaseStream()
    mediaRecorder = null

    if (session !== activeSession || session === cancelledSession) return
    const blob = new Blob(chunks, { type: mimeType })
    chunks = []
    if (!blob.size) {
      setError('没有录到有效音频，请重新录音。')
      return
    }

    status.value = 'transcribing'
    try {
      const file = new File([blob], `recording.${fileExtension(mimeType)}`, { type: mimeType })
      const result = await transcribeVoice(file, navigator.language || '')
      if (session !== activeSession) return
      const transcript = String(result?.transcript || '').trim()
      if (!transcript) {
        setError('没有识别到有效文字，请重新录音。')
        return
      }
      onTranscript(transcript, result)
      status.value = 'ready'
    } catch (requestError) {
      if (session === activeSession) setError(readableError(requestError))
    }
  }

  const setError = (message) => {
    error.value = message
    status.value = 'error'
  }

  const clearTimer = () => {
    if (timer) {
      window.clearInterval(timer)
      timer = null
    }
  }

  const releaseStream = () => {
    mediaStream?.getTracks().forEach((track) => track.stop())
    mediaStream = null
  }

  const releaseResources = () => {
    clearTimer()
    releaseStream()
    mediaRecorder = null
  }

  watch(threadId, () => cancel())
  onBeforeUnmount(() => cancel())

  return {
    status,
    error,
    elapsedSeconds,
    isRecording,
    isBusy,
    start,
    stop,
    cancel,
    reset
  }
}

function fileExtension(mimeType) {
  const baseType = mimeType.split(';', 1)[0]
  return {
    'audio/mp4': 'm4a',
    'audio/ogg': 'ogg',
    'audio/wav': 'wav',
    'audio/webm': 'webm'
  }[baseType]
}

function readableError(error) {
  return error?.response?.data?.detail?.message || error?.message || '语音转写失败，请重新录音。'
}
