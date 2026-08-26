import { computed, ref } from 'vue'
import { synthesizeVoice } from '@/apis/voice_api'

const state = ref('idle')
const activeId = ref('')
const error = ref(null)

let audio = null
let objectUrl = ''
let abortController = null
let requestVersion = 0

/** 维护平台唯一音频播放器，新的朗读会终止旧音频。 */
export function useVoicePlayback(playbackId = '', { onFinished } = {}) {
  const isActive = computed(() => activeId.value === playbackId)
  const localState = computed(() => (isActive.value ? state.value : 'idle'))

  const play = async ({ text, voice = 'default', speed = 1.0, format = 'mp3' }) => {
    const normalizedText = String(text || '').trim()
    if (!normalizedText) return

    stop()
    const version = ++requestVersion
    activeId.value = playbackId
    state.value = 'loading'
    error.value = null
    abortController = new AbortController()

    try {
      const response = await synthesizeVoice(
        { text: normalizedText, voice, speed, format },
        abortController.signal
      )
      if (version !== requestVersion || activeId.value !== playbackId) return

      const blob = await response.blob()
      if (version !== requestVersion || activeId.value !== playbackId) return
      objectUrl = URL.createObjectURL(blob)
      audio = new Audio(objectUrl)
      audio.addEventListener('ended', () => finish(playbackId, onFinished), { once: true })
      audio.addEventListener('error', () => fail(playbackId, '音频播放失败，请稍后重试。'), {
        once: true
      })
      await audio.play()
      if (activeId.value === playbackId) state.value = 'playing'
    } catch (requestError) {
      if (requestError?.name === 'AbortError' || version !== requestVersion) return
      fail(playbackId, readableError(requestError))
    }
  }

  const pause = () => {
    if (!isActive.value || !audio || state.value !== 'playing') return
    audio.pause()
    state.value = 'paused'
  }

  const resume = async () => {
    if (!isActive.value || !audio || state.value !== 'paused') return
    try {
      await audio.play()
      state.value = 'playing'
    } catch (playError) {
      fail(playbackId, readableError(playError))
    }
  }

  const stopIfActive = () => {
    if (isActive.value) stop()
  }

  return {
    state: localState,
    error,
    isActive,
    play,
    pause,
    resume,
    stop,
    stopIfActive
  }
}

/** 停止任何正在请求或播放的平台语音。 */
export function stopVoicePlayback() {
  stop()
}

function stop() {
  requestVersion += 1
  abortController?.abort()
  abortController = null
  if (audio) {
    audio.pause()
    audio.currentTime = 0
  }
  cleanupAudio()
  activeId.value = ''
  state.value = 'stopped'
  error.value = null
}

function finish(playbackId, onFinished) {
  if (activeId.value !== playbackId) return
  cleanupAudio()
  activeId.value = ''
  state.value = 'idle'
  onFinished?.()
}

function fail(playbackId, message) {
  if (activeId.value !== playbackId) return
  cleanupAudio()
  state.value = 'error'
  error.value = message
}

function cleanupAudio() {
  audio = null
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
}

function readableError(requestError) {
  return (
    requestError?.response?.data?.detail?.message ||
    requestError?.message ||
    '语音朗读失败，请稍后重试。'
  )
}
