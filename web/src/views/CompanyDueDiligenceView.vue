<script setup>
import { ref } from 'vue'
import { FlaskConical } from 'lucide-vue-next'

import DatasetPanel from '@/components/company-due-diligence/evaluation/DatasetPanel.vue'
import PerformanceRunPanel from '@/components/company-due-diligence/evaluation/PerformanceRunPanel.vue'
import QualityRunPanel from '@/components/company-due-diligence/evaluation/QualityRunPanel.vue'

const dataset = ref(null)
</script>

<template>
  <main class="evaluation-page">
    <header class="page-hero">
      <div><FlaskConical size="27" /></div>
      <section>
        <h1>企业尽调评测工作台</h1>
        <p>
          企业调查、关系查询和尽调报告统一从主聊天窗口调用“企业尽调智能体”；本页面只保留全局质量与性能评测。
        </p>
      </section>
    </header>

    <section class="evaluation-card"><DatasetPanel @select="dataset = $event" /></section>
    <section class="evaluation-card">
      <a-tabs default-active-key="quality">
        <a-tab-pane key="quality" tab="质量评测"><QualityRunPanel :dataset="dataset" /></a-tab-pane>
        <a-tab-pane key="performance" tab="性能评测"
          ><PerformanceRunPanel :dataset="dataset"
        /></a-tab-pane>
      </a-tabs>
    </section>
  </main>
</template>

<style scoped lang="less">
.evaluation-page {
  display: grid;
  gap: 18px;
  min-height: 100%;
  padding: var(--page-padding);
  background: var(--gray-25);
}

.page-hero {
  display: flex;
  align-items: center;
  gap: 13px;

  > div {
    display: grid;
    width: 46px;
    height: 46px;
    border-radius: 13px;
    background: var(--color-accent-50);
    color: var(--color-accent-700);
    place-items: center;
  }

  h1 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 24px;
  }

  p {
    margin: 4px 0 0;
    color: var(--gray-600);
  }
}

.evaluation-card {
  padding: 18px;
  border: 1px solid var(--gray-200);
  border-radius: 14px;
  background: var(--gray-0);
  box-shadow: 0 4px 18px var(--shadow-1);
}
</style>
