<script setup>
import { computed } from 'vue'
import { BookOpenCheck, Database, FileText, GitBranch, ShieldCheck } from 'lucide-vue-next'

import DataQualityPanel from './DataQualityPanel.vue'
import EvidenceList from './EvidenceList.vue'
import RelationPathView from './RelationPathView.vue'
import { statusMeta } from '@/utils/companyDueDiligence'

const props = defineProps({ result: { type: Object, default: null } })

const status = computed(() => statusMeta(props.result?.resolution_status))
const sources = computed(() =>
  Object.fromEntries((props.result?.sources || []).map((source) => [source.source_id, source]))
)
</script>

<template>
  <section v-if="result" class="query-result">
    <div class="answer-card">
      <header>
        <span><BookOpenCheck size="18" /> 查询答案</span>
        <a-tag :color="status.color">{{ status.label }}</a-tag>
      </header>
      <p>{{ result.answer }}</p>
      <div v-if="result.projection_versions && Object.keys(result.projection_versions).length" class="versions">
        <code v-for="(version, source) in result.projection_versions" :key="source">{{ source }}: {{ version }}</code>
      </div>
    </div>

    <a-tabs default-active-key="facts" class="result-tabs">
      <a-tab-pane key="facts">
        <template #tab><span class="tab-label"><Database size="14" />事实 {{ result.facts?.length || 0 }}</span></template>
        <a-empty v-if="!result.facts?.length" description="没有返回结构化事实" />
        <div v-else class="fact-table-wrap">
          <table class="fact-table">
            <thead>
              <tr><th>字段</th><th>值</th><th>日期</th><th>置信度</th><th>来源</th></tr>
            </thead>
            <tbody>
              <tr v-for="fact in result.facts" :key="fact.fact_id">
                <td>{{ fact.fact_type }}</td>
                <td>{{ fact.display_value || fact.normalized_value }} {{ fact.unit || '' }}</td>
                <td>{{ fact.effective_date || '-' }}</td>
                <td><a-tag :color="fact.confidence === 'low' ? 'orange' : 'green'">{{ fact.confidence }}</a-tag></td>
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
      <a-tab-pane key="relations">
        <template #tab><span class="tab-label"><GitBranch size="14" />关系与路径</span></template>
        <RelationPathView :paths="result.paths || []" :relations="result.relations || []" />
      </a-tab-pane>
      <a-tab-pane key="evidence">
        <template #tab><span class="tab-label"><FileText size="14" />证据 {{ result.evidence?.length || 0 }}</span></template>
        <EvidenceList :evidence="result.evidence || []" />
      </a-tab-pane>
      <a-tab-pane key="quality">
        <template #tab><span class="tab-label"><ShieldCheck size="14" />质量与耗时</span></template>
        <DataQualityPanel :data-quality="result.data_quality" :latency="result.latency" :steps="result.steps" />
      </a-tab-pane>
      <a-tab-pane key="sources" tab="来源清单">
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

.versions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;

  code {
    padding: 3px 6px;
    border-radius: 5px;
    background: var(--gray-100);
    color: var(--gray-600);
    font-size: 11px;
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
