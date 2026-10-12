<template>
  <section v-if="requests.length" class="queued-request-panel" aria-label="排队请求">
    <div v-if="queueSnapshot.status === 'paused'" class="queued-request-notice is-paused">
      <span>{{ pausedMessage }}</span>
      <button
        type="button"
        class="queued-request-continue"
        :disabled="continueInFlight"
        @click="$emit('continue')"
      >
        <Play :size="14" fill="currentColor" />
        继续队列
      </button>
    </div>
    <div v-else-if="queueSnapshot.status === 'interrupted'" class="queued-request-notice">
      当前任务正在等待回答或审批，完成后将继续处理后续请求。
    </div>
    <div class="queued-request-list">
      <div v-for="request in requests" :key="request.request_id" class="queued-request-row">
        <CornerDownRight :size="16" class="queued-request-icon" aria-hidden="true" />
        <span class="queued-request-content" :title="request.content || '排队请求'">
          {{ request.content || '排队请求' }}
        </span>
        <div class="queued-request-actions">
          <span v-if="request.queue_policy === 'steer'" class="queued-request-position">
            引导 · 下一条执行
          </span>
          <button
            v-if="canSteer(request)"
            type="button"
            class="queued-request-steer"
            :disabled="steeringRequestIds.has(request.request_id)"
            @click="$emit('steer', request.request_id)"
          >
            <CornerDownRight :size="14" aria-hidden="true" />
            引导
          </button>
          <button
            v-if="canCancel(request)"
            type="button"
            class="queued-request-delete lucide-icon-btn"
            :disabled="cancellingRequestIds.has(request.request_id)"
            :aria-label="`删除排队请求：${request.content || '排队请求'}`"
            @click="$emit('cancel', request.request_id)"
          >
            <Trash2 :size="16" />
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { CornerDownRight, Play, Trash2 } from 'lucide-vue-next'

// 仅展示父组件的队列快照，所有操作交回父组件处理。
defineProps({
  requests: { type: Array, default: () => [] },
  queueSnapshot: { type: Object, default: () => ({}) },
  continueInFlight: { type: Boolean, default: false },
  pausedMessage: { type: String, default: '' },
  steeringRequestIds: { type: Object, default: () => new Set() },
  cancellingRequestIds: { type: Object, default: () => new Set() },
  canSteer: { type: Function, required: true },
  canCancel: { type: Function, required: true }
})

defineEmits(['continue', 'steer', 'cancel'])
</script>

<style scoped lang="less">
.queued-request-panel {
  max-height: 196px;
  overflow-y: auto;
  padding: 6px 12px 18px;
  background: var(--gray-25);
  border: 1px solid var(--gray-150);
  border-radius: 16px 16px 12px 12px;
}

.queued-request-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 6px 4px;
  padding: 0;
  color: var(--color-text-tertiary);
  background: transparent;
  font-size: 13px;
  line-height: 1.5;

  &.is-paused {
    color: var(--color-warning-700);
    background: transparent;
  }
}

.queued-request-continue {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  padding: 4px 0;
  color: var(--color-warning-700);
  background: transparent;
  border: 0;
  cursor: pointer;
  font-size: 12px;

  &:disabled {
    opacity: 0.55;
    cursor: wait;
  }
}

.queued-request-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.queued-request-row {
  min-height: 28px;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 0 4px 0 6px;
  color: var(--color-text);
  border-radius: 8px;
  transition: background-color 0.18s ease;

  &:hover {
    background: var(--gray-50);
  }
}

.queued-request-icon {
  color: var(--gray-500);
}

.queued-request-content {
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 400;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queued-request-position {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--gray-500);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;

  &::before {
    content: '↪';
    color: var(--gray-400);
    font-size: 14px;
  }
}

.queued-request-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.queued-request-steer {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px;
  color: var(--gray-500);
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  transition:
    color 0.18s ease,
    background-color 0.18s ease;

  &:hover:not(:disabled) {
    color: var(--gray-700);
    background: var(--gray-100);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  &:disabled {
    opacity: 0.45;
    cursor: wait;
  }
}

.queued-request-delete {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: var(--gray-500);
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
  transition:
    color 0.18s ease,
    background-color 0.18s ease;

  &:hover:not(:disabled) {
    color: var(--color-error-700);
    background: var(--color-error-50);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  &:disabled {
    color: var(--gray-300);
    cursor: wait;
  }
}
</style>
