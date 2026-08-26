<template>
  <div class="voice-playback-control">
    <button
      v-if="state === 'idle' || state === 'stopped' || state === 'error'"
      type="button"
      class="voice-playback-button"
      :disabled="loading || !ttsEnabled || !hasText"
      :title="readTitle"
      aria-label="朗读助手消息"
      @click="handlePlay"
    >
      <Volume2 :size="15" />
      <span>朗读</span>
    </button>
    <button
      v-else-if="state === 'loading'"
      type="button"
      class="voice-playback-button"
      title="停止生成语音"
      @click="handleStop"
    >
      <LoaderCircle :size="15" class="voice-spin" />
      <span>生成中</span>
    </button>
    <button
      v-else-if="state === 'playing'"
      type="button"
      class="voice-playback-button"
      title="暂停朗读"
      @click="handlePause"
    >
      <Pause :size="15" />
      <span>暂停</span>
    </button>
    <button
      v-else
      type="button"
      class="voice-playback-button"
      title="继续朗读"
      @click="handleResume"
    >
      <Play :size="15" />
      <span>继续</span>
    </button>
    <button
      v-if="isActive && ['playing', 'paused'].includes(state)"
      type="button"
      class="voice-stop-button"
      title="停止朗读"
      aria-label="停止朗读"
      @click="handleStop"
    >
      <Square :size="12" fill="currentColor" />
    </button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { message } from 'ant-design-vue'
import { LoaderCircle, Pause, Play, Square, Volume2 } from 'lucide-vue-next'
import { recordVoiceMetric } from '@/apis/voice_api'
import { useVoiceCapabilities } from '@/composables/useVoiceCapabilities'
import { useVoicePlayback } from '@/composables/useVoicePlayback'

const props = defineProps({
  playbackId: { type: String, required: true },
  text: { type: String, default: '' },
  resolveText: { type: Function, default: null }
})

const { capabilities, loading, ttsEnabled, load } = useVoiceCapabilities()
const { state, error, isActive, play, pause, resume, stop, stopIfActive } = useVoicePlayback(
  props.playbackId,
  { onFinished: () => void recordVoiceMetric('speech_completed').catch(() => {}) }
)
const hasText = computed(() => Boolean(props.text.trim()))
const readTitle = computed(() => {
  if (loading.value) return '正在查询平台语音能力'
  if (!ttsEnabled.value) return '平台尚未配置语音朗读'
  return '朗读助手消息；选中正文后可只朗读选区'
})

const handlePlay = async () => {
  const resolvedText = String(props.resolveText?.() || '').trim()
  const selectedOrFullText = String(resolvedText || props.text || '').trim()
  if (!selectedOrFullText) return

  const limit = capabilities.value?.speech?.max_characters || 0
  if (limit && selectedOrFullText.length > limit) {
    message.warning(`内容超过 ${limit} 字，请选择部分正文后朗读。`)
    return
  }

  if (resolvedText && resolvedText !== props.text.trim()) {
    void recordVoiceMetric('speech_selection_used').catch(() => {})
  }
  void recordVoiceMetric('speech_started').catch(() => {})
  await play({
    text: selectedOrFullText,
    voice: capabilities.value?.speech?.default_voice || 'default',
    speed: capabilities.value?.speech?.default_speed || 1.0,
    format: capabilities.value?.speech?.default_format || 'mp3'
  })
}

const handlePause = () => {
  void recordVoiceMetric('speech_paused').catch(() => {})
  pause()
}

const handleResume = async () => {
  void recordVoiceMetric('speech_resumed').catch(() => {})
  await resume()
}

const handleStop = () => {
  void recordVoiceMetric('speech_stopped').catch(() => {})
  stop()
}

watch(error, (value) => {
  if (isActive.value && value) message.error(value)
})

onMounted(() => {
  void load().catch(() => {})
})
onUnmounted(stopIfActive)
</script>

<style lang="less" scoped>
.voice-playback-control {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.voice-playback-button,
.voice-stop-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 26px;
  padding: 0 7px;
  border: 0;
  border-radius: 6px;
  color: var(--gray-500);
  background: transparent;
  font-size: 12px;
  cursor: pointer;

  &:hover:not(:disabled) {
    color: var(--main-600);
    background: var(--main-30);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
  }
}

.voice-stop-button {
  width: 26px;
  padding: 0;
  color: var(--color-error-500);
}

.voice-spin {
  animation: voice-spin 1s linear infinite;
}

@keyframes voice-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
