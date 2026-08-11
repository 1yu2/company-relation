<script setup>
import { reactive, watch } from 'vue'
import { Network, SearchCheck, Send } from 'lucide-vue-next'

import { QUICK_QUESTIONS } from '@/utils/companyDueDiligence'

defineProps({
  disabled: { type: Boolean, default: true },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['submit'])

const form = reactive({
  mode: 'qa',
  question: QUICK_QUESTIONS[0],
  asOf: '',
  hopLimit: 3,
  topK: 10,
  includeLowConfidence: false
})

watch(
  () => form.mode,
  (mode) => {
    if (mode === 'graph') form.question = '该企业有哪些最多三跳的关联路径？'
    if (mode === 'evidence') form.question = '该企业有哪些处罚、许可、专利、产品或新闻证据？'
  }
)

const submit = () => emit('submit', { ...form })
</script>

<template>
  <section class="question-panel">
    <div class="mode-row">
      <a-radio-group v-model:value="form.mode" button-style="solid" :disabled="disabled">
        <a-radio-button value="qa">简单问答</a-radio-button>
        <a-radio-button value="graph"><Network size="14" /> 多跳关系</a-radio-button>
        <a-radio-button value="evidence"><SearchCheck size="14" /> 证据检索</a-radio-button>
      </a-radio-group>
    </div>

    <div class="quick-questions">
      <button
        v-for="question in QUICK_QUESTIONS"
        :key="question"
        type="button"
        :disabled="disabled"
        @click="form.question = question"
      >
        {{ question.replace('是什么？', '').replace('有哪些？', '') }}
      </button>
    </div>

    <a-textarea
      v-model:value="form.question"
      :disabled="disabled"
      :auto-size="{ minRows: 3, maxRows: 7 }"
      placeholder="输入需要核查的企业问题"
    />

    <div class="advanced-row">
      <label>业务时点 <input v-model="form.asOf" type="date" :disabled="disabled" /></label>
      <label>最多跳数 <a-input-number v-model:value="form.hopLimit" :min="1" :max="3" :disabled="disabled" /></label>
      <label>证据条数 <a-input-number v-model:value="form.topK" :min="1" :max="50" :disabled="disabled" /></label>
      <a-checkbox v-model:checked="form.includeLowConfidence" :disabled="disabled">显示低置信度线索</a-checkbox>
      <a-button type="primary" :loading="loading" :disabled="disabled" @click="submit">
        <template #icon><Send size="15" /></template>
        开始查询
      </a-button>
    </div>
  </section>
</template>

<style scoped lang="less">
.question-panel {
  display: grid;
  gap: 14px;
}

.mode-row :deep(.ant-radio-button-wrapper) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
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

.advanced-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;

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

  :deep(.ant-btn) {
    margin-left: auto;
  }
}

@media (max-width: 760px) {
  .advanced-row :deep(.ant-btn) {
    width: 100%;
    margin-left: 0;
  }
}
</style>
