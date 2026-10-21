<script setup>
import { onMounted, ref } from 'vue'
import { CircleAlert, RefreshCw } from 'lucide-vue-next'
import { evaluationApi } from '@/apis/evaluation_api'
import { reasonLabel, statusMeta } from '@/utils/evaluationView'
import { formatFullDateTime } from '@/utils/time'

const findings = ref([])
const datasets = ref([])
const status = ref('pending_review')
const selectedIds = ref([])
const datasetId = ref(undefined)
const loading = ref(false)
const promoting = ref(false)
const error = ref('')
const createdVersionId = ref('')
let loadSequence = 0

/** 刷新当前状态的样本及可选数据集，快速筛选时仅采用最后一次请求。 */
async function refresh() {
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  selectedIds.value = []
  findings.value = []
  try {
    const [samples, targets] = await Promise.all([
      evaluationApi.listOnlineFindings({ status: status.value }),
      evaluationApi.listDatasets('', { limit: 100 })
    ])
    const items = [...targets.data.items]
    let cursor = targets.data.next_cursor
    while (cursor) {
      const page = await evaluationApi.listDatasets('', { limit: 100, cursor })
      items.push(...page.data.items)
      cursor = page.data.next_cursor
    }
    if (sequence !== loadSequence) return
    findings.value = samples.data
    datasets.value = items
  } catch (err) {
    if (sequence === loadSequence) error.value = err.message || '加载失败池失败'
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

/** 把勾选的待复核样本生成草稿，不自动补写期望或发布。 */
async function promote() {
  promoting.value = true
  error.value = ''
  createdVersionId.value = ''
  try {
    const response = await evaluationApi.promoteOnlineFindings(datasetId.value, selectedIds.value)
    createdVersionId.value = response.data.version_id
    await refresh()
  } catch (err) {
    error.value = err.message || '生成草稿版本失败'
  } finally {
    promoting.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section class="failure-pool" aria-label="线上抽样失败池">
    <div class="pool-toolbar">
      <label
        >状态
        <a-select
          v-model:value="status"
          :disabled="promoting"
          @change="refresh"
          aria-label="失败池状态"
          class="status-select"
          :options="[
            { value: '', label: '全部' },
            { value: 'pending_review', label: '待复核' },
            { value: 'promoted', label: '已生成草稿' }
          ]"
        />
      </label>
      <a-button class="lucide-icon-btn" :loading="loading" :disabled="promoting" @click="refresh"
        ><RefreshCw v-if="!loading" :size="16" />刷新</a-button
      >
    </div>
    <p class="pool-hint">
      展示最近 500 条命中样本，每次最多选择 200 条。生成草稿后需补写期望并复核。
    </p>
    <p v-if="error" class="pool-error" role="alert"><CircleAlert :size="16" />{{ error }}</p>
    <p v-if="createdVersionId" class="pool-success" role="status">
      已生成草稿版本 <span class="mono">{{ createdVersionId }}</span
      >。草稿题需要补写期望并复核后才能发布。
    </p>
    <a-checkbox-group
      v-model:value="selectedIds"
      class="findings-list"
      :disabled="loading || promoting"
    >
      <article v-for="finding in findings" :key="finding.finding_id" class="finding-row">
        <a-checkbox
          :value="finding.finding_id"
          :disabled="
            finding.status !== 'pending_review' ||
            (selectedIds.length >= 200 && !selectedIds.includes(finding.finding_id))
          "
          :aria-label="`选择 Run ${finding.run_id}`"
        />
        <div class="finding-detail">
          <div class="finding-head">
            <strong>{{ finding.agent_slug }}</strong>
            <span class="finding-status" :class="statusMeta(finding.status).tone">{{
              statusMeta(finding.status).label
            }}</span>
          </div>
          <p>
            Run ID：<span class="mono">{{ finding.run_id }}</span>
          </p>
          <p>命中规则：{{ finding.rule_hits.map(reasonLabel).join('、') }}</p>
          <p>
            工具调用 {{ finding.summary.tool_calls }} 次 · 报错 {{ finding.summary.tool_errors }} 次
          </p>
          <p>点踩原因：{{ finding.feedback?.reason || '无' }}</p>
          <p class="pool-hint">
            <!-- 该接口将 UTC naive 时间序列化为不带时区的 ISO 字符串。 -->
            {{ formatFullDateTime(finding.run_finished_at && `${finding.run_finished_at}Z`) }}
          </p>
        </div>
      </article>
    </a-checkbox-group>
    <p v-if="!findings.length" class="pool-hint">
      {{ loading ? '加载中…' : '当前状态没有失败样本。可切换状态查看。' }}
    </p>
    <div class="pool-toolbar promote-controls">
      <label
        >目标数据集
        <a-select
          v-model:value="datasetId"
          class="dataset-select"
          aria-label="目标数据集"
          placeholder="选择数据集"
          :disabled="loading || promoting"
          :options="datasets.map((item) => ({ value: item.dataset_id, label: item.name }))"
        />
      </label>
      <a-button
        type="primary"
        :loading="promoting"
        :disabled="loading || !datasetId || !selectedIds.length"
        @click="promote"
        >生成草稿版本{{ selectedIds.length ? `（${selectedIds.length} 条）` : '' }}</a-button
      >
    </div>
  </section>
</template>

<style scoped lang="less">
.failure-pool {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.pool-toolbar {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 12px;
  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 13px;
  }
}
.status-select {
  width: 160px;
}
.dataset-select {
  width: 320px;
  max-width: 100%;
}
.pool-hint {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.pool-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--color-error-700);
}
.pool-success {
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--color-success-50);
  color: var(--color-success-700);
  overflow-wrap: anywhere;
}
.findings-list {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.finding-row {
  display: flex;
  align-items: start;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--gray-100);
  &:last-child {
    border-bottom: 0;
  }
}
.finding-detail {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  flex: 1;
  p {
    margin: 0;
    overflow-wrap: anywhere;
    font-size: 13px;
  }
}
.finding-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  overflow-wrap: anywhere;
}
.finding-status {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  &.warning {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
@media (max-width: 640px) {
  .promote-controls {
    flex-direction: column;
    align-items: stretch;
    label {
      min-width: 0;
    }
  }
  .dataset-select {
    width: 100%;
  }
}
</style>
