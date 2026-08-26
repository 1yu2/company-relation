<template>
  <div class="voice-recorder">
    <button
      v-if="status !== 'recording'"
      type="button"
      class="voice-icon-button"
      :class="{ 'has-error': status === 'error' }"
      :disabled="disabled || loading || isBusy || !asrEnabled"
      :title="buttonTitle"
      aria-label="开始语音输入"
      @click="start"
    >
      <LoaderCircle v-if="isBusy" :size="16" class="voice-spin" />
      <RotateCcw v-else-if="status === 'ready' || status === 'error'" :size="16" />
      <Mic v-else :size="16" />
    </button>

    <template v-else>
      <span class="voice-recording-status" aria-live="polite">
        <span class="voice-recording-dot"></span>
        {{ formattedDuration }}
      </span>
      <button
        type="button"
        class="voice-icon-button is-stop"
        title="停止并转写"
        aria-label="停止录音并转写"
        @click="handleStop"
      >
        <Square :size="14" fill="currentColor" />
      </button>
      <button
        type="button"
        class="voice-icon-button"
        title="取消录音"
        aria-label="取消录音"
        @click="handleCancel"
      >
        <X :size="16" />
      </button>
    </template>

    <span v-if="status === 'transcribing'" class="voice-status-text">正在转写</span>
  </div>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue'
import { message } from 'ant-design-vue'
import { LoaderCircle, Mic, RotateCcw, Square, X } from 'lucide-vue-next'
import { recordVoiceMetric } from '@/apis/voice_api'
import { useVoiceCapabilities } from '@/composables/useVoiceCapabilities'
import { useVoiceRecorder } from '@/composables/useVoiceRecorder'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  threadId: { type: String, default: '' }
})

const emit = defineEmits(['transcript'])
const { capabilities, loading, asrEnabled, load } = useVoiceCapabilities()
const threadId = computed(() => props.threadId)
const { status, error, elapsedSeconds, isBusy, start, stop, cancel } = useVoiceRecorder({
  capabilities,
  threadId,
  onTranscript: (transcript, metadata) => {
    void recordVoiceMetric('transcription_completed').catch(() => {})
    emit('transcript', transcript, metadata)
  }
})

const formattedDuration = computed(() => {
  const minutes = Math.floor(elapsedSeconds.value / 60)
  const seconds = String(elapsedSeconds.value % 60).padStart(2, '0')
  return `${minutes}:${seconds}`
})

const handleStop = () => {
  void recordVoiceMetric('transcription_stopped').catch(() => {})
  stop()
}

const handleCancel = () => {
  void recordVoiceMetric('transcription_cancelled').catch(() => {})
  cancel()
}

const buttonTitle = computed(() => {
  if (loading.value) return '正在查询平台语音能力'
  if (!asrEnabled.value) return '平台尚未配置语音转写，请使用文字输入'
  if (status.value === 'ready') return '重新录音'
  if (status.value === 'error') return '语音输入失败，点击重试'
  return '语音输入'
})

watch(error, (value) => {
  if (value) message.error(value)
})

watch(status, (value) => {
  if (value === 'recording') {
    void recordVoiceMetric('transcription_started').catch(() => {})
  }
})

onMounted(() => {
  void load({ force: true }).catch(() => {})
})
</script>

<style lang="less" scoped>
.voice-recorder {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.voice-icon-button {
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 7px;
  color: var(--gray-500);
  background: transparent;
  cursor: pointer;

  &:hover:not(:disabled) {
    color: var(--main-600);
    background: var(--main-30);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
  }

  &.is-stop,
  &.has-error {
    color: var(--color-error-500);
  }
}

.voice-recording-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-error-500);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.voice-recording-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  animation: voice-pulse 1.2s ease-in-out infinite;
}

.voice-status-text {
  color: var(--gray-500);
  font-size: 12px;
}

.voice-spin {
  animation: voice-spin 1s linear infinite;
}

@keyframes voice-pulse {
  50% {
    opacity: 0.35;
  }
}

@keyframes voice-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
