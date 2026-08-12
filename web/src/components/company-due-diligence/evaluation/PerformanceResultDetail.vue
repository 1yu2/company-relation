<script setup>
import { computed } from 'vue'

const props = defineProps({
  report: { type: Object, required: true },
  comparison: { type: Object, default: null }
})

const comparisonByScenario = computed(() =>
  Object.fromEntries((props.comparison?.scenarios || []).map((item) => [item.scenario, item]))
)
const globalDimensions = computed(() => [
  ['接口', props.report.global_summary?.by_interface || {}],
  ['意图', props.report.global_summary?.by_intent || {}],
  ['工具链', props.report.global_summary?.by_toolchain || {}],
  ['数据源请求', props.report.global_summary?.by_data_source_requests || {}]
])

const delta = (scenario) => {
  const previous = comparisonByScenario.value[scenario.scenario]?.latency?.p95_ms
  const current = scenario.latency?.p95_ms
  if (previous == null || current == null) return null
  return current - previous
}

const formatDimension = (values) =>
  Object.entries(values)
    .map(([key, value]) => `${key} ${value}`)
    .join(' · ') || '暂无数据'
</script>

<template>
  <div class="performance-detail">
    <div class="performance-meta">
      <span>代码 {{ report.code_version }}</span>
      <span>策略 {{ report.strategy_version }}</span>
      <span>并发 {{ report.request.concurrency }}</span>
      <span>安全模式 {{ report.request.safety_mode }}</span>
      <a-tag :color="report.regression_gate.passed ? 'green' : 'red'">
        门禁{{ report.regression_gate.passed ? '通过' : '未通过' }}
      </a-tag>
    </div>

    <div v-if="report.global_summary" class="global-grid">
      <article>
        <span>全局 P50</span><strong>{{ report.global_summary.latency.p50_ms ?? '-' }} ms</strong>
      </article>
      <article>
        <span>全局 P95</span><strong>{{ report.global_summary.latency.p95_ms ?? '-' }} ms</strong>
      </article>
      <article>
        <span>全局 P99</span><strong>{{ report.global_summary.latency.p99_ms ?? '-' }} ms</strong>
      </article>
      <article>
        <span>吞吐</span
        ><strong>{{ report.global_summary.throughput_rps.toFixed(2) }} req/s</strong>
      </article>
      <article>
        <span>错误率</span
        ><strong>{{ (report.global_summary.error_rate * 100).toFixed(2) }}%</strong>
      </article>
      <article>
        <span>超时率</span
        ><strong>{{ (report.global_summary.timeout_rate * 100).toFixed(2) }}%</strong>
      </article>
    </div>

    <div v-if="report.global_summary" class="dimension-grid">
      <article v-for="[label, values] in globalDimensions" :key="label">
        <span>{{ label }}</span
        ><strong>{{ formatDimension(values) }}</strong>
      </article>
    </div>

    <div class="scenario-grid">
      <article v-for="scenario in report.scenarios" :key="scenario.scenario">
        <header>
          <strong>{{ scenario.scenario }}</strong>
          <a-tag
            :color="
              scenario.status === 'skipped' ? 'default' : scenario.target_met ? 'green' : 'red'
            "
          >
            {{ scenario.status }}
          </a-tag>
        </header>
        <p v-if="scenario.reason">{{ scenario.reason }}</p>
        <div class="latency-stats">
          <span
            >P50 <strong>{{ scenario.latency.p50_ms ?? '-' }} ms</strong></span
          >
          <span
            >P95 <strong>{{ scenario.latency.p95_ms ?? '-' }} ms</strong></span
          >
          <span
            >P99 <strong>{{ scenario.latency.p99_ms ?? '-' }} ms</strong></span
          >
          <span
            >最大 <strong>{{ scenario.latency.max_ms ?? '-' }} ms</strong></span
          >
        </div>
        <footer>
          <span>{{ scenario.throughput_rps.toFixed(2) }} req/s</span>
          <span>错误 {{ (scenario.error_rate * 100).toFixed(2) }}%</span>
          <span>超时 {{ (scenario.timeout_rate * 100).toFixed(2) }}%</span>
          <span v-if="delta(scenario) != null" :class="{ regression: delta(scenario) > 0 }">
            对比 P95 {{ delta(scenario) > 0 ? '+' : '' }}{{ delta(scenario).toFixed(2) }} ms
          </span>
        </footer>
        <details>
          <summary>阶段耗时与逐请求</summary>
          <div class="stage-grid">
            <span v-for="(value, stage) in scenario.average_stage_latency" :key="stage">
              {{ stage }} <strong>{{ Number(value).toFixed(2) }} ms</strong>
            </span>
          </div>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>冷热</th>
                <th>状态</th>
                <th>总耗时</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="request in scenario.requests.slice(0, 30)" :key="request.request_index">
                <td>{{ request.request_index }}</td>
                <td>{{ request.cache_phase }}</td>
                <td>{{ request.status }}</td>
                <td>{{ request.duration_ms }} ms</td>
                <td>{{ request.error || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </details>
      </article>
    </div>

    <div class="pool-grid">
      <article>
        <span>池大小</span><strong>{{ report.pool.after.size }}</strong>
      </article>
      <article>
        <span>结束签出</span><strong>{{ report.pool.after.checked_out }}</strong>
      </article>
      <article>
        <span>结束溢出</span><strong>{{ report.pool.after.overflow }}</strong>
      </article>
      <article>
        <span>等待增量</span><strong>{{ report.pool.wait_ms_delta }} ms</strong>
      </article>
      <article>
        <span>超时增量</span><strong>{{ report.pool.timeout_delta }}</strong>
      </article>
      <article>
        <span>最大 RSS</span><strong>{{ report.resources_after.max_rss_mb }} MB</strong>
      </article>
      <article>
        <span>Tasker 排队</span
        ><strong>{{ report.runtime_observations?.tasker_queue_wait_ms ?? 0 }} ms</strong>
      </article>
      <article>
        <span>Tasker worker</span
        ><strong>{{ report.runtime_observations?.tasker_worker_count ?? '-' }}</strong>
      </article>
    </div>
  </div>
</template>

<style scoped lang="less">
.performance-detail {
  display: grid;
  gap: 14px;
}

.performance-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  span {
    padding: 5px 8px;
    border-radius: 7px;
    background: var(--gray-50);
    color: var(--gray-700);
    font-size: 12px;
  }
}

.global-grid,
.dimension-grid,
.pool-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.dimension-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));

  article {
    display: grid;
    gap: 4px;
    padding: 10px;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
  }

  span {
    color: var(--gray-600);
    font-size: 11px;
  }

  strong {
    color: var(--gray-800);
    font-size: 12px;
    word-break: break-word;
  }
}

.global-grid article {
  display: grid;
  gap: 4px;
  padding: 10px;
  border: 1px solid var(--main-100);
  border-radius: 8px;
  background: var(--main-5);

  span {
    color: var(--gray-600);
    font-size: 11px;
  }
}

.scenario-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;

  article {
    padding: 12px;
    border: 1px solid var(--gray-200);
    border-radius: 10px;
  }

  header,
  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }

  p,
  footer {
    color: var(--gray-600);
    font-size: 12px;
  }

  footer .regression {
    color: var(--color-error-700);
  }
}

.latency-stats,
.stage-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 7px;
  margin: 10px 0;

  span {
    display: grid;
    gap: 3px;
    padding: 7px;
    border-radius: 7px;
    background: var(--gray-50);
    color: var(--gray-600);
    font-size: 11px;
  }

  strong {
    color: var(--gray-900);
  }
}

details {
  margin-top: 10px;

  summary {
    color: var(--main-700);
    cursor: pointer;
  }

  table {
    width: 100%;
    margin-top: 8px;
    border-collapse: collapse;
    font-size: 11px;
  }

  th,
  td {
    padding: 5px;
    border-bottom: 1px solid var(--gray-100);
    text-align: left;
  }
}

.pool-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;

  article {
    display: grid;
    gap: 3px;
    padding: 10px;
    border-radius: 8px;
    background: var(--main-5);
  }

  span {
    color: var(--gray-600);
    font-size: 11px;
  }
}

@media (max-width: 900px) {
  .scenario-grid {
    grid-template-columns: 1fr;
  }

  .pool-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 600px) {
  .latency-stats,
  .stage-grid,
  .dimension-grid,
  .pool-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
