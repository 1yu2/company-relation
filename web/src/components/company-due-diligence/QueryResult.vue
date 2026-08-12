<script setup>
import { computed } from 'vue'
import {
  BookOpenCheck,
  CheckCircle2,
  Database,
  FileText,
  GitBranch,
  ShieldCheck
} from 'lucide-vue-next'

import DataQualityPanel from './DataQualityPanel.vue'
import EvidenceList from './EvidenceList.vue'
import RelationPathView from './RelationPathView.vue'
import { presentationSections, statusMeta } from '@/utils/companyDueDiligence'

const props = defineProps({ result: { type: Object, default: null } })

const status = computed(() => statusMeta(props.result?.status))
const sections = computed(() => presentationSections(props.result))
const sources = computed(() =>
  Object.fromEntries((props.result?.sources || []).map((source) => [source.source_id, source]))
)
const defaultTab = computed(() => {
  if (sections.value.facts) return 'facts'
  if (sections.value.relations) return 'relations'
  if (sections.value.evidence) return 'evidence'
  if (sections.value.quality) return 'quality'
  return 'sources'
})

const topicLabels = {
  profile: '企业概况',
  shareholders: '股东与出资',
  relations: '企业关系',
  products: '产品业务',
  financing: '融资',
  financials: '财务',
  risks: '风险',
  intellectual_property: '知识产权',
  news: '新闻舆情',
  full_due_diligence: '完整尽调'
}

const presentationLabels = {
  direct_answer: '直接回答',
  summary_with_list: '摘要与列表',
  relation_result: '关系结果',
  risk_summary: '风险摘要',
  topic_report: '专题报告',
  full_report: '完整报告',
  clarification: '必要澄清'
}

const timeScopeText = computed(() => {
  const scope = props.result?.understood_request?.time_scope || {}
  if (scope.type === 'recent_years') return `近 ${scope.years} 年`
  if (scope.type === 'date_range') return `${scope.start_date || '-'} 至 ${scope.end_date || '-'}`
  if (scope.type === 'as_of') return `截至 ${scope.end_date || '-'}`
  if (scope.type === 'current') return '当前时点'
  return '最新可用数据'
})
</script>

<template>
  <section v-if="result" class="query-result">
    <div class="understood-card">
      <div>
        <CheckCircle2 size="18" />
        <span>系统理解</span>
      </div>
      <a-tag v-for="topic in result.understood_request?.topics || []" :key="topic" color="blue">
        {{ topicLabels[topic] || topic }}
      </a-tag>
      <span>{{
        presentationLabels[result.understood_request?.presentation] ||
        result.understood_request?.presentation
      }}</span>
      <span>{{ timeScopeText }}</span>
    </div>

    <div class="answer-card">
      <header>
        <span><BookOpenCheck size="18" /> 调查结果</span>
        <a-tag :color="status.color">{{ status.label }}</a-tag>
      </header>
      <p>{{ result.summary }}</p>
      <ul v-if="result.findings?.length" class="findings">
        <li v-for="finding in result.findings" :key="finding">{{ finding }}</li>
      </ul>
      <div class="result-meta">
        <code v-if="result.claim_ledger">Claim {{ result.claim_ledger.claims?.length || 0 }}</code>
        <code v-for="(version, source) in result.projection_versions || {}" :key="source">
          {{ source }}: {{ version }}
        </code>
      </div>
    </div>

    <div v-if="sections.clarification" class="clarification-card">
      <button v-for="candidate in result.candidates || []" :key="candidate.eid" type="button">
        <strong>{{ candidate.name }}</strong>
        <span>{{ candidate.credit_code || candidate.stock_code || '待确认企业' }}</span>
      </button>
      <a-empty
        v-if="!result.candidates?.length"
        description="请在问题中补充企业名称、股票代码或统一社会信用代码，也可以使用左侧可选企业上下文"
      />
    </div>

    <div v-else-if="sections.global" class="global-result-card">
      <header>
        <strong>全局数据结果</strong>
        <code>{{ result.global_result?.data_version }}</code>
      </header>
      <p>数据分母：{{ result.global_result?.denominator || 0 }}</p>
      <div class="global-metrics">
        <span v-for="(value, key) in result.global_result?.metrics || {}" :key="key">
          {{ key }}：{{ value }}
        </span>
      </div>
      <ul v-if="result.global_result?.items?.length">
        <li v-for="item in result.global_result.items" :key="item.key">
          <strong>{{ item.label }}</strong>
          <span>{{ item.value }}</span>
          <small v-if="item.metadata?.enterprise_names?.length">
            {{ item.metadata.enterprise_names.join('、') }}
          </small>
        </li>
      </ul>
      <a-empty v-else description="当前全局统计没有返回可展示条目" />
    </div>

    <a-alert
      v-else-if="sections.report"
      type="info"
      show-icon
      message="报告任务已进入统一调查链路"
      description="可在下方查看进度、章节状态并下载可追溯产物。"
    />

    <a-tabs
      v-else
      :key="result.presentation?.type"
      :default-active-key="defaultTab"
      class="result-tabs"
    >
      <a-tab-pane v-if="sections.facts" key="facts">
        <template #tab
          ><span class="tab-label"
            ><Database size="14" />事实 {{ result.facts?.length || 0 }}</span
          ></template
        >
        <a-empty v-if="!result.facts?.length" description="没有返回与本次意图相关的结构化事实" />
        <div v-else class="fact-table-wrap">
          <table class="fact-table">
            <thead>
              <tr>
                <th>字段</th>
                <th>值</th>
                <th>日期</th>
                <th>置信度</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="fact in result.facts" :key="fact.fact_id">
                <td>{{ fact.fact_type }}</td>
                <td>{{ fact.display_value || fact.normalized_value }} {{ fact.unit || '' }}</td>
                <td>{{ fact.effective_date || '-' }}</td>
                <td>
                  <a-tag :color="fact.confidence === 'low' ? 'orange' : 'green'">{{
                    fact.confidence
                  }}</a-tag>
                </td>
                <td>
                  <a-tooltip :title="sources[fact.source_id]?.table || fact.source_id">
                    <code>{{ fact.source_id }}</code>
                  </a-tooltip>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </a-tab-pane>

      <a-tab-pane v-if="sections.relations" key="relations">
        <template #tab
          ><span class="tab-label"><GitBranch size="14" />关系与路径</span></template
        >
        <RelationPathView :paths="result.paths || []" :relations="result.relations || []" />
      </a-tab-pane>

      <a-tab-pane v-if="sections.evidence" key="evidence">
        <template #tab
          ><span class="tab-label"
            ><FileText size="14" />证据 {{ result.evidence?.length || 0 }}</span
          ></template
        >
        <EvidenceList :evidence="result.evidence || []" />
      </a-tab-pane>

      <a-tab-pane v-if="sections.quality" key="quality">
        <template #tab
          ><span class="tab-label"><ShieldCheck size="14" />质量与耗时</span></template
        >
        <DataQualityPanel
          :data-quality="result.data_quality"
          :latency="result.latency"
          :steps="result.steps"
        />
      </a-tab-pane>

      <a-tab-pane v-if="sections.sources" key="sources" tab="来源清单">
        <div class="source-list">
          <article v-for="source in result.sources || []" :key="source.source_id">
            <code>{{ source.source_id }}</code>
            <span>{{ source.table }} · {{ source.record_key }}</span>
            <small>{{ source.business_date || '业务日期未知' }}</small>
          </article>
          <a-empty v-if="!result.sources?.length" description="没有登记来源" />
        </div>
      </a-tab-pane>
    </a-tabs>
  </section>
</template>

<style scoped lang="less">
.query-result {
  display: grid;
  gap: 16px;
}

.understood-card,
.result-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.understood-card {
  padding: 11px 13px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  color: var(--gray-600);
  font-size: 12px;

  div {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--gray-900);
    font-weight: 650;
  }
}

.answer-card {
  padding: 16px;
  border: 1px solid var(--main-100);
  border-radius: 12px;
  background: linear-gradient(135deg, var(--main-5), var(--gray-0));

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;

    span {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      color: var(--gray-1000);
      font-weight: 650;
    }
  }

  p {
    margin: 12px 0 0;
    color: var(--gray-800);
    line-height: 1.75;
    white-space: pre-wrap;
  }
}

.findings {
  margin: 10px 0 0;
  padding-left: 20px;
  color: var(--gray-700);
}

.result-meta {
  margin-top: 10px;

  code {
    padding: 3px 6px;
    border-radius: 5px;
    background: var(--gray-100);
    color: var(--gray-600);
    font-size: 11px;
  }
}

.clarification-card {
  display: grid;
  gap: 8px;

  button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 11px 12px;
    border: 1px solid var(--gray-200);
    border-radius: 9px;
    background: var(--gray-0);
    color: var(--gray-800);
  }

  span {
    color: var(--gray-500);
    font-size: 12px;
  }
}

.global-result-card {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--main-100);
  border-radius: 10px;
  background: var(--main-5);

  header,
  li,
  .global-metrics {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  header {
    justify-content: space-between;
  }

  p,
  ul {
    margin: 0;
  }

  ul {
    display: grid;
    gap: 7px;
    padding: 0;
    list-style: none;
  }

  li,
  .global-metrics span {
    padding: 7px 9px;
    border-radius: 7px;
    background: var(--gray-0);
  }

  small {
    color: var(--gray-500);
  }
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.fact-table-wrap {
  overflow-x: auto;
}

.fact-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;

  th,
  td {
    padding: 10px;
    border-bottom: 1px solid var(--gray-100);
    text-align: left;
    vertical-align: top;
  }

  th {
    background: var(--gray-50);
    color: var(--gray-700);
    font-weight: 600;
  }

  code {
    display: block;
    max-width: 180px;
    overflow: hidden;
    color: var(--gray-600);
    font-size: 11px;
    text-overflow: ellipsis;
  }
}

.source-list {
  display: grid;
  gap: 8px;

  article {
    display: grid;
    grid-template-columns: minmax(180px, 1.2fr) minmax(160px, 1fr) auto;
    gap: 10px;
    padding: 10px;
    border-bottom: 1px solid var(--gray-100);
    color: var(--gray-700);
  }

  code {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  small {
    color: var(--gray-500);
  }
}

@media (max-width: 760px) {
  .source-list article {
    grid-template-columns: 1fr;
  }
}
</style>
