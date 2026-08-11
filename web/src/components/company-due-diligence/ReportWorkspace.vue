<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Download, FileStack, RefreshCw, RotateCcw, Square } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

import { companyDueDiligenceApi } from '@/apis/company_due_diligence_api'
import { taskerApi } from '@/apis/tasker'
import { useTaskPolling } from '@/composables/useTaskPolling'
import {
  REPORT_SECTIONS,
  TERMINAL_TASK_STATUSES,
  describeApiError,
  reportProgress,
  statusMeta
} from '@/utils/companyDueDiligence'

const props = defineProps({ company: { type: Object, default: null } })

const selectedSections = ref(REPORT_SECTIONS.map(([value]) => value))
const reports = ref([])
const reportId = ref(null)
const creating = ref(false)

const reportPoll = useTaskPolling(companyDueDiligenceApi.getReport, {
  isTerminal: (detail) => reportProgress(detail).terminal
})
const retryPoll = useTaskPolling(taskerApi.fetchTaskDetail, {
  isTerminal: (detail) => TERMINAL_TASK_STATUSES.has(detail?.task?.status)
})

const detail = computed(() => reportPoll.detail.value)
const taskState = computed(() => reportProgress(detail.value))
const report = computed(() => detail.value?.report || null)
const taskStatus = computed(() => statusMeta(taskState.value.status))

const loadReports = async () => {
  try {
    const result = await companyDueDiligenceApi.listReports(20)
    reports.value = result.reports || []
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const createReport = async () => {
  if (!props.company?.eid) return message.warning('请先确认唯一企业')
  creating.value = true
  try {
    const result = await companyDueDiligenceApi.createReport({
      company_ref: { eid: props.company.eid },
      sections: selectedSections.value
    })
    reportId.value = result.report_id
    await reportPoll.start(reportId.value)
    await loadReports()
  } catch (error) {
    message.error(describeApiError(error))
  } finally {
    creating.value = false
  }
}

const openReport = async (id) => {
  reportId.value = id
  await reportPoll.start(id)
}

const cancelReport = async () => {
  try {
    await companyDueDiligenceApi.cancelReport(reportId.value)
    message.success('已请求取消报告')
    await reportPoll.start(reportId.value)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

const retrySection = async (section) => {
  try {
    const result = await companyDueDiligenceApi.retryReportSection(reportId.value, section)
    message.success(`已提交 ${section} 章节重试`)
    await retryPoll.start(result.task_id)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

watch(
  () => retryPoll.detail.value?.task?.status,
  async (status) => {
    if (TERMINAL_TASK_STATUSES.has(status) && reportId.value) await openReport(reportId.value)
  }
)

const downloadReport = async (format) => {
  try {
    const response = await companyDueDiligenceApi.downloadReport(reportId.value, format)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `company-due-diligence-${reportId.value}.${format === 'markdown' ? 'md' : 'json'}`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    message.error(describeApiError(error))
  }
}

onMounted(loadReports)
</script>

<template>
  <section class="report-workspace">
    <div class="report-controls">
      <div>
        <h3><FileStack size="18" /> 完整尽调报告</h3>
        <p>选择章节后生成版本锁定、可追溯的报告。</p>
      </div>
      <a-button type="primary" :disabled="!company || !selectedSections.length" :loading="creating" @click="createReport">
        生成报告
      </a-button>
    </div>

    <a-checkbox-group v-model:value="selectedSections" class="section-selector">
      <a-checkbox v-for="([value, label]) in REPORT_SECTIONS" :key="value" :value="value">{{ label }}</a-checkbox>
    </a-checkbox-group>

    <div v-if="reports.length" class="recent-reports">
      <span>最近报告</span>
      <button v-for="item in reports.slice(0, 6)" :key="item.id" type="button" @click="openReport(item.id)">
        {{ item.id.slice(0, 8) }} · {{ statusMeta(item.status).label }}
      </button>
      <a-button type="text" size="small" @click="loadReports"><RefreshCw size="14" /></a-button>
    </div>

    <div v-if="reportId" class="report-status-card">
      <header>
        <div>
          <strong>报告 {{ reportId }}</strong>
          <span>{{ taskState.message }}</span>
        </div>
        <a-tag :color="taskStatus.color">{{ taskStatus.label }}</a-tag>
      </header>
      <a-progress :percent="Math.round(taskState.progress)" :status="taskState.status === 'failed' ? 'exception' : 'active'" />
      <div class="report-actions">
        <a-button v-if="!taskState.terminal" danger @click="cancelReport"><Square size="14" />取消</a-button>
        <template v-if="report">
          <a-button @click="downloadReport('json')"><Download size="14" />JSON</a-button>
          <a-button @click="downloadReport('markdown')"><Download size="14" />Markdown</a-button>
        </template>
      </div>
    </div>

    <div v-if="report" class="report-sections">
      <article v-for="(section, name) in report.sections" :key="name">
        <header>
          <div>
            <strong>{{ section.title }}</strong>
            <a-tag :color="statusMeta(section.status).color">{{ statusMeta(section.status).label }}</a-tag>
          </div>
          <a-button v-if="section.status === 'failed'" size="small" @click="retrySection(name)">
            <RotateCcw size="13" />重试
          </a-button>
        </header>
        <p>{{ section.summary }}</p>
        <small v-if="section.query_result">
          事实 {{ section.query_result.facts?.length || 0 }} · 路径 {{ section.query_result.paths?.length || 0 }} ·
          证据 {{ section.query_result.evidence?.length || 0 }} · 来源 {{ section.query_result.sources?.length || 0 }}
        </small>
        <a-alert v-if="section.error" type="error" :message="section.error" show-icon />
      </article>
    </div>
  </section>
</template>

<style scoped lang="less">
.report-workspace {
  display: grid;
  gap: 16px;
}

.report-controls,
.report-status-card header,
.report-sections article header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.report-controls {
  h3 {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 0;
    color: var(--gray-1000);
  }

  p {
    margin: 4px 0 0;
    color: var(--gray-600);
  }
}

.section-selector {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  padding: 13px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);
}

.recent-reports {
  display: flex;
  align-items: center;
  gap: 7px;
  overflow-x: auto;

  > span {
    flex: 0 0 auto;
    color: var(--gray-600);
    font-size: 12px;
  }

  button {
    flex: 0 0 auto;
    padding: 5px 8px;
    border: 1px solid var(--gray-200);
    border-radius: 7px;
    background: var(--gray-0);
    color: var(--gray-700);
    cursor: pointer;
  }
}

.report-status-card {
  padding: 14px;
  border: 1px solid var(--main-100);
  border-radius: 10px;
  background: var(--main-5);

  header div {
    display: grid;
    gap: 3px;
  }

  header span {
    color: var(--gray-600);
    font-size: 12px;
  }
}

.report-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

.report-sections {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;

  article {
    padding: 13px;
    border: 1px solid var(--gray-200);
    border-radius: 10px;
    background: var(--gray-0);
  }

  header div {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  p {
    margin: 10px 0;
    color: var(--gray-700);
    line-height: 1.6;
  }

  small {
    color: var(--gray-500);
  }
}

@media (max-width: 900px) {
  .section-selector,
  .report-sections {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 600px) {
  .report-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .section-selector,
  .report-sections {
    grid-template-columns: 1fr;
  }
}
</style>
