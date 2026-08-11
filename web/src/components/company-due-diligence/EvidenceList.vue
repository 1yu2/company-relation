<script setup>
import { FileSearch } from 'lucide-vue-next'

defineProps({ evidence: { type: Array, default: () => [] } })
</script>

<template>
  <div class="evidence-list">
    <a-empty v-if="!evidence.length" description="没有召回文本证据" />
    <article v-for="item in evidence" :key="item.evidence_id" class="evidence-card">
      <header>
        <span><FileSearch size="15" /> {{ item.document_type }}</span>
        <span>{{ item.published_at || '日期未知' }}</span>
      </header>
      <p>{{ item.text }}</p>
      <footer>
        <code>{{ item.source_id }}</code>
        <a-tag color="blue">相关度 {{ Number(item.score || 0).toFixed(3) }}</a-tag>
      </footer>
    </article>
  </div>
</template>

<style scoped lang="less">
.evidence-list {
  display: grid;
  gap: 10px;
}

.evidence-card {
  padding: 13px 14px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);

  header,
  footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  header {
    color: var(--gray-600);
    font-size: 12px;

    span:first-child {
      display: inline-flex;
      gap: 5px;
      color: var(--gray-900);
      font-weight: 600;
    }
  }

  p {
    margin: 10px 0;
    color: var(--gray-800);
    line-height: 1.7;
    white-space: pre-wrap;
  }

  code {
    overflow: hidden;
    color: var(--gray-600);
    font-size: 11px;
    text-overflow: ellipsis;
  }
}
</style>
