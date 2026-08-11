<script setup>
import { reactive } from 'vue'
import { FileText, Send } from 'lucide-vue-next'

import { FULL_REPORT_SHORTCUT, QUICK_QUESTIONS } from '@/utils/companyDueDiligence'

defineProps({
  disabled: { type: Boolean, default: true },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['submit'])

const form = reactive({
  message: QUICK_QUESTIONS[0],
  startDate: '',
  asOf: '',
  hopLimit: 3,
  topK: 10,
  includeLowConfidence: false
})

const submit = () => emit('submit', { ...form })
const createFullReport = () =>
  emit('submit', { ...form, message: '', shortcut: FULL_REPORT_SHORTCUT })
</script>

<template>
  <section class="question-panel">
    <div class="quick-questions">
      <button
        v-for="question in QUICK_QUESTIONS"
        :key="question"
        type="button"
        :disabled="disabled"
        @click="form.message = question"
      >
        {{ question.replace('是什么？', '').replace('有哪些？', '').replace('情况如何？', '') }}
      </button>
    </div>

    <a-textarea
      v-model:value="form.message"
      :disabled="disabled"
      :auto-size="{ minRows: 4, maxRows: 9 }"
      placeholder="例如：请分析这家企业近三年的关系和风险；或生成完整尽调报告"
      @press-enter.ctrl="submit"
    />

    <div class="advanced-row">
      <label>开始日期 <input v-model="form.startDate" type="date" :disabled="disabled" /></label>
      <label>截止日期 <input v-model="form.asOf" type="date" :disabled="disabled" /></label>
      <label
        >最多跳数
        <a-input-number v-model:value="form.hopLimit" :min="1" :max="3" :disabled="disabled"
      /></label>
      <label
        >结果上限 <a-input-number v-model:value="form.topK" :min="1" :max="50" :disabled="disabled"
      /></label>
      <a-checkbox v-model:checked="form.includeLowConfidence" :disabled="disabled"
        >包含低置信度线索</a-checkbox
      >
    </div>

    <div class="action-row">
      <span>系统自动理解主题、时间、展示形式和交付方式。</span>
      <a-button :disabled="disabled" :loading="loading" @click="createFullReport">
        <template #icon><FileText size="15" /></template>
        生成完整尽调报告
      </a-button>
      <a-button type="primary" :loading="loading" :disabled="disabled" @click="submit">
        <template #icon><Send size="15" /></template>
        开始调查
      </a-button>
    </div>
  </section>
</template>

<style scoped lang="less">
.question-panel {
  display: grid;
  gap: 14px;
}

.quick-questions {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;

  button {
    padding: 5px 9px;
    border: 1px solid var(--gray-200);
    border-radius: 999px;
    background: var(--gray-0);
    color: var(--gray-700);
    font-size: 12px;
    cursor: pointer;

    &:hover:not(:disabled) {
      border-color: var(--main-200);
      color: var(--main-700);
      background: var(--main-5);
    }
  }
}

.advanced-row,
.action-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.advanced-row {
  label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--gray-700);
    font-size: 12px;
  }

  input[type='date'] {
    min-height: 30px;
    border: 1px solid var(--gray-200);
    border-radius: 6px;
    color: var(--gray-800);
  }
}

.action-row {
  justify-content: flex-end;

  span {
    margin-right: auto;
    color: var(--gray-500);
    font-size: 12px;
  }
}

@media (max-width: 760px) {
  .action-row {
    align-items: stretch;
    flex-direction: column;

    span {
      margin-right: 0;
    }
  }
}
</style>
