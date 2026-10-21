<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CircleAlert, GitCompareArrows, Play, RefreshCw, Square } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import EvaluationFailurePool from '@/components/evaluation/EvaluationFailurePool.vue'
import { evaluationApi } from '@/apis/evaluation_api'
import {
  buildLatencyRows,
  buildMetricRows,
  buildRolloutRows,
  experimentIdentity,
  faultRecoveryStat,
  formatCount,
  formatMs,
  formatPValue,
  formatPercent,
  formatSigned,
  isActiveExperiment,
  metricLabel,
  reasonLabel,
  statusMeta,
  taskSuccessStat
} from '@/utils/evaluationView'

const POLL_INTERVAL_MS = 5000
const activeTab = ref('experiments')

const experiments = ref([])
const selectedId = ref('')
const detail = ref(null)
const tasksById = ref(new Map())
const tasksVersionId = ref('')
const loading = ref(false)
const actionPending = ref(false)
const error = ref('')
const onlyFailures = ref(false)
const traceByRollout = ref(new Map())
const traceLoading = ref(new Set())
const baselineId = ref(undefined)
const comparison = ref(null)
const compareError = ref('')
const compareLoading = ref(false)
let pollTimer = null

const identity = computed(() => experimentIdentity(detail.value))
const rows = computed(() => buildRolloutRows(detail.value, tasksById.value))
const visibleRows = computed(() =>
  onlyFailures.value ? rows.value.filter((row) => row.verdict !== 'passed') : rows.value
)
const metricRows = computed(() => buildMetricRows(detail.value?.metric_breakdown))
const resultMetricRows = computed(() => metricRows.value.filter((row) => row.layer === 'result'))
const retrievalMetricRows = computed(() =>
  metricRows.value.filter((row) => row.layer === 'retrieval')
)
const trajectoryMetricRows = computed(() =>
  metricRows.value.filter((row) => row.layer === 'trajectory')
)
const otherMetricRows = computed(() => metricRows.value.filter((row) => row.layer === 'other'))
const performance = computed(() => detail.value?.performance || {})
const latencyRows = computed(() => buildLatencyRows(performance.value))
const breakdown = computed(() => detail.value?.metric_breakdown || {})
const taskCount = computed(() => identity.value.taskCount ?? tasksById.value.size)
const finishedCount = computed(() =>
  Object.entries(performance.value.rollout_status || {})
    .filter(([status]) => status !== 'RUNNING')
    .reduce((sum, [, count]) => sum + count, 0)
)
const expectedRollouts = computed(() => (taskCount.value || 0) * (detail.value?.max_attempts || 1))
const isActive = computed(() => isActiveExperiment(detail.value?.status))
// 草稿可以运行；异常结束（FAILED）的实验可以重新运行，已完成的题会按幂等键跳过。
const canRun = computed(() => ['DRAFT', 'FAILED'].includes(detail.value?.status))
// 快照里记录了评分器版本时，版本不同的实验无法对比，直接不列出；旧实验没有快照，交给后端校验。
const isComparableBaseline = (item) => {
  const current = identity.value.evaluators.join()
  const other = experimentIdentity(item).evaluators.join()
  return (
    item.experiment_id !== selectedId.value &&
    item.dataset_version_id === detail.value?.dataset_version_id &&
    item.status === 'FINISHED' &&
    (!current || !other || current === other)
  )
}
const baselineOptions = computed(() =>
  experiments.value.filter(isComparableBaseline).map((item) => {
    const info = experimentIdentity(item)
    return {
      value: item.experiment_id,
      label: `${info.agent} · ${info.model} · ${formatTime(item.created_at)}`
    }
  })
)
const keyStats = computed(() => {
  const taskPass = breakdown.value.task_pass || {}
  const answerValue = breakdown.value.answer_value
  const counts = taskPass.status_counts || {}
  const stats = [
    {
      label: '任务成功率',
      ...taskSuccessStat(
        detail.value?.aggregate,
        detail.value?.max_attempts,
        counts,
        taskPass.reason_counts
      )
    },
    faultRecoveryStat(detail.value?.fault_recovery),
    answerValue && {
      label: '答案值正确率',
      value: formatPercent(answerValue.pass_rate),
      hint: '不考虑格式，只看值是否正确'
    },
    (detail.value?.max_attempts || 1) > 1 && {
      label: `pass^${detail.value.max_attempts}`,
      value: formatPercent(detail.value.aggregate?.['pass^k']),
      hint: '每次尝试都通过的题占比'
    },
    {
      label: '完成情况',
      value: `${finishedCount.value} / ${expectedRollouts.value || '—'}`,
      hint: rolloutStatusText(performance.value.rollout_status)
    }
  ]
  return stats.filter(Boolean)
})

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function formatShortTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  const pad = (number) => String(number).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function rolloutStatusText(statusCounts) {
  const parts = Object.entries(statusCounts || {}).map(
    ([status, count]) => `${statusMeta(status).label} ${count}`
  )
  return parts.length ? parts.join(' · ') : '尚未开始'
}

const shortHash = (value) => (value ? String(value).slice(0, 12) : '—')
const listText = (value) =>
  value === null || value === undefined ? '默认' : value.length ? value.join('、') : '无'
const formatArgs = (args) => JSON.stringify(args ?? {}, null, 2)

const describeError = (err, fallback) =>
  err?.response?.data?.detail?.message || err?.message || fallback

async function loadExperiments() {
  const response = await evaluationApi.listExperiments('', { limit: 50 })
  experiments.value = response?.data?.items || []
  if (!selectedId.value && experiments.value.length)
    selectedId.value = experiments.value[0].experiment_id
}

async function loadDetail() {
  if (!selectedId.value) {
    detail.value = null
    return
  }
  // 快速切换实验时，较慢返回的旧请求不能覆盖当前选中实验的详情。
  const requestedId = selectedId.value
  const response = await evaluationApi.getExperiment(requestedId)
  if (requestedId !== selectedId.value) return
  const versionId = response?.data?.dataset_version_id
  if (versionId && versionId !== tasksVersionId.value) {
    const version = await evaluationApi.getVersion(versionId)
    if (requestedId !== selectedId.value) return
    tasksById.value = new Map((version?.data?.tasks || []).map((task) => [task.task_id, task]))
    tasksVersionId.value = versionId
  }
  detail.value = response?.data || null
  // 运行中轮询的是详情；同步列表里的状态，避免同一实验在两处显示不同状态。
  const listed = experiments.value.find((item) => item.experiment_id === requestedId)
  if (listed && detail.value) listed.status = detail.value.status
  schedulePoll()
}

function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = null
  if (!isActive.value) return
  pollTimer = setTimeout(async () => {
    try {
      await loadDetail()
      if (!isActive.value) await loadExperiments()
    } catch (err) {
      error.value = describeError(err, '刷新实验进度失败')
    }
  }, POLL_INTERVAL_MS)
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    await loadExperiments()
    await loadDetail()
  } catch (err) {
    error.value = describeError(err, '加载评测数据失败')
  } finally {
    loading.value = false
  }
}

async function selectExperiment(id) {
  if (id === selectedId.value) return
  selectedId.value = id
  traceByRollout.value = new Map()
  baselineId.value = undefined
  comparison.value = null
  compareError.value = ''
  await refreshDetail()
}

async function refreshDetail() {
  error.value = ''
  try {
    await loadDetail()
  } catch (err) {
    error.value = describeError(err, '加载实验详情失败')
  }
}

async function runAction(action) {
  actionPending.value = true
  error.value = ''
  try {
    await (action === 'run'
      ? evaluationApi.runExperiment(selectedId.value)
      : evaluationApi.cancelExperiment(selectedId.value))
    await loadDetail()
    await loadExperiments()
  } catch (err) {
    error.value = describeError(err, action === 'run' ? '启动实验失败' : '取消实验失败')
  } finally {
    actionPending.value = false
  }
}

async function compareWithBaseline() {
  if (!baselineId.value) return
  compareLoading.value = true
  compareError.value = ''
  comparison.value = null
  try {
    const response = await evaluationApi.compareExperiments(baselineId.value, selectedId.value)
    comparison.value = response?.data || null
  } catch (err) {
    compareError.value = describeError(err, '对比失败')
  } finally {
    compareLoading.value = false
  }
}

async function loadTrace(rolloutId) {
  if (traceByRollout.value.has(rolloutId) || traceLoading.value.has(rolloutId)) return
  traceLoading.value = new Set(traceLoading.value).add(rolloutId)
  try {
    const response = await evaluationApi.getTrace(rolloutId)
    traceByRollout.value = new Map(traceByRollout.value).set(
      rolloutId,
      response?.data?.events || []
    )
  } catch {
    traceByRollout.value = new Map(traceByRollout.value).set(rolloutId, null)
  } finally {
    const next = new Set(traceLoading.value)
    next.delete(rolloutId)
    traceLoading.value = next
  }
}

const deltaRows = computed(() => {
  const delta = comparison.value?.performance_delta || {}
  return [
    { label: '端到端耗时 P50', item: delta.agent_total_p50_ms, format: formatMs },
    { label: '端到端耗时均值', item: delta.agent_total_mean_ms, format: formatMs },
    { label: '端到端耗时最大值', item: delta.agent_total_max_ms, format: formatMs },
    { label: '每题 token 均值', item: delta.tokens_per_rollout_mean, format: formatCount },
    {
      label: '每题 LLM 调用均值',
      item: delta.llm_calls_per_rollout_mean,
      format: (value) => value.toFixed(2)
    },
    {
      label: '每题工具调用均值',
      item: delta.tool_calls_per_rollout_mean,
      format: (value) => value.toFixed(2)
    }
  ].filter((row) => row.item)
})

onMounted(refresh)
onBeforeUnmount(() => clearTimeout(pollTimer))
</script>

<template>
  <div class="evaluation-page">
    <PageHeader title="Agent 评测" :show-border="true">
      <template #actions>
        <a-button
          v-if="activeTab === 'experiments'"
          class="lucide-icon-btn"
          :loading="loading"
          @click="refresh"
        >
          <RefreshCw v-if="!loading" :size="16" /> 刷新
        </a-button>
      </template>
    </PageHeader>

    <div class="evaluation-content">
      <div class="view-tabs" role="tablist" aria-label="评测视图">
        <button
          id="experiments-tab"
          role="tab"
          :aria-selected="activeTab === 'experiments'"
          aria-controls="experiments-panel"
          @click="activeTab = 'experiments'"
        >
          实验
        </button>
        <button
          id="findings-tab"
          role="tab"
          :aria-selected="activeTab === 'findings'"
          aria-controls="findings-panel"
          @click="activeTab = 'findings'"
        >
          失败池
        </button>
      </div>
      <EvaluationFailurePool
        v-if="activeTab === 'findings'"
        id="findings-panel"
        role="tabpanel"
        aria-labelledby="findings-tab"
      />
      <p v-if="error && activeTab === 'experiments'" class="error-banner" role="alert">
        <CircleAlert :size="16" /> {{ error }}
      </p>

      <div
        v-show="activeTab === 'experiments'"
        id="experiments-panel"
        class="evaluation-layout"
        role="tabpanel"
        aria-labelledby="experiments-tab"
      >
        <aside class="experiment-panel" aria-label="实验列表">
          <div class="panel-title">
            <h2>实验</h2>
            <span class="muted">{{ experiments.length }} 个</span>
          </div>
          <div v-if="experiments.length" class="experiment-list">
            <button
              v-for="item in experiments"
              :key="item.experiment_id"
              type="button"
              class="experiment-row"
              :class="{ selected: item.experiment_id === selectedId }"
              @click="selectExperiment(item.experiment_id)"
            >
              <span class="experiment-main">
                <strong>{{ experimentIdentity(item).agent }}</strong>
                <span class="muted ellipsis" :title="experimentIdentity(item).note"
                  >{{ formatShortTime(item.created_at) }} ·
                  {{ experimentIdentity(item).model }}</span
                >
              </span>
              <span class="status-tag" :class="statusMeta(item.status).tone">{{
                statusMeta(item.status).label
              }}</span>
            </button>
          </div>
          <p v-else-if="!loading" class="empty-state">
            还没有评测实验。基于已发布的数据集创建实验后，会显示在这里。
          </p>
        </aside>

        <main v-if="detail" class="experiment-detail">
          <section class="detail-head">
            <div class="detail-title">
              <h2>
                {{ identity.agent }} <span class="muted">· {{ identity.model }}</span>
              </h2>
              <p class="muted">
                {{ formatTime(detail.created_at) }} · {{ taskCount || 0 }} 题<template
                  v-if="detail.max_attempts > 1"
                >
                  × {{ detail.max_attempts }} 次</template
                >
                <template v-if="identity.evaluators.length">
                  · 评分 {{ identity.evaluators.join('、') }}</template
                >
              </p>
              <p v-if="identity.note" class="note">{{ identity.note }}</p>
            </div>
            <div class="detail-actions">
              <span class="status-tag" :class="statusMeta(detail.status).tone">{{
                statusMeta(detail.status).label
              }}</span>
              <a-button
                v-if="canRun"
                type="primary"
                class="lucide-icon-btn"
                :loading="actionPending"
                @click="runAction('run')"
              >
                <Play v-if="!actionPending" :size="16" />
                {{ detail.status === 'FAILED' ? '重新运行' : '运行实验' }}
              </a-button>
              <a-button
                v-if="isActive"
                class="lucide-icon-btn"
                :loading="actionPending"
                title="长时间没有进展时可以重新投递；同一实验同时只会有一个执行者"
                @click="runAction('run')"
              >
                <Play v-if="!actionPending" :size="16" /> 继续运行
              </a-button>
              <a-button
                v-if="isActive"
                danger
                class="lucide-icon-btn"
                :loading="actionPending"
                @click="runAction('cancel')"
              >
                <Square v-if="!actionPending" :size="16" /> 取消实验
              </a-button>
            </div>
          </section>

          <p v-if="detail.status === 'FAILED' && detail.error_message" class="failure-note">
            <CircleAlert :size="16" /> 实验异常结束：{{
              detail.error_message
            }}。可以重新运行，已完成的题不会重复执行。
          </p>

          <p v-if="isActive" class="progress-note">
            实验正在后台运行，已完成 {{ finishedCount }} / {{ expectedRollouts }}，页面每 5
            秒自动刷新。
          </p>

          <section class="stat-grid" aria-label="关键结果">
            <div v-for="stat in keyStats" :key="stat.label" class="stat-tile">
              <span class="muted">{{ stat.label }}</span>
              <strong>{{ stat.value }}</strong>
              <small class="muted">{{ stat.hint }}</small>
              <small v-if="stat.notice" class="calibration-note">{{ stat.notice }}</small>
            </div>
          </section>

          <section v-if="metricRows.length" class="detail-card">
            <h3>指标明细</h3>
            <div
              v-for="group in [
                { title: '结果层', hint: '全部通过才算任务成功', rows: resultMetricRows },
                {
                  title: '检索层',
                  hint: '反映相关内容的命中、召回与排序',
                  rows: retrievalMetricRows
                },
                {
                  title: '轨迹层',
                  hint: '只反映过程质量，不影响任务成功',
                  rows: trajectoryMetricRows
                },
                { title: '其他', hint: '', rows: otherMetricRows }
              ]"
              :key="group.title"
              class="metric-group"
            >
              <template v-if="group.rows.length">
                <p class="group-title">
                  {{ group.title }} <span class="muted">{{ group.hint }}</span>
                </p>
                <div class="table-scroll">
                  <table class="data-table">
                    <thead>
                      <tr>
                        <th>指标</th>
                        <th>通过率 / 均值</th>
                        <th>通过 · 未通过 · 未评分</th>
                        <th>主要原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="row in group.rows" :key="row.name">
                        <td>{{ row.label }}</td>
                        <td class="numeric">
                          {{
                            row.passRate !== null && row.passRate !== undefined
                              ? formatPercent(row.passRate)
                              : formatPercent(row.measuredMean)
                          }}
                        </td>
                        <td class="numeric">
                          <template v-if="row.counts.measured"
                            >{{ row.counts.measured }} 题已测量</template
                          >
                          <template v-else
                            >{{ row.counts.passed || 0 }} · {{ row.counts.failed || 0 }} ·
                            {{ row.counts.not_measured || 0 }}</template
                          >
                        </td>
                        <td>
                          <span
                            v-for="reason in row.reasons"
                            :key="reason.label"
                            class="reason-chip"
                            >{{ reason.label }} × {{ reason.count }}</span
                          >
                          <span v-if="!row.reasons.length" class="muted">—</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
            </div>
          </section>

          <section v-if="latencyRows.length || performance.usage?.sample_count" class="detail-card">
            <h3>性能与成本</h3>
            <div v-if="latencyRows.length" class="table-scroll">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>耗时</th>
                    <th>P50</th>
                    <th>P95</th>
                    <th>最大</th>
                    <th>样本</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in latencyRows" :key="row.label">
                    <td>{{ row.label }}</td>
                    <td class="numeric">{{ formatMs(row.item.p50) }}</td>
                    <td class="numeric">
                      {{ row.item.p95 === null ? '样本不足' : formatMs(row.item.p95) }}
                    </td>
                    <td class="numeric">{{ formatMs(row.item.max) }}</td>
                    <td class="numeric">{{ row.item.sample_count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <dl v-if="performance.usage?.sample_count" class="fact-list">
              <div>
                <dt>Token 总量</dt>
                <dd>
                  {{ formatCount(performance.usage.total_sum) }}
                  <span class="muted"
                    >（输入 {{ formatCount(performance.usage.input_sum) }} / 输出
                    {{ formatCount(performance.usage.output_sum) }}）</span
                  >
                </dd>
              </div>
              <div>
                <dt>每题 Token</dt>
                <dd>
                  中位 {{ formatCount(performance.usage.total_per_rollout?.p50) }} · 最大
                  {{ formatCount(performance.usage.total_per_rollout?.max) }}
                </dd>
              </div>
              <div>
                <dt>每题 LLM 调用</dt>
                <dd>
                  平均 {{ performance.usage.llm_calls_per_rollout?.mean?.toFixed(2) ?? '—' }} 次
                </dd>
              </div>
              <div>
                <dt>工具调用</dt>
                <dd>
                  <template v-if="Object.keys(performance.tool_calls?.by_name || {}).length"
                    ><span
                      v-for="(count, name) in performance.tool_calls.by_name"
                      :key="name"
                      class="reason-chip mono"
                      >{{ name }} × {{ count }}</span
                    ></template
                  ><span v-else class="muted">无</span>
                </dd>
              </div>
              <div>
                <dt>估算成本</dt>
                <dd>
                  <template v-if="performance.estimated_cost"
                    >{{ performance.estimated_cost.total.toFixed(4) }}
                    {{ performance.estimated_cost.currency }}
                    <span class="muted"
                      >（每题 {{ performance.estimated_cost.per_rollout_mean?.toFixed(4) }}）</span
                    ></template
                  ><span v-else class="muted">创建实验时未提供模型单价，不估算</span>
                </dd>
              </div>
            </dl>
          </section>

          <section class="detail-card">
            <div class="card-title-row">
              <h3>逐题结果</h3>
              <a-checkbox v-model:checked="onlyFailures">只看未通过</a-checkbox>
            </div>
            <div v-if="visibleRows.length" class="rollout-list">
              <details
                v-for="row in visibleRows"
                :key="row.rolloutId"
                class="rollout-item"
                @toggle="$event.target.open && loadTrace(row.rolloutId)"
              >
                <summary>
                  <span class="mono task-key"
                    >{{ row.taskKey
                    }}<template v-if="detail.max_attempts > 1"> #{{ row.attempt }}</template></span
                  >
                  <span class="status-tag" :class="statusMeta(row.verdict).tone">{{
                    statusMeta(row.verdict).label
                  }}</span>
                  <span class="reason ellipsis">{{ reasonLabel(row.reason) }}</span>
                  <span class="muted numeric">{{ formatMs(row.latencyMs) }}</span>
                  <span class="muted numeric">{{
                    row.tokens !== null ? `${formatCount(row.tokens)} tok` : ''
                  }}</span>
                </summary>
                <div class="rollout-detail">
                  <dl class="fact-list">
                    <div v-if="row.query">
                      <dt>题目</dt>
                      <dd>{{ row.query }}</dd>
                    </div>
                    <div v-if="row.expected !== null">
                      <dt>期望答案</dt>
                      <dd class="mono">{{ row.expected }}</dd>
                    </div>
                  </dl>
                  <p v-if="row.errorMessage" class="failure-note">
                    <CircleAlert :size="16" /> {{ row.errorMessage }}
                  </p>
                  <template v-if="row.finalAnswer">
                    <p class="group-title">最终回答</p>
                    <pre class="text-block">{{ row.finalAnswer }}</pre>
                  </template>
                  <p class="group-title">
                    工具调用 <span class="muted">{{ row.toolCalls.length }} 次</span>
                  </p>
                  <ol v-if="row.toolCalls.length" class="tool-list">
                    <li v-for="(call, index) in row.toolCalls" :key="index">
                      <div class="tool-head">
                        <span class="mono">{{ call.name }}</span
                        ><span
                          class="status-tag"
                          :class="call.status === 'success' ? 'success' : 'error'"
                          >{{ call.status }}</span
                        >
                      </div>
                      <pre class="text-block small">{{ formatArgs(call.args) }}</pre>
                      <pre v-if="call.output" class="text-block small">{{ call.output }}</pre>
                    </li>
                  </ol>
                  <p v-else class="muted">没有调用工具</p>
                  <p class="group-title">逐项评分</p>
                  <div class="metric-chips">
                    <span
                      v-for="(metric, name) in row.metrics"
                      :key="name"
                      class="metric-chip"
                      :class="statusMeta(metric.status).tone"
                    >
                      {{ metricLabel(name)
                      }}<template v-if="metric.status === 'measured'">
                        {{ formatPercent(metric.value) }}</template
                      >
                    </span>
                  </div>
                  <p class="group-title">Trace</p>
                  <p v-if="traceLoading.has(row.rolloutId)" class="muted">加载中…</p>
                  <p v-else-if="traceByRollout.get(row.rolloutId) === null" class="muted">
                    Trace 加载失败，展开重试
                  </p>
                  <ol v-else-if="traceByRollout.get(row.rolloutId)?.length" class="trace-list">
                    <li
                      v-for="event in traceByRollout.get(row.rolloutId)"
                      :key="event.event_id"
                      class="mono"
                    >
                      {{ event.component }} · {{ event.operation }}
                    </li>
                  </ol>
                </div>
              </details>
            </div>
            <p v-else class="empty-state">
              {{
                rows.length
                  ? '没有未通过的题。'
                  : isActive
                    ? '题目正在执行，结果会陆续出现。'
                    : '这个实验还没有结果，运行后会在这里逐题展示。'
              }}
            </p>
          </section>

          <section class="detail-card">
            <h3>与基线对比</h3>
            <p class="muted">
              选择同一数据集版本下已完成的实验作为基线，逐题比较哪些题修复、哪些题退化。
            </p>
            <div class="compare-controls">
              <a-select
                v-model:value="baselineId"
                class="baseline-select"
                placeholder="选择基线实验"
                :options="baselineOptions"
                :disabled="!baselineOptions.length"
                :not-found-content="null"
              />
              <a-button
                class="lucide-icon-btn"
                :disabled="!baselineId"
                :loading="compareLoading"
                @click="compareWithBaseline"
              >
                <GitCompareArrows v-if="!compareLoading" :size="16" /> 对比
              </a-button>
            </div>
            <p v-if="!baselineOptions.length" class="muted">
              没有可用的基线：需要同一数据集版本下另一个已完成的实验。
            </p>
            <p v-if="compareError" class="failure-note">
              <CircleAlert :size="16" /> {{ compareError }}
            </p>
            <div v-if="comparison" class="compare-result">
              <div class="stat-grid">
                <div class="stat-tile">
                  <span class="muted">任务成功率</span
                  ><strong
                    >{{ formatPercent(comparison.outcomes.base_pass_rate) }} →
                    {{ formatPercent(comparison.outcomes.candidate_pass_rate) }}</strong
                  ><small class="muted"
                    >变化 {{ formatSigned(comparison.outcomes.pass_rate_delta, formatPercent) }} ·
                    配对 {{ comparison.outcomes.paired_count }} 题</small
                  >
                </div>
                <div class="stat-tile">
                  <span class="muted">修复 / 退化</span
                  ><strong
                    >{{ comparison.outcomes.fixed.length }} /
                    {{ comparison.outcomes.broken.length }}</strong
                  ><small class="muted"
                    >同为通过 {{ comparison.outcomes.both_pass }} · 同为未通过
                    {{ comparison.outcomes.both_fail }}</small
                  >
                </div>
                <div class="stat-tile">
                  <span class="muted">McNemar p 值</span
                  ><strong>{{ formatPValue(comparison.outcomes.mcnemar_p_value) }}</strong
                  ><small class="muted">{{
                    comparison.outcomes.mcnemar_p_value < 0.05
                      ? '差异显著（p < 0.05）'
                      : '差异不显著，可能是运行波动'
                  }}</small>
                </div>
              </div>
              <dl class="fact-list">
                <div>
                  <dt>修复的题</dt>
                  <dd>
                    <span
                      v-for="key in comparison.outcomes.fixed"
                      :key="key"
                      class="reason-chip mono success"
                      >{{ key }}</span
                    ><span v-if="!comparison.outcomes.fixed.length" class="muted">无</span>
                  </dd>
                </div>
                <div>
                  <dt>退化的题</dt>
                  <dd>
                    <span
                      v-for="key in comparison.outcomes.broken"
                      :key="key"
                      class="reason-chip mono error"
                      >{{ key }}</span
                    ><span v-if="!comparison.outcomes.broken.length" class="muted">无</span>
                  </dd>
                </div>
                <div v-if="comparison.outcomes.unpaired_task_keys.length">
                  <dt>未配对</dt>
                  <dd class="muted">
                    {{ comparison.outcomes.unpaired_task_keys.join('、') }}（任一侧未评分）
                  </dd>
                </div>
              </dl>
              <div v-if="deltaRows.length" class="table-scroll">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>性能</th>
                      <th>基线</th>
                      <th>当前</th>
                      <th>变化</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in deltaRows" :key="row.label">
                      <td>{{ row.label }}</td>
                      <td class="numeric">
                        {{ typeof row.item.base === 'number' ? row.format(row.item.base) : '—' }}
                      </td>
                      <td class="numeric">
                        {{
                          typeof row.item.candidate === 'number'
                            ? row.format(row.item.candidate)
                            : '—'
                        }}
                      </td>
                      <td class="numeric">{{ formatSigned(row.item.delta, row.format) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="group-title">配置差异</p>
              <div v-if="comparison.config_diff.length" class="table-scroll">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>配置项</th>
                      <th>基线</th>
                      <th>当前</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in comparison.config_diff" :key="item.key">
                      <td class="mono">{{ item.key }}</td>
                      <td class="mono">{{ item.base ?? '—' }}</td>
                      <td class="mono">{{ item.candidate ?? '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-else class="muted">两次实验记录的配置项一致。</p>
            </div>
          </section>

          <details class="detail-card snapshot">
            <summary>
              <h3>配置快照</h3>
              <span class="muted">复现这次实验所需的模型、配置与版本</span>
            </summary>
            <dl class="fact-list">
              <div>
                <dt>模型</dt>
                <dd class="mono">{{ identity.model }}</dd>
              </div>
              <div>
                <dt>工具审批</dt>
                <dd>
                  {{
                    detail.config_snapshot?.tool_approval_mode === 'always_trust'
                      ? '完全信任（评测无人审批）'
                      : detail.config_snapshot?.tool_approval_mode || '—'
                  }}
                </dd>
              </div>
              <template v-if="detail.config_snapshot?.captured?.agent">
                <div>
                  <dt>配置的工具</dt>
                  <dd>{{ listText(detail.config_snapshot.captured.agent.configured_tools) }}</dd>
                </div>
                <div>
                  <dt>配置的知识库</dt>
                  <dd>
                    {{ listText(detail.config_snapshot.captured.agent.configured_knowledges) }}
                  </dd>
                </div>
                <div>
                  <dt>Prompt 校验和</dt>
                  <dd class="mono">
                    {{ shortHash(detail.config_snapshot.captured.agent.system_prompt_checksum) }}
                  </dd>
                </div>
                <div>
                  <dt>Agent 配置校验和</dt>
                  <dd class="mono">
                    {{ shortHash(detail.config_snapshot.captured.agent.agent_context_checksum) }}
                  </dd>
                </div>
              </template>
              <div>
                <dt>数据集版本</dt>
                <dd class="mono">
                  {{ detail.dataset_version_id
                  }}<span class="muted">
                    · {{ shortHash(detail.config_snapshot?.captured?.dataset?.checksum) }}</span
                  >
                </dd>
              </div>
              <div>
                <dt>代码校验和</dt>
                <dd class="mono">
                  {{ shortHash(detail.config_snapshot?.captured?.code_checksum) }}
                </dd>
              </div>
              <div>
                <dt>Git 提交</dt>
                <dd class="mono">
                  {{ shortHash(detail.config_snapshot?.git_commit)
                  }}<span v-if="detail.config_snapshot?.git_dirty" class="muted">
                    · 含未提交改动</span
                  >
                </dd>
              </div>
              <div>
                <dt>实验 ID</dt>
                <dd class="mono">{{ detail.experiment_id }}</dd>
              </div>
            </dl>
          </details>
        </main>

        <main v-else class="experiment-detail">
          <p class="empty-state">{{ loading ? '加载中…' : '选择左侧的实验查看结果。' }}</p>
        </main>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.evaluation-page {
  min-height: 100%;
  background: var(--gray-0);
  color: var(--color-text);
}
.evaluation-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: var(--page-padding);
}
.view-tabs {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid var(--gray-150);
  button {
    padding: 8px 16px;
    border: 0;
    border-bottom: 2px solid transparent;
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    &[aria-selected='true'] {
      border-bottom-color: var(--main-color);
      color: var(--main-color);
    }
    &:hover {
      background: var(--gray-25);
    }
    &:focus-visible {
      outline: 2px solid var(--main-color);
    }
  }
}
.calibration-note {
  color: var(--color-warning-900);
}
.evaluation-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}
.muted {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 400;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.numeric {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.error-banner,
.failure-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--color-error-700);
  font-size: 13px;
}
.error-banner {
  padding: 10px 12px;
  border: 1px solid var(--color-error-100);
  border-radius: 8px;
  background: var(--color-error-50);
}

.experiment-panel {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.panel-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.experiment-list {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 160px);
  overflow-y: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.experiment-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-0);
  color: inherit;
  text-align: left;
  cursor: pointer;
  &:last-child {
    border-bottom: 0;
  }
  &:hover {
    background: var(--gray-25);
  }
  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: -2px;
  }
  &.selected {
    background: var(--main-10);
  }
}
.experiment-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  strong {
    font-size: 14px;
    font-weight: 500;
  }
}

.status-tag {
  display: inline-flex;
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  font-weight: 500;
  &.success {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }
  &.error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
  &.warning {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }
  &.info {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }
}

.experiment-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.detail-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  p {
    margin: 0;
  }
}
.note {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.detail-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}
.progress-note {
  margin: 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--color-info-50);
  color: var(--color-info-700);
  font-size: 13px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
}
.stat-tile {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  strong {
    font-size: 22px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
}

.detail-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  p {
    margin: 0;
  }
}
.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.metric-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  &:empty {
    display: none;
  }
}
.group-title {
  margin: 4px 0 0;
  font-size: 13px;
  font-weight: 600;
}
.table-scroll {
  overflow-x: auto;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  th {
    padding: 8px 12px;
    border-bottom: 1px solid var(--gray-150);
    color: var(--color-text-secondary);
    font-weight: 500;
    text-align: left;
    white-space: nowrap;
  }
  td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--gray-100);
    vertical-align: top;
  }
  td:first-child {
    white-space: nowrap;
  }
  tr:last-child td {
    border-bottom: 0;
  }
}
.reason-chip {
  display: inline-flex;
  margin: 0 6px 4px 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--gray-50);
  color: var(--gray-700);
  font-size: 12px;
  &.success {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }
  &.error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}
.fact-list {
  display: grid;
  gap: 8px;
  margin: 0;
  div {
    display: grid;
    grid-template-columns: 120px minmax(0, 1fr);
    gap: 12px;
  }
  dt {
    color: var(--color-text-secondary);
    font-size: 13px;
  }
  dd {
    margin: 0;
    font-size: 13px;
    overflow-wrap: anywhere;
  }
}

.rollout-list {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.rollout-item {
  border-bottom: 1px solid var(--gray-100);
  &:last-child {
    border-bottom: 0;
  }
}
.rollout-item summary {
  display: grid;
  grid-template-columns: minmax(120px, 180px) auto minmax(0, 1fr) 72px 88px;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  cursor: pointer;
  list-style: none;
  &::-webkit-details-marker {
    display: none;
  }
  &:hover {
    background: var(--gray-25);
  }
  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: -2px;
  }
}
.task-key {
  font-size: 13px;
}
.reason {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.rollout-detail {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px 16px;
  border-top: 1px solid var(--gray-100);
  background: var(--gray-10);
}
.text-block {
  max-height: 240px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-0);
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
  &.small {
    max-height: 160px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
  }
}
.tool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding-left: 20px;
}
.tool-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.metric-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.metric-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  &.success {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }
  &.error {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}
.trace-list {
  margin: 0;
  padding-left: 20px;
  color: var(--gray-600);
}

.compare-controls {
  display: flex;
  gap: 8px;
}
.baseline-select {
  flex: 1;
  min-width: 0;
  max-width: 520px;
}
.compare-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.snapshot summary {
  display: flex;
  align-items: baseline;
  gap: 12px;
  cursor: pointer;
  list-style: none;
  &::-webkit-details-marker {
    display: none;
  }
}
.empty-state {
  margin: 0;
  padding: 24px 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

@media (max-width: 1024px) {
  .evaluation-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .experiment-panel {
    position: static;
  }
  .experiment-list {
    max-height: 280px;
  }
}
@media (max-width: 640px) {
  .detail-head {
    flex-direction: column;
  }
  .rollout-item summary {
    grid-template-columns: minmax(0, 1fr) auto;
    .reason,
    .numeric {
      display: none;
    }
  }
  .fact-list div {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
  .compare-controls {
    flex-direction: column;
  }
  .baseline-select {
    max-width: none;
  }
}
</style>
