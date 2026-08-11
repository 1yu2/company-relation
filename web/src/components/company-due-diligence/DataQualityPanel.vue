<script setup>
import { computed } from 'vue'
import { AlertTriangle, Clock3 } from 'lucide-vue-next'

import { dataQualityItems, statusMeta } from '@/utils/companyDueDiligence'

const props = defineProps({
  dataQuality: { type: Object, default: () => ({}) },
  latency: { type: Object, default: () => ({}) },
  steps: { type: Array, default: () => [] }
})

const qualityItems = computed(() => dataQualityItems(props.dataQuality))
const sourceStatus = computed(() => statusMeta(props.dataQuality.source_status || 'available'))
const latencyItems = computed(() =>
  Object.entries(props.latency || {}).filter(([, value]) => Number(value) > 0)
)
</script>

<template>
  <div class="quality-grid">
    <section>
      <h4><AlertTriangle size="15" /> 数据质量</h4>
      <a-tag :color="sourceStatus.color">来源：{{ sourceStatus.label }}</a-tag>
      <div v-if="qualityItems.length" class="quality-list">
        <div v-for="([label, values]) in qualityItems" :key="label">
          <strong>{{ label }}</strong>
          <span>{{ values.join('、') }}</span>
        </div>
      </div>
      <a-empty v-else :image="null" description="未发现缺失、过期或冲突声明" />
    </section>
    <section>
      <h4><Clock3 size="15" /> 运行明细</h4>
      <div class="latency-list">
        <span v-for="([name, value]) in latencyItems" :key="name">
          {{ name.replace('_ms', '') }} <strong>{{ Number(value).toFixed(1) }} ms</strong>
        </span>
      </div>
      <div v-if="steps.length" class="step-list">
        <span v-for="step in steps" :key="`${step.source}-${step.operation}`">
          <a-tag :color="step.status === 'unavailable' ? 'red' : step.status === 'no_data' ? 'default' : 'green'">
            {{ step.status }}
          </a-tag>
          {{ step.source }} · {{ step.operation }} · {{ step.item_count }} 条
        </span>
      </div>
    </section>
  </div>
</template>

<style scoped lang="less">
.quality-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;

  section {
    padding: 13px;
    border: 1px solid var(--gray-200);
    border-radius: 10px;
    background: var(--gray-0);
  }

  h4 {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 10px;
    color: var(--gray-900);
  }
}

.quality-list,
.step-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;

  div,
  span {
    color: var(--gray-700);
    font-size: 12px;
  }

  strong {
    margin-right: 8px;
    color: var(--gray-900);
  }
}

.latency-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;

  span {
    display: flex;
    justify-content: space-between;
    padding: 6px 8px;
    border-radius: 6px;
    background: var(--gray-50);
    color: var(--gray-600);
    font-size: 12px;
  }
}

@media (max-width: 760px) {
  .quality-grid {
    grid-template-columns: 1fr;
  }
}
</style>
