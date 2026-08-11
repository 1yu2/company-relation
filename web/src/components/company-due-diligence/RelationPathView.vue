<script setup>
import { ArrowRight, GitBranch } from 'lucide-vue-next'

defineProps({
  paths: { type: Array, default: () => [] },
  relations: { type: Array, default: () => [] }
})
</script>

<template>
  <div class="relation-view">
    <a-empty v-if="!paths.length && !relations.length" description="没有返回关联关系" />
    <article v-for="path in paths" :key="path.path_id" class="path-card">
      <header>
        <span><GitBranch size="15" /> {{ path.hop_count }} 跳路径</span>
        <a-tag :color="path.confidence === 'low' ? 'orange' : 'blue'">{{ path.confidence }}</a-tag>
      </header>
      <div class="path-flow">
        <template v-for="(node, index) in path.nodes" :key="`${path.path_id}-${node.entity_id}`">
          <span class="path-node">{{ node.name || node.entity_id }}</span>
          <span v-if="path.edges[index]" class="path-edge">
            <small>{{ path.edges[index].relation_type }}</small>
            <ArrowRight size="16" />
          </span>
        </template>
      </div>
    </article>
    <div v-if="relations.length" class="relation-list">
      <div v-for="relation in relations" :key="relation.relation_id" class="relation-row">
        <code>{{ relation.source_entity_id }}</code>
        <span>{{ relation.relation_type }} →</span>
        <code>{{ relation.target_entity_id }}</code>
        <a-tag :color="relation.confidence === 'low' ? 'orange' : 'default'">{{ relation.confidence }}</a-tag>
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.relation-view,
.relation-list {
  display: grid;
  gap: 10px;
}

.path-card {
  padding: 12px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--gray-0);

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;

    span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--gray-900);
      font-weight: 600;
    }
  }
}

.path-flow {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.path-node {
  flex: 0 0 auto;
  padding: 7px 10px;
  border-radius: 8px;
  background: var(--main-20);
  color: var(--main-800);
}

.path-edge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--gray-600);
}

.relation-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--gray-100);
  overflow-x: auto;

  code {
    color: var(--gray-800);
  }
}
</style>
