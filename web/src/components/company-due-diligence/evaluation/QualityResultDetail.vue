<script setup>
import { computed, ref } from 'vue'

import { filterQualitySamples, metricPercent } from '@/utils/companyDueDiligence'

const props = defineProps({ report: { type: Object, required: true } })

const errorOnly = ref(false)
const firstError = ref('')
const reviewStatus = ref('')
const domain = ref('')
const page = ref(1)
const pageSize = 12

const strict = computed(() => props.report.strict_metrics || {})
const metricCards = computed(() => [
  ['字段准确率', strict.value.field_accuracy],
  ['关系准确率', strict.value.relation_accuracy],
  ['路径准确率', strict.value.path_accuracy],
  ['证据覆盖率', strict.value.evidence_coverage],
  ['拒答准确率', strict.value.refusal_accuracy],
  ['章节完整率', strict.value.report_completeness],
  ['时效准确率', strict.value.freshness_accuracy],
  ['严格通过率', strict.value.strict_pass_rate]
])
const domains = computed(() => Object.keys(props.report.metrics_by_domain || {}))
const firstErrors = computed(() =>
  [...new Set((props.report.samples || []).map((sample) => sample.metrics?.first_error).filter(Boolean))].sort()
)
const filtered = computed(() =>
  filterQualitySamples(props.report.samples || [], {
    errorOnly: errorOnly.value,
    firstError: firstError.value,
    reviewStatus: reviewStatus.value,
    domain: domain.value
  })
)
const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
</script>

<template>
  <div class="quality-detail">
    <div class="gate-banner" :class="{ passed: report.regression_gate.passed }">
      <strong>严格门禁：{{ report.regression_gate.passed ? '通过' : '未通过' }}</strong>
      <span>{{ report.strategy_label }} · 人工样本 {{ strict.human_verified_count || 0 }} · 幻觉率 {{ (strict.hallucination_rate * 100).toFixed(2) }}%</span>
      <small v-for="reason in report.regression_gate.reasons" :key="reason">{{ reason }}</small>
    </div>

    <div class="metric-grid">
      <article v-for="([label, metric]) in metricCards" :key="label">
        <span>{{ label }}</span>
        <strong>{{ metricPercent(metric) }}</strong>
        <small>{{ metric?.passed || 0 }} / {{ metric?.total || 0 }}</small>
      </article>
    </div>

    <a-collapse>
      <a-collapse-panel key="groups" header="分类指标（后端聚合）">
        <div class="group-tables">
          <table v-for="(groups, title) in {
            '数据域': report.metrics_by_domain,
            '样本类型': report.metrics_by_sample_type,
            '复核状态': report.metrics_by_review_status
          }" :key="title">
            <caption>{{ title }}</caption>
            <tbody>
              <tr v-for="(metrics, name) in groups" :key="name">
                <td>{{ name }}</td>
                <td>{{ metrics.sample_count }} 条</td>
                <td>严格通过 {{ metricPercent(metrics.strict_pass_rate) }}</td>
                <td>幻觉 {{ (metrics.hallucination_rate * 100).toFixed(2) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </a-collapse-panel>
    </a-collapse>

    <div class="sample-filters">
      <a-checkbox v-model:checked="errorOnly">只看错误</a-checkbox>
      <a-select v-model:value="firstError" allow-clear placeholder="首错阶段" style="width: 160px">
        <a-select-option v-for="item in firstErrors" :key="item" :value="item">{{ item }}</a-select-option>
      </a-select>
      <a-select v-model:value="domain" allow-clear placeholder="数据域" style="width: 150px">
        <a-select-option v-for="item in domains" :key="item" :value="item">{{ item }}</a-select-option>
      </a-select>
      <a-select v-model:value="reviewStatus" allow-clear placeholder="复核状态" style="width: 210px">
        <a-select-option value="human_verified">人工复核</a-select-option>
        <a-select-option value="source_derived_unreviewed">程序派生未复核</a-select-option>
      </a-select>
    </div>

    <div class="sample-results">
      <details v-for="sample in paged" :key="sample.sample_id" :class="{ failed: sample.error || !sample.metrics?.strict_passed }">
        <summary>
          <span><strong>{{ sample.sample_id }}</strong> · {{ sample.domain }} · {{ sample.sample_type }}</span>
          <a-tag :color="sample.metrics?.strict_passed ? 'green' : 'red'">
            {{ sample.error || sample.metrics?.first_error || 'passed' }}
          </a-tag>
        </summary>
        <div class="sample-json-grid">
          <section><h4>输入</h4><pre>{{ JSON.stringify(sample.input, null, 2) }}</pre></section>
          <section><h4>Golden</h4><pre>{{ JSON.stringify(sample.expected, null, 2) }}</pre></section>
          <section><h4>预测</h4><pre>{{ JSON.stringify(sample.prediction, null, 2) }}</pre></section>
          <section><h4>差异</h4><pre>{{ JSON.stringify(sample.metrics?.differences || { error: sample.error }, null, 2) }}</pre></section>
        </div>
      </details>
    </div>
    <a-pagination v-model:current="page" :page-size="pageSize" :total="filtered.length" :show-size-changer="false" />
  </div>
</template>

<style scoped lang="less">
.quality-detail {
  display: grid;
  gap: 14px;
}

.gate-banner {
  display: grid;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--color-warning-100);
  border-radius: 10px;
  background: var(--color-warning-10);

  &.passed {
    border-color: var(--color-success-100);
    background: var(--color-success-10);
  }

  span,
  small {
    color: var(--gray-700);
  }
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;

  article {
    display: grid;
    gap: 4px;
    padding: 11px;
    border: 1px solid var(--gray-200);
    border-radius: 9px;
    background: var(--gray-0);
  }

  span,
  small {
    color: var(--gray-600);
    font-size: 12px;
  }

  strong {
    color: var(--gray-1000);
    font-size: 20px;
  }
}

.group-tables {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  caption {
    margin-bottom: 6px;
    color: var(--gray-900);
    font-weight: 600;
    text-align: left;
  }

  td {
    padding: 6px;
    border-bottom: 1px solid var(--gray-100);
  }
}

.sample-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.sample-results {
  display: grid;
  gap: 8px;

  details {
    padding: 10px;
    border: 1px solid var(--gray-200);
    border-radius: 9px;

    &.failed {
      border-color: var(--color-error-100);
    }
  }

  summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
  }
}

.sample-json-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 10px;

  section {
    min-width: 0;
  }

  h4 {
    margin: 0 0 5px;
  }

  pre {
    max-height: 280px;
    overflow: auto;
    padding: 9px;
    border-radius: 7px;
    background: var(--gray-50);
    color: var(--gray-800);
    font-size: 11px;
    white-space: pre-wrap;
  }
}

@media (max-width: 900px) {
  .metric-grid,
  .group-tables {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 600px) {
  .metric-grid,
  .group-tables,
  .sample-json-grid {
    grid-template-columns: 1fr;
  }
}
</style>
