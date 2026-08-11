<script setup>
import { computed, onMounted, ref } from 'vue'
import { Download, Play, Square } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

import { companyDueDiligenceApi } from '@/apis/company_due_diligence_api'
import { useTaskPolling } from '@/composables/useTaskPolling'
import { describeApiError, reportProgress, statusMeta } from '@/utils/companyDueDiligence'
import QualityResultDetail from './QualityResultDetail.vue'

const props = defineProps({ dataset: { type: Object, default: null } })

const maxSamples = ref(null)
const strategyLabel = ref('default')
const runId = ref(null)
const runs = ref([])
const creating = ref(false)
const comparison = ref(null)

const polling = useTaskPolling(companyDueDiligenceApi.getQualityRun, {
  isTerminal: (detail) => reportProgress(detail).terminal
})
const detail = computed(() => polling.detail.value)
const taskState = computed(() => reportProgress(detail.value))
const report = computed(() => detail.value?.report || null)
const status = computed(() => statusMeta(taskState.value.status))

const loadRuns = async () => {
  try {
    const result = await companyDueDiligenceApi.listQualityRuns(50)
    runs.value = result.runs || []
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const createRun = async () => {
  if (!props.dataset?.dataset_id) return message.warning('请先选择数据集')
  creating.value = true
  try {
    const result = await companyDueDiligenceApi.createQualityRun({
      dataset_id: props.dataset.dataset_id,
      max_samples: maxSamples.value || null,
      strategy_label: strategyLabel.value.trim() || 'default'
    })
    runId.value = result.run_id
    await polling.start(runId.value)
    await loadRuns()
  } catch (error) {
    message.error(describeApiError(error))
  } finally {
    creating.value = false
  }
}

const openRun = async (id) => {
  runId.value = id
  await polling.start(id)
}

const cancelRun = async () => {
  try {
    await companyDueDiligenceApi.cancelQualityRun(runId.value)
    message.success('已请求取消质量评测')
    await polling.start(runId.value)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const loadComparison = async (id) => {
  if (!id) return (comparison.value = null)
  try {
    comparison.value = (await companyDueDiligenceApi.getQualityRun(id)).report
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const download = async (format) => {
  try {
    const response = await companyDueDiligenceApi.downloadQualityRun(runId.value, format)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `quality-${runId.value}.${format === 'markdown' ? 'md' : format}`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

onMounted(loadRuns)
</script>

<template>
  <section class="run-panel">
    <div class="run-form">
      <div>
        <strong>质量评测</strong>
        <span>{{ dataset?.name || '未选择数据集' }}</span>
      </div>
      <label>策略标签 <a-input v-model:value="strategyLabel" style="width: 150px" /></label>
      <label>样本上限 <a-input-number v-model:value="maxSamples" :min="1" :max="10000" placeholder="全部" /></label>
      <a-button type="primary" :disabled="!dataset" :loading="creating" @click="createRun"><Play size="14" />运行</a-button>
    </div>

    <div v-if="runs.length" class="history-row">
      <span>历史运行</span>
      <button v-for="item in runs.slice(0, 8)" :key="item.id" type="button" @click="openRun(item.id)">
        {{ item.id.slice(0, 8) }} · {{ statusMeta(item.status).label }}
      </button>
      <a-select allow-clear placeholder="对比运行" style="width: 180px" @change="loadComparison">
        <a-select-option v-for="item in runs" :key="item.id" :value="item.id">{{ item.id.slice(0, 12) }}</a-select-option>
      </a-select>
    </div>

    <div v-if="runId" class="task-card">
      <header><strong>{{ runId }}</strong><a-tag :color="status.color">{{ status.label }}</a-tag></header>
      <a-progress :percent="Math.round(taskState.progress)" />
      <p>{{ taskState.message }}</p>
      <div>
        <a-button v-if="!taskState.terminal" danger @click="cancelRun"><Square size="13" />取消</a-button>
        <template v-if="report">
          <a-button @click="download('json')"><Download size="13" />JSON</a-button>
          <a-button @click="download('jsonl')"><Download size="13" />JSONL</a-button>
          <a-button @click="download('markdown')"><Download size="13" />Markdown</a-button>
        </template>
      </div>
    </div>

    <a-alert
      v-if="comparison && report"
      type="info"
      show-icon
      :message="`策略对比：${report.strategy_label} vs ${comparison.strategy_label}`"
      :description="`严格通过率 ${(report.strict_metrics.strict_pass_rate.rate || 0) * 100}% vs ${(comparison.strict_metrics.strict_pass_rate.rate || 0) * 100}%`"
    />
    <QualityResultDetail v-if="report" :report="report" />
  </section>
</template>

<style scoped lang="less">
.run-panel {
  display: grid;
  gap: 14px;
}

.run-form,
.history-row,
.task-card header,
.task-card > div {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.run-form {
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-50);

  > div {
    display: grid;
    margin-right: auto;
  }

  span,
  label {
    color: var(--gray-600);
    font-size: 12px;
  }
}

.history-row {
  overflow-x: auto;

  > span {
    flex: 0 0 auto;
    color: var(--gray-600);
  }

  button {
    flex: 0 0 auto;
    padding: 5px 8px;
    border: 1px solid var(--gray-200);
    border-radius: 7px;
    background: var(--gray-0);
    cursor: pointer;
  }
}

.task-card {
  padding: 12px;
  border: 1px solid var(--main-100);
  border-radius: 10px;
  background: var(--main-5);

  header {
    justify-content: space-between;
  }

  p {
    color: var(--gray-600);
  }

  > div {
    justify-content: flex-end;
  }
}
</style>
