<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Download, Gauge, Play, ShieldAlert, Square } from 'lucide-vue-next'
import { message, Modal } from 'ant-design-vue'

import { companyDueDiligenceApi } from '@/apis/company_due_diligence_api'
import { useTaskPolling } from '@/composables/useTaskPolling'
import {
  REAL_DATABASE_CONFIRMATION,
  describeApiError,
  reportProgress,
  statusMeta,
  validatePerformanceForm
} from '@/utils/companyDueDiligence'
import PerformanceResultDetail from './PerformanceResultDetail.vue'

const scenarios = [
  'enterprise_profile',
  'shareholder_list',
  'financing_and_risk',
  'graph_path_3_hops',
  'report_aggregate_without_llm',
  'report_with_llm'
]
const form = reactive({
  scenarios: ['enterprise_profile'],
  safetyMode: 'safe_regression',
  concurrency: 1,
  requestCount: 5,
  durationSeconds: 30,
  requestTimeoutSeconds: 60,
  confirmation: ''
})
const runId = ref(null)
const runs = ref([])
const creating = ref(false)
const comparison = ref(null)
const globalSummary = ref(null)

const polling = useTaskPolling(companyDueDiligenceApi.getPerformanceRun, {
  isTerminal: (detail) => reportProgress(detail).terminal
})
const detail = computed(() => polling.detail.value)
const taskState = computed(() => reportProgress(detail.value))
const report = computed(() => detail.value?.report || null)
const status = computed(() => statusMeta(taskState.value.status))

const loadRuns = async () => {
  try {
    const result = await companyDueDiligenceApi.listPerformanceRuns(50)
    runs.value = result.runs || []
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const loadGlobalSummary = async () => {
  try {
    globalSummary.value = await companyDueDiligenceApi.getGlobalPerformanceSummary(1440)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const enqueue = async () => {
  try {
    creating.value = true
    const payload = validatePerformanceForm(form)
    const result = await companyDueDiligenceApi.createPerformanceRun(payload)
    runId.value = result.run_id
    await polling.start(runId.value)
    await loadRuns()
  } catch (error) {
    message.error(describeApiError(error))
  } finally {
    creating.value = false
  }
}

const createRun = () => {
  if (form.safetyMode !== 'controlled_real_database') return enqueue()
  Modal.confirm({
    title: '确认受控真实只读库性能运行',
    content: `该模式最多并发 10、最长 300 秒，必须已获得数据源负责人允许。确认口令：${REAL_DATABASE_CONFIRMATION}`,
    okText: '确认启动',
    okType: 'danger',
    onOk: enqueue
  })
}

const openRun = async (id) => {
  runId.value = id
  await polling.start(id)
}

const cancelRun = async () => {
  try {
    await companyDueDiligenceApi.cancelPerformanceRun(runId.value)
    message.success('已请求取消性能评测')
    await polling.start(runId.value)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const loadComparison = async (id) => {
  if (!id) return (comparison.value = null)
  try {
    comparison.value = (await companyDueDiligenceApi.getPerformanceRun(id)).report
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const download = async (format) => {
  try {
    const response = await companyDueDiligenceApi.downloadPerformanceRun(runId.value, format)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `performance-${runId.value}.${format === 'markdown' ? 'md' : 'json'}`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

onMounted(async () => {
  await Promise.all([loadRuns(), loadGlobalSummary()])
})
</script>

<template>
  <section class="performance-panel">
    <div class="performance-form">
      <header>
        <Gauge size="18" /><strong>性能评测</strong><span>全局目录自动选样，不输入企业名称</span>
      </header>
      <a-checkbox-group v-model:value="form.scenarios" class="scenario-selector">
        <a-checkbox v-for="scenario in scenarios" :key="scenario" :value="scenario">{{
          scenario
        }}</a-checkbox>
      </a-checkbox-group>
      <div class="parameter-row">
        <label
          >模式
          <a-select v-model:value="form.safetyMode" style="width: 210px">
            <a-select-option value="safe_regression">安全回归</a-select-option>
            <a-select-option value="controlled_real_database">受控真实只读库</a-select-option>
          </a-select>
        </label>
        <label>并发 <a-input-number v-model:value="form.concurrency" :min="1" :max="10" /></label>
        <label
          >请求数 <a-input-number v-model:value="form.requestCount" :min="1" :max="1000"
        /></label>
        <label
          >时长秒 <a-input-number v-model:value="form.durationSeconds" :min="1" :max="300"
        /></label>
        <label
          >超时秒 <a-input-number v-model:value="form.requestTimeoutSeconds" :min="1" :max="120"
        /></label>
      </div>
      <a-alert
        v-if="form.safetyMode === 'controlled_real_database'"
        type="warning"
        show-icon
        message="受控真实库模式不会进入默认 CI；启动前必须获得数据源负责人允许。"
      >
        <template #icon><ShieldAlert size="18" /></template>
      </a-alert>
      <a-input
        v-if="form.safetyMode === 'controlled_real_database'"
        v-model:value="form.confirmation"
        :placeholder="`输入确认口令：${REAL_DATABASE_CONFIRMATION}`"
      />
      <a-button type="primary" :loading="creating" @click="createRun"
        ><Play size="14" />启动性能评测</a-button
      >
    </div>

    <div v-if="globalSummary" class="global-summary">
      <span
        >P50 <strong>{{ globalSummary.latency?.p50_ms ?? '-' }} ms</strong></span
      >
      <span
        >P95 <strong>{{ globalSummary.latency?.p95_ms ?? '-' }} ms</strong></span
      >
      <span
        >P99 <strong>{{ globalSummary.latency?.p99_ms ?? '-' }} ms</strong></span
      >
      <span
        >吞吐
        <strong>{{ Number(globalSummary.throughput_rps || 0).toFixed(2) }} req/s</strong></span
      >
      <span
        >错误 <strong>{{ (Number(globalSummary.error_rate || 0) * 100).toFixed(2) }}%</strong></span
      >
      <span
        >超时
        <strong>{{ (Number(globalSummary.timeout_rate || 0) * 100).toFixed(2) }}%</strong></span
      >
    </div>

    <div v-if="runs.length" class="history-row">
      <span>历史运行</span>
      <button
        v-for="item in runs.slice(0, 8)"
        :key="item.id"
        type="button"
        @click="openRun(item.id)"
      >
        {{ item.id.slice(0, 8) }} · {{ statusMeta(item.status).label }}
      </button>
      <a-select allow-clear placeholder="对比运行" style="width: 180px" @change="loadComparison">
        <a-select-option v-for="item in runs" :key="item.id" :value="item.id">{{
          item.id.slice(0, 12)
        }}</a-select-option>
      </a-select>
    </div>

    <div v-if="runId" class="task-card">
      <header>
        <strong>{{ runId }}</strong
        ><a-tag :color="status.color">{{ status.label }}</a-tag>
      </header>
      <a-progress :percent="Math.round(taskState.progress)" />
      <p>{{ taskState.message }}</p>
      <div>
        <a-button v-if="!taskState.terminal" danger @click="cancelRun"
          ><Square size="13" />取消</a-button
        >
        <template v-if="report">
          <a-button @click="download('json')"><Download size="13" />JSON</a-button>
          <a-button @click="download('markdown')"><Download size="13" />Markdown</a-button>
        </template>
      </div>
    </div>
    <PerformanceResultDetail v-if="report" :report="report" :comparison="comparison" />
  </section>
</template>

<style scoped lang="less">
.performance-panel,
.performance-form {
  display: grid;
  gap: 13px;
}

.global-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;

  span {
    display: grid;
    gap: 3px;
    padding: 9px;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    color: var(--gray-600);
    font-size: 12px;
  }

  strong {
    color: var(--gray-1000);
  }
}

.performance-form {
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-50);

  header,
  .parameter-row,
  .history-row {
    display: flex;
    align-items: center;
    gap: 9px;
    flex-wrap: wrap;
  }

  header {
    color: var(--main-700);

    span {
      color: var(--gray-600);
      font-size: 12px;
    }
  }
}

.scenario-selector {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
}

.parameter-row label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--gray-700);
  font-size: 12px;
}

.history-row,
.task-card header,
.task-card > div {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.history-row {
  overflow-x: auto;

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

@media (max-width: 760px) {
  .scenario-selector {
    grid-template-columns: 1fr;
  }

  .global-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
