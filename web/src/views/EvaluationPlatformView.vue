<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Clock3,
  Database,
  FlaskConical,
  ListChecks,
  RefreshCw,
  Route,
  ShieldCheck
} from 'lucide-vue-next'

import PageHeader from '@/components/shared/PageHeader.vue'
import { evaluationApi } from '@/apis/evaluation_api'

const pipelineSteps = [
  { title: '输入', description: 'Query + 参考答案', icon: ClipboardCheck },
  { title: '执行', description: 'Agent Harness', icon: FlaskConical },
  { title: '轨迹', description: '状态与工具事件', icon: Route },
  { title: '评估', description: '确定性指标', icon: ShieldCheck },
  { title: '聚合', description: '全局结果与失败', icon: ListChecks }
]

const fallbackDatasets = [
  {
    name: 'Agent 基础问答 Fixture',
    version: 'v0.1',
    tasks: 10,
    source: '前端可重复样例',
    status: '已发布'
  },
  {
    name: 'Tool / MCP 契约样例',
    version: 'v0.1',
    tasks: 0,
    source: '待接入平台 API',
    status: '草稿'
  }
]

const fallbackExperiments = [
  {
    id: 'fixture-baseline',
    name: 'Fixture Baseline · 确定性指标',
    agent: '通用 Agent（fixture）',
    dataset: 'Agent 基础问答 Fixture v0.1',
    status: '已完成',
    passRate: '80%',
    passed: '8 / 10',
    latency: '—',
    createdAt: '本地示例'
  }
]

const fallbackFixtureResults = [
  {
    id: 'qa-001',
    query: '评测平台的最小输入和输出是什么？',
    expected: '输入是 query 与参考答案，输出包含 Agent 最终答案、轨迹摘要和指标结果。',
    actual: '输入是 query 与参考答案，输出包含最终答案、轨迹摘要和指标结果。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-002',
    query: '为什么评测运行不能出现在普通聊天历史？',
    expected: '评测线程使用 internal 可见性，只通过评测平台查询，不能污染普通聊天。',
    actual: '评测线程使用 internal 可见性，并从普通聊天列表中隐藏。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-003',
    query: 'P95 延迟在没有真实 Agent 调用时应该如何展示？',
    expected: '没有真实调用数据时返回 null 或短横线，不能使用固定值冒充实测结果。',
    actual: '当前页面展示短横线，标记为未执行真实调用。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-004',
    query: 'Tool 路径指标主要检查什么？',
    expected: '检查工具选择、调用顺序及参数契约是否符合预期。',
    actual: '检查工具选择和调用顺序。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '通过', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-005',
    query: '当前平台是否执行真实模型调用？',
    expected: '否，当前阶段只使用 fake / fixture 验证控制面和指标语义。',
    actual: '是，页面会自动调用当前默认模型。',
    status: '失败',
    failure: '答案与当前阶段约束相反，属于 fixture 中的预置失败样例。',
    metrics: { answerMatch: '失败', toolPath: '不适用', trace: '通过', task: '失败' }
  },
  {
    id: 'qa-006',
    query: '确定性指标和 LLM Judge 是否应该混为一个分数？',
    expected: '不应该。两者需要独立记录，LLM Judge 不能覆盖确定性指标。',
    actual: '不应该，确定性指标和 LLM Judge 应独立记录。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-007',
    query: '评测平台的事实结果应该保存在哪里？',
    expected: 'PostgreSQL 保存可长期查询的评测事实，Redis 只负责队列和短期事件。',
    actual: 'PostgreSQL 保存评测事实，Redis 保存队列和事件。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-008',
    query: '结果页为什么要保留每条 rollout，而不是只保存总分？',
    expected: '保留单条 rollout 才能追溯输入、输出、轨迹、失败分类和分位延迟。',
    actual: '为了保留单题结果，便于追溯失败原因。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  },
  {
    id: 'qa-009',
    query: '当前页面的数据来源是什么？',
    expected: '当前数据来自前端 fixture，仅用于展示和回归 UI，不代表生产评测结果。',
    actual: '当前数据来自生产 PostgreSQL 中的真实实验记录。',
    status: '失败',
    failure: '数据来源描述错误，页面顶部已明确标记 Fixture 模式。',
    metrics: { answerMatch: '失败', toolPath: '不适用', trace: '通过', task: '失败' }
  },
  {
    id: 'qa-010',
    query: '企业尽调是否应该拥有一套独立评测页面？',
    expected: '不应该。企业尽调作为普通 agent 使用统一平台能力，不复制专属评测页面。',
    actual: '不应该，企业尽调可以复用统一平台的数据集和 Evaluator。',
    status: '通过',
    metrics: { answerMatch: '通过', toolPath: '不适用', trace: '通过', task: '通过' }
  }
]

const fallbackMetricDefinitions = [
  {
    name: 'answer_match',
    label: '答案匹配',
    description: '对最终答案做归一化后，与参考答案进行确定性匹配。'
  },
  {
    name: 'task_success',
    label: '任务完成',
    description: '检查是否到达题目预期的终态，不只判断文本是否相似。'
  },
  {
    name: 'tool_path',
    label: '工具路径',
    description: '检查工具选择、调用顺序和参数契约；不需要工具的题目记为不适用。'
  },
  {
    name: 'trace_completeness',
    label: '轨迹完整',
    description: '检查输入、执行、工具事件、输出和终态等可观察事件是否齐全。'
  }
]

const datasets = ref(fallbackDatasets)
const experiments = ref(fallbackExperiments)
const fixtureResults = ref(fallbackFixtureResults)
const metricDefinitions = ref(fallbackMetricDefinitions)
const selectedExperimentId = ref('fixture-baseline')
const sourceFilter = ref('')
const traceByRollout = ref(new Map())
const traceLoading = ref(new Set())
const experimentAggregate = ref({})
const lastRefreshed = ref('刚刚')
const isLoading = ref(false)
const loadError = ref('')
const usingApiData = ref(false)
const selectedExperiment = computed(
  () =>
    experiments.value.find((experiment) => experiment.id === selectedExperimentId.value) ||
    experiments.value[0] || {
      id: '',
      name: '暂无实验',
      agent: '—',
      dataset: '—',
      status: '未测量',
      passRate: '—',
      passed: '—',
      latency: '—',
      createdAt: '—'
    }
)

const overviewCards = computed(() => {
  const aggregate = experimentAggregate.value || {}
  const taskCount = selectedExperiment.value.taskCount ?? fixtureResults.value.length
  const passedCount = fixtureResults.value.filter((item) => item.status === '通过').length
  const passAtOne = aggregate['pass@1']
  return [
    { label: '数据集', value: String(datasets.value.length), helper: sourceFilter.value || '全部来源', icon: Database, tone: 'violet' },
    { label: '评测题目', value: String(taskCount), helper: `${passedCount} 条已通过`, icon: ListChecks, tone: 'blue' },
    { label: '实验批次', value: String(experiments.value.length), helper: '平台统一实验', icon: FlaskConical, tone: 'orange' },
    { label: 'pass@1', value: typeof passAtOne === 'number' ? `${Math.round(passAtOne * 100)}%` : '—', helper: aggregate.metric_version || '未测量', icon: CheckCircle2, tone: 'green' },
    { label: 'P95 延迟', value: '—', helper: 'Fixture 未测量', icon: Clock3, tone: 'gray' }
  ]
})

const refreshTimestamp = () => {
  lastRefreshed.value = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date())
}

const unwrapItems = (response) => {
  const payload = response?.data
  if (Array.isArray(payload)) return { items: payload, nextCursor: null }
  return { items: payload?.items || [], nextCursor: payload?.next_cursor || null }
}

const refreshFixture = async () => {
  isLoading.value = true
  loadError.value = ''
  try {
    const [datasetResponse, experimentResponse] = await Promise.all([
      evaluationApi.listDatasets(sourceFilter.value),
      evaluationApi.listExperiments(sourceFilter.value)
    ])
    const datasetItems = unwrapItems(datasetResponse).items
    const experimentItems = unwrapItems(experimentResponse).items
    if (!Array.isArray(datasetItems) || !Array.isArray(experimentItems)) {
      throw new Error('评测 API 返回格式不正确')
    }
    datasets.value = datasetItems.map((dataset) => ({
      name: dataset.name,
      version: dataset.latest_version || dataset.source_kind || '未指定来源',
      tasks: dataset.item_count || dataset.task_count || 0,
      source: dataset.source_kind || '平台评测来源',
      status: dataset.status || '可用',
      checksum: dataset.checksum
    }))
    experiments.value = experimentItems.map((experiment) => ({
      id: experiment.experiment_id,
      name: experiment.experiment_id,
      agent: experiment.agent_slug || experiment.source_kind || '平台评测来源',
      dataset: experiment.dataset_version_id,
      status: experiment.status || '未测量',
      passRate: '—',
      passed: '待加载',
      latency: '—',
      createdAt: experiment.created_at,
      sourceKind: experiment.source_kind,
      taskCount: 0
    }))
    usingApiData.value = true
    if (experiments.value.length > 0) {
      selectedExperimentId.value = experiments.value[0].id
      await loadExperimentDetails(experiments.value[0].id)
    } else {
      selectedExperimentId.value = ''
      fixtureResults.value = []
    }
    refreshTimestamp()
  } catch (error) {
    loadError.value = error?.message || '评测数据加载失败'
    usingApiData.value = false
  } finally {
    isLoading.value = false
  }
}

const loadExperimentDetails = async (experimentId) => {
  if (!usingApiData.value || !experimentId) return
  const response = await evaluationApi.getExperiment(experimentId)
  const detail = response?.data
  if (!detail) return
  const aggregate = detail.aggregate || {}
  experimentAggregate.value = aggregate
  let taskById = new Map()
  try {
    const versionResponse = await evaluationApi.getVersion(detail.dataset_version_id)
    taskById = new Map((versionResponse?.data?.tasks || []).map((task) => [task.task_id, task]))
  } catch {
    taskById = new Map()
  }
  const metricByRollout = new Map()
  for (const metric of detail.metrics || []) {
    const current = metricByRollout.get(metric.rollout_id) || {}
    current[metric.metric_name] = metric.status
    metricByRollout.set(metric.rollout_id, current)
  }
  const target = experiments.value.find((experiment) => experiment.id === experimentId)
  if (target) {
    const passRate = aggregate['pass@1']
    target.passRate = typeof passRate === 'number' ? `${Math.round(passRate * 100)}%` : '—'
    target.passed = `${detail.rollouts?.filter((rollout) => metricByRollout.get(rollout.rollout_id)?.task_pass === 'passed').length || 0} / ${detail.rollouts?.length || 0}`
    target.taskCount = detail.rollouts?.length || 0
  }
  const failuresByRollout = new Map()
  for (const failure of detail.failures || []) failuresByRollout.set(failure.rollout_id, failure.message || failure.reason_code)
  metricDefinitions.value = [...new Set((detail.metrics || []).map((item) => item.metric_name))].map((name) => ({
    name,
    label: name,
    description: `后端返回的 ${name} 确定性结果。`
  }))
  fixtureResults.value = (detail.rollouts || []).map((rollout, index) => ({
    id: rollout.rollout_id,
    query: taskById.get(rollout.task_id)?.input?.query || rollout.task_id,
    expected: taskById.has(rollout.task_id) ? JSON.stringify(taskById.get(rollout.task_id).expected || {}) : '由数据集版本提供',
    actual: rollout.actual_output ? JSON.stringify(rollout.actual_output) : '未产生输出',
    status: metricByRollout.get(rollout.rollout_id)?.task_pass === 'passed' ? '通过' : rollout.status === 'RUNNING' ? '运行中' : rollout.status === 'CANCELLED' ? '已取消' : '失败',
    failure: failuresByRollout.get(rollout.rollout_id),
    metrics: metricByRollout.get(rollout.rollout_id) || { task_pass: '未测量' },
    index,
    rolloutStatus: rollout.status,
    attempt: rollout.attempt,
    duration: rollout.duration_ms
  }))
}

const loadTrace = async (rolloutId) => {
  if (!usingApiData.value || !rolloutId || traceByRollout.value.has(rolloutId)) return
  traceLoading.value = new Set(traceLoading.value).add(rolloutId)
  try {
    const response = await evaluationApi.getTrace(rolloutId)
    const next = new Map(traceByRollout.value)
    next.set(rolloutId, response?.data || { events: [] })
    traceByRollout.value = next
  } catch (error) {
    const next = new Map(traceByRollout.value)
    next.set(rolloutId, { events: [], error: error?.message || 'Trace 加载失败' })
    traceByRollout.value = next
  } finally {
    const next = new Set(traceLoading.value)
    next.delete(rolloutId)
    traceLoading.value = next
  }
}

const onResultToggle = (result, event) => {
  if (event.target.open) loadTrace(result.id)
}

const formatAggregate = (name) => {
  const value = experimentAggregate.value?.[name]
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '—'
}

const selectExperiment = async (experimentId) => {
  selectedExperimentId.value = experimentId
  await loadExperimentDetails(experimentId)
}

onMounted(refreshFixture)

const statusClass = (status) => {
  if (['通过', '已发布', 'FINISHED', 'COMPLETED', 'passed'].includes(status)) return 'success'
  if (['运行中', 'RUNNING', 'QUEUED', 'DRAFT'].includes(status)) return 'info'
  return 'warning'
}
</script>

<template>
  <div class="evaluation-platform-view">
    <PageHeader title="平台评测" :show-border="true">
      <template #info>
        <div class="header-meta">
          <span class="mode-pill"><FlaskConical :size="14" /> {{ usingApiData ? '平台数据' : 'Fixture 回退' }}</span>
          <span class="refresh-time">更新于 {{ lastRefreshed }}</span>
        </div>
      </template>
      <template #actions>
        <button type="button" class="secondary-button" :disabled="isLoading" @click="refreshFixture">
          <RefreshCw :size="15" />
          {{ isLoading ? '加载中' : '刷新数据' }}
        </button>
      </template>
    </PageHeader>

    <main class="evaluation-content">
      <section class="mode-banner" aria-label="评测运行模式">
        <div class="mode-banner-icon"><FlaskConical :size="22" /></div>
        <div class="mode-banner-copy">
            <strong>{{ usingApiData ? '当前展示平台评测数据' : '当前为 Fixture 回退模式' }}</strong>
            <p>
            {{ usingApiData ? '数据来自平台评测 API，按来源、实验、rollout、Trace 和指标逐级查看。' : '平台 API 暂不可用，页面保留本地可重复样例；不会调用真实模型或创建用户可见聊天。' }}
            </p>
          </div>
        <span class="mode-banner-tag">{{ loadError ? '接口异常' : usingApiData ? '可追溯' : '可安全回归 UI' }}</span>
      </section>

      <section class="source-filter" aria-label="评测来源筛选">
        <span class="filter-label">来源</span>
        <div class="filter-segmented" role="group" aria-label="来源类型">
          <button type="button" :class="{ active: sourceFilter === '' }" @click="sourceFilter = ''; refreshFixture()">全部</button>
          <button type="button" :class="{ active: sourceFilter === 'agent' }" @click="sourceFilter = 'agent'; refreshFixture()">Agent / Tool / MCP</button>
          <button type="button" :class="{ active: sourceFilter === 'rag' }" @click="sourceFilter = 'rag'; refreshFixture()">RAG</button>
          <button type="button" :class="{ active: sourceFilter === 'graph' }" @click="sourceFilter = 'graph'; refreshFixture()">Graph</button>
        </div>
      </section>

      <p v-if="loadError" class="load-error" role="alert">{{ loadError }}</p>

      <section class="section-card pipeline-card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">统一评测链路</p>
            <h2>从输入到全局结果</h2>
          </div>
          <span class="section-note">来源：{{ sourceFilter || '全部' }}</span>
        </div>
        <div class="pipeline-steps">
          <template v-for="(step, index) in pipelineSteps" :key="step.title">
            <div class="pipeline-step">
              <span class="pipeline-icon"><component :is="step.icon" :size="18" /></span>
              <div>
                <strong>{{ step.title }}</strong>
                <span>{{ step.description }}</span>
              </div>
            </div>
            <ChevronRight v-if="index < pipelineSteps.length - 1" class="pipeline-arrow" :size="16" />
          </template>
        </div>
      </section>

      <section class="overview-grid" aria-label="评测概览">
        <article v-for="card in overviewCards" :key="card.label" class="overview-card">
          <div class="overview-card-top">
            <span class="overview-icon" :class="`overview-icon--${card.tone}`">
              <component :is="card.icon" :size="18" />
            </span>
            <span class="overview-label">{{ card.label }}</span>
          </div>
          <strong class="overview-value">{{ card.value }}</strong>
          <span class="overview-helper">{{ card.helper }}</span>
        </article>
      </section>

      <div class="content-grid">
        <section class="section-card">
          <div class="section-heading section-heading--compact">
            <div>
              <p class="eyebrow">Datasets</p>
              <h2>数据集版本</h2>
            </div>
            <span class="count-label">{{ datasets.length }} 个</span>
          </div>
          <div class="dataset-list">
            <p v-if="!datasets.length" class="empty-state">暂无符合条件的数据集</p>
            <div v-for="dataset in datasets" :key="`${dataset.name}-${dataset.version}`" class="dataset-row">
              <div class="row-leading-icon"><Database :size="17" /></div>
              <div class="row-main">
                <strong>{{ dataset.name }}</strong>
                <span>{{ dataset.version }} · {{ dataset.source }}</span>
                <code v-if="dataset.checksum" class="checksum">{{ dataset.checksum }}</code>
              </div>
              <div class="row-trailing">
                <span>{{ dataset.tasks }} 题</span>
                <span class="status-tag" :class="statusClass(dataset.status)">{{ dataset.status }}</span>
              </div>
            </div>
          </div>
        </section>

        <section class="section-card">
          <div class="section-heading section-heading--compact">
            <div>
              <p class="eyebrow">Experiments</p>
              <h2>实验批次</h2>
            </div>
            <span class="count-label">{{ experiments.length }} 个</span>
          </div>
          <div class="experiment-list">
            <p v-if="!experiments.length" class="empty-state">暂无符合条件的实验</p>
            <button
              v-for="experiment in experiments"
              :key="experiment.id"
              type="button"
              class="experiment-row"
              :class="{ selected: selectedExperimentId === experiment.id }"
              @click="selectExperiment(experiment.id)"
            >
              <div class="row-main">
                <strong>{{ experiment.name }}</strong>
                <span>{{ experiment.agent }} · {{ experiment.dataset }}</span>
              </div>
              <div class="experiment-result">
                <strong>{{ experiment.passRate }}</strong>
                <span>{{ experiment.passed }}</span>
              </div>
              <ChevronRight :size="17" />
            </button>
          </div>
        </section>
      </div>

      <section class="section-card results-card">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Rollouts</p>
            <h2>{{ selectedExperiment.name }}</h2>
            <span class="section-subtitle">{{ selectedExperiment.agent }} · {{ selectedExperiment.dataset }}</span>
          </div>
          <div class="result-summary">
            <span class="status-tag" :class="statusClass(selectedExperiment.status)">{{ selectedExperiment.status }}</span>
            <strong>{{ selectedExperiment.passRate }}</strong>
            <span>{{ selectedExperiment.passed }} 通过</span>
            <span class="aggregate-chip">pass@k {{ formatAggregate('pass@k') }}</span>
            <span class="aggregate-chip">pass^k {{ formatAggregate('pass^k') }}</span>
            <span class="aggregate-chip">best@k {{ formatAggregate('best@k') }}</span>
          </div>
        </div>

        <div class="result-hint">
          <CircleAlert :size="16" />
          单题输入、标准答案、模拟输出和指标默认折叠，点击题目展开查看。
        </div>

        <div class="results-list">
          <p v-if="!fixtureResults.length" class="empty-state">当前实验暂无 rollout 结果</p>
          <details v-for="(result, index) in fixtureResults" :key="result.id" class="result-item" :open="index === 0" @toggle="onResultToggle(result, $event)">
            <summary>
              <span class="result-number">{{ String(index + 1).padStart(2, '0') }}</span>
              <span class="result-query">{{ result.query }}</span>
              <span class="status-tag" :class="statusClass(result.status)">{{ result.status }}</span>
            </summary>
            <div class="result-detail">
              <div class="answer-grid">
                <div>
                  <span class="detail-label">Query</span>
                  <p>{{ result.query }}</p>
                </div>
                <div>
                  <span class="detail-label">标准答案</span>
                  <p>{{ result.expected }}</p>
                </div>
                <div>
                  <span class="detail-label">模拟输出</span>
                  <p>{{ result.actual }}</p>
                </div>
              </div>
              <div class="metric-row">
                <span v-for="(metricStatus, metricName) in result.metrics" :key="metricName" class="metric-chip">
                  <strong>{{ metricName }}</strong>
                  <span :class="statusClass(metricStatus)">{{ metricStatus }}</span>
                </span>
              </div>
              <div v-if="result.failure" class="failure-note">
                <CircleAlert :size="15" />
                <span>{{ result.failure }}</span>
              </div>
              <div v-if="usingApiData" class="trace-panel">
                <div class="detail-label">Trace</div>
                <span v-if="traceLoading.has(result.id)" class="trace-muted">加载中...</span>
                <span v-else-if="traceByRollout.get(result.id)?.error" class="trace-error">{{ traceByRollout.get(result.id).error }}</span>
                <span v-else-if="!traceByRollout.get(result.id)" class="trace-muted">展开题目后加载脱敏事件</span>
                <span v-else-if="!traceByRollout.get(result.id).events?.length" class="trace-muted">无可用 Trace 事件</span>
                <ol v-else class="trace-events">
                  <li v-for="event in traceByRollout.get(result.id).events" :key="event.event_id">
                    <code>#{{ event.sequence }}</code>
                    <span>{{ event.component }}.{{ event.operation }}</span>
                    <small v-if="event.reason_code">{{ event.reason_code }}</small>
                  </li>
                </ol>
              </div>
            </div>
          </details>
        </div>
      </section>

      <section class="section-card metric-card">
        <div class="section-heading section-heading--compact">
          <div>
            <p class="eyebrow">Evaluator Registry</p>
            <h2>指标定义</h2>
          </div>
          <span class="section-note">首版只展示确定性指标</span>
        </div>
        <div class="metric-definition-grid">
          <article v-for="metric in metricDefinitions" :key="metric.name" class="metric-definition">
            <code>{{ metric.name }}</code>
            <strong>{{ metric.label }}</strong>
            <p>{{ metric.description }}</p>
          </article>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped lang="less">
.evaluation-platform-view {
  min-height: 100%;
  background: var(--gray-25);
  color: var(--gray-1000);
}

.evaluation-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: var(--page-padding);
}

.header-meta,
.mode-pill,
.secondary-button,
.result-summary,
.metric-chip,
.mode-banner-tag,
.aggregate-chip {
  display: inline-flex;
  align-items: center;
}

.aggregate-chip {
  gap: 4px;
  padding: 4px 7px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  color: var(--gray-600);
  font-size: 11px;
  white-space: nowrap;
}

.header-meta {
  gap: 10px;
  color: var(--gray-600);
  font-size: 12px;
}

.mode-pill {
  gap: 5px;
  padding: 5px 9px;
  border: 1px solid var(--main-200);
  border-radius: 999px;
  background: var(--main-50);
  color: var(--main-800);
  font-weight: 600;
}

.refresh-time {
  white-space: nowrap;
}

.secondary-button {
  gap: 6px;
  height: 32px;
  padding: 0 11px;
  border: 1px solid var(--gray-200);
  border-radius: 7px;
  background: var(--gray-0);
  color: var(--gray-800);
  font-size: 13px;
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    color 0.2s ease,
    background-color 0.2s ease;

  &:hover {
    border-color: var(--main-300);
    background: var(--main-10);
    color: var(--main-800);
  }

  &:disabled {
    cursor: wait;
    opacity: 0.6;
  }
}

.load-error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--color-warning-200);
  border-radius: 7px;
  background: var(--color-warning-10);
  color: var(--color-warning-800);
  font-size: 13px;
}

.source-filter {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.filter-label {
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 650;
}

.filter-segmented {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;

  button {
    min-height: 28px;
    padding: 0 9px;
    border: 1px solid transparent;
    border-radius: 6px;
    background: transparent;
    color: var(--gray-600);
    font-size: 12px;
    cursor: pointer;

    &:hover,
    &.active {
      border-color: var(--main-200);
      background: var(--main-50);
      color: var(--main-800);
    }
  }
}

.mode-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid var(--color-info-100);
  border-radius: 10px;
  background: var(--color-info-10);
}

.mode-banner-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 38px;
  width: 38px;
  height: 38px;
  border-radius: 9px;
  background: var(--color-info-50);
  color: var(--color-info-700);
}

.mode-banner-copy {
  min-width: 0;
  flex: 1;

  strong {
    display: block;
    color: var(--gray-900);
    font-size: 14px;
  }

  p {
    margin: 4px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.6;
  }
}

.mode-banner-tag {
  flex: 0 0 auto;
  padding: 5px 9px;
  border-radius: 6px;
  background: var(--gray-0);
  color: var(--color-info-700);
  font-size: 12px;
  font-weight: 600;
}

.section-card {
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-0);
  box-shadow: 0 1px 2px var(--shadow-1);
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;

  h2 {
    margin: 3px 0 0;
    color: var(--gray-1000);
    font-size: 17px;
    font-weight: 650;
  }
}

.section-heading--compact {
  margin-bottom: 13px;
}

.eyebrow {
  margin: 0;
  color: var(--main-700);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.section-note,
.section-subtitle,
.count-label {
  color: var(--gray-500);
  font-size: 12px;
}

.pipeline-card {
  padding-bottom: 16px;
}

.pipeline-steps {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.pipeline-step {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 132px;
  flex: 1;
}

.pipeline-icon,
.row-leading-icon,
.overview-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.pipeline-icon {
  display: grid;
  place-items: center;
  box-sizing: border-box;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--main-50);
  color: var(--main-700);
  line-height: 0;

  svg {
    display: block;
    width: 18px;
    height: 18px;
    margin: 0;
  }
}

.pipeline-step strong,
.pipeline-step div span {
  display: block;
}

.pipeline-step strong {
  color: var(--gray-800);
  font-size: 13px;
}

.pipeline-step div span {
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 11px;
  white-space: nowrap;
}

.pipeline-arrow {
  flex: 0 0 auto;
  color: var(--gray-300);
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.overview-card {
  min-width: 0;
  padding: 15px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
}

.overview-card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.overview-icon {
  width: 30px;
  height: 30px;
  border-radius: 8px;
}

.overview-icon--violet {
  background: var(--main-50);
  color: var(--main-700);
}

.overview-icon--blue {
  background: var(--color-info-50);
  color: var(--color-info-700);
}

.overview-icon--orange {
  background: var(--color-warning-50);
  color: var(--color-warning-700);
}

.overview-icon--green {
  background: var(--color-success-50);
  color: var(--color-success-700);
}

.overview-icon--gray {
  background: var(--gray-100);
  color: var(--gray-600);
}

.overview-label {
  overflow: hidden;
  color: var(--gray-600);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-value {
  display: block;
  margin-top: 14px;
  color: var(--gray-1000);
  font-size: 25px;
  font-weight: 650;
  line-height: 1;
}

.overview-helper {
  display: block;
  margin-top: 7px;
  overflow: hidden;
  color: var(--gray-500);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.dataset-list,
.experiment-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.empty-state {
  margin: 8px 0;
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

.dataset-row,
.experiment-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 11px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);
}

.experiment-row {
  width: 100%;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease;

  &:hover,
  &.selected {
    border-color: var(--main-300);
    background: var(--main-10);
  }

  & > svg {
    flex: 0 0 auto;
    color: var(--gray-400);
  }
}

.row-leading-icon {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: var(--gray-0);
  color: var(--main-700);
}

.row-main {
  min-width: 0;
  flex: 1;

  strong,
  span {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: var(--gray-800);
    font-size: 13px;
    font-weight: 600;
  }

  span {
    margin-top: 4px;
    color: var(--gray-500);
    font-size: 11px;
  }
}

.checksum {
  display: block;
  max-width: 100%;
  overflow: hidden;
  color: var(--gray-400);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-trailing {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  color: var(--gray-500);
  font-size: 11px;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  padding: 3px 7px;
  border-radius: 5px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  line-height: 1.3;
  white-space: nowrap;

  &.success {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.warning {
    background: var(--color-warning-50);
    color: var(--color-warning-700);
  }

  &.info {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }
}

.experiment-result {
  flex: 0 0 auto;
  text-align: right;

  strong,
  span {
    display: block;
  }

  strong {
    color: var(--color-success-700);
    font-size: 14px;
  }

  span {
    margin-top: 3px;
    color: var(--gray-500);
    font-size: 11px;
  }
}

.results-card {
  min-width: 0;
}

.result-summary {
  gap: 9px;
  flex: 0 0 auto;
  color: var(--gray-500);
  font-size: 12px;

  strong {
    color: var(--color-success-700);
    font-size: 18px;
  }
}

.result-hint,
.failure-note {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--gray-600);
  font-size: 12px;
}

.trace-panel {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--gray-150);
}

.trace-muted,
.trace-error {
  display: block;
  margin-top: 5px;
  color: var(--gray-500);
  font-size: 12px;
}

.trace-error {
  color: var(--color-warning-800);
}

.trace-events {
  display: grid;
  gap: 5px;
  margin: 7px 0 0;
  padding-left: 18px;
  color: var(--gray-700);
  font-size: 12px;

  li {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  code {
    color: var(--main-700);
  }

  small {
    color: var(--color-warning-800);
  }
}

.result-hint {
  margin-bottom: 10px;
  padding: 9px 10px;
  border-radius: 7px;
  background: var(--gray-10);
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.result-item {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);

  &[open] {
    border-color: var(--main-200);
  }

  summary {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 42px;
    padding: 0 11px;
    color: var(--gray-800);
    font-size: 13px;
    cursor: pointer;
    list-style: none;

    &::-webkit-details-marker {
      display: none;
    }

    &::before {
      content: '›';
      color: var(--gray-400);
      font-size: 18px;
      transition: transform 0.2s ease;
    }
  }

  &[open] summary::before {
    transform: rotate(90deg);
    color: var(--main-700);
  }
}

.result-number {
  flex: 0 0 auto;
  color: var(--gray-400);
  font-family: monospace;
  font-size: 11px;
}

.result-query {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-detail {
  padding: 0 12px 13px 42px;
}

.answer-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  padding-top: 3px;
}

.answer-grid > div {
  min-width: 0;
  padding: 10px;
  border-radius: 7px;
  background: var(--gray-10);
}

.detail-label {
  color: var(--gray-500);
  font-size: 11px;
  font-weight: 600;
}

.answer-grid p {
  margin: 6px 0 0;
  color: var(--gray-700);
  font-size: 12px;
  line-height: 1.55;
}

.metric-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 10px;
}

.metric-chip {
  gap: 7px;
  padding: 5px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-0);
  color: var(--gray-600);
  font-size: 11px;

  strong {
    color: var(--gray-700);
    font-family: monospace;
    font-size: 10px;
  }

  .success {
    color: var(--color-success-700);
  }

  .warning {
    color: var(--color-warning-700);
  }
}

.failure-note {
  margin-top: 10px;
  padding: 8px 9px;
  border-radius: 6px;
  background: var(--color-warning-50);
  color: var(--color-warning-700);
}

.metric-definition-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.metric-definition {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);

  code,
  strong,
  p {
    display: block;
  }

  code {
    color: var(--main-700);
    font-size: 11px;
  }

  strong {
    margin-top: 7px;
    color: var(--gray-800);
    font-size: 13px;
  }

  p {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.55;
  }
}

@media (max-width: 1100px) {
  .overview-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .metric-definition-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .content-grid,
  .answer-grid {
    grid-template-columns: 1fr;
  }

  .mode-banner {
    flex-wrap: wrap;
  }

  .mode-banner-tag {
    margin-left: 50px;
  }
}

@media (max-width: 560px) {
  .overview-grid,
  .metric-definition-grid {
    grid-template-columns: 1fr 1fr;
  }

  .header-meta .refresh-time,
  .section-note {
    display: none;
  }

  .result-summary {
    align-items: flex-end;
    flex-direction: column;
    gap: 3px;
  }

  .result-detail {
    padding-left: 12px;
  }
}
</style>
