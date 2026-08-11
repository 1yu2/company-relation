<script setup>
import { computed, onMounted, ref } from 'vue'
import { DatabaseZap, Eye } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

import { companyDueDiligenceApi } from '@/apis/company_due_diligence_api'
import { describeApiError } from '@/utils/companyDueDiligence'

const emit = defineEmits(['select'])

const datasets = ref([])
const loading = ref(false)
const detail = ref(null)
const detailOpen = ref(false)
const sampleType = ref('')
const reviewStatus = ref('')
const page = ref(1)
const pageSize = 10

const sourceLabel = (kind) =>
  kind === 'akshare_public_baseline' ? 'AkShare 30 家公开基线' : '广东企业 Golden'

const filteredSamples = computed(() =>
  (detail.value?.samples || []).filter((sample) => {
    if (sampleType.value && sample.sample_type !== sampleType.value) return false
    if (reviewStatus.value && sample.review_status !== reviewStatus.value) return false
    return true
  })
)
const pagedSamples = computed(() =>
  filteredSamples.value.slice((page.value - 1) * pageSize, page.value * pageSize)
)

const loadDatasets = async () => {
  loading.value = true
  try {
    const result = await companyDueDiligenceApi.listEvaluationDatasets()
    datasets.value = result.datasets || []
    if (datasets.value[0]) await selectDataset(datasets.value[0].dataset_id, false)
  } catch (error) {
    message.error(describeApiError(error))
  } finally {
    loading.value = false
  }
}

const selectDataset = async (datasetId, open = false) => {
  try {
    detail.value = await companyDueDiligenceApi.getEvaluationDataset(datasetId)
    emit('select', detail.value)
    if (open) detailOpen.value = true
  } catch (error) {
    message.error(describeApiError(error))
  }
}

onMounted(loadDatasets)

defineExpose({ datasets, loadDatasets })
</script>

<template>
  <section class="dataset-panel">
    <div class="panel-title">
      <div><DatabaseZap size="18" /><strong>冻结数据集</strong></div>
      <a-button size="small" :loading="loading" @click="loadDatasets">刷新</a-button>
    </div>
    <a-empty v-if="!loading && !datasets.length" description="暂无冻结数据集" />
    <div class="dataset-grid">
      <article
        v-for="dataset in datasets"
        :key="dataset.dataset_id"
        :class="{ selected: detail?.dataset_id === dataset.dataset_id }"
        @click="selectDataset(dataset.dataset_id)"
      >
        <header>
          <strong>{{ dataset.name }}</strong>
          <a-tag :color="dataset.source_kind === 'akshare_public_baseline' ? 'purple' : 'blue'">
            {{ sourceLabel(dataset.source_kind) }}
          </a-tag>
        </header>
        <p>版本 {{ dataset.version }} · 业务时点 {{ dataset.business_as_of || '未声明' }}</p>
        <code>{{ dataset.checksum }}</code>
        <footer>
          <span>{{ dataset.sample_count }} 个样本</span>
          <span :class="{ unreviewed: dataset.human_review_rate < 1 }">
            人工复核 {{ (dataset.human_review_rate * 100).toFixed(1) }}%
          </span>
          <button type="button" @click.stop="selectDataset(dataset.dataset_id, true)"><Eye size="13" />查看</button>
        </footer>
      </article>
    </div>

    <a-modal v-model:open="detailOpen" width="900px" title="数据集样本" :footer="null">
      <div v-if="detail" class="dataset-detail">
        <div class="detail-filters">
          <a-select v-model:value="sampleType" allow-clear placeholder="样本类型" style="width: 150px">
            <a-select-option value="qa">问答</a-select-option>
            <a-select-option value="report">报告</a-select-option>
          </a-select>
          <a-select v-model:value="reviewStatus" allow-clear placeholder="复核状态" style="width: 210px">
            <a-select-option value="human_verified">人工复核</a-select-option>
            <a-select-option value="source_derived_unreviewed">程序派生未复核</a-select-option>
          </a-select>
        </div>
        <article v-for="sample in pagedSamples" :key="sample.sample_id" class="sample-row">
          <header>
            <strong>{{ sample.sample_id }}</strong>
            <span>
              <a-tag>{{ sample.sample_type }}</a-tag>
              <a-tag :color="sample.review_status === 'human_verified' ? 'green' : 'orange'">
                {{ sample.review_status }}
              </a-tag>
            </span>
          </header>
          <p>{{ sample.qa_request?.question || sample.report_request?.sections?.join('、') }}</p>
          <code>{{ sample.domain }}</code>
        </article>
        <a-pagination
          v-model:current="page"
          :page-size="pageSize"
          :total="filteredSamples.length"
          :show-size-changer="false"
        />
      </div>
    </a-modal>
  </section>
</template>

<style scoped lang="less">
.dataset-panel {
  display: grid;
  gap: 12px;
}

.panel-title,
.panel-title div,
.dataset-grid article header,
.dataset-grid article footer,
.sample-row header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.panel-title div {
  justify-content: flex-start;
  color: var(--main-700);
}

.dataset-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;

  article {
    padding: 12px;
    border: 1px solid var(--gray-200);
    border-radius: 10px;
    background: var(--gray-0);
    cursor: pointer;

    &.selected,
    &:hover {
      border-color: var(--main-200);
      background: var(--main-5);
    }

    p {
      margin: 8px 0;
      color: var(--gray-600);
      font-size: 12px;
    }

    > code {
      display: block;
      overflow: hidden;
      color: var(--gray-500);
      font-size: 10px;
      text-overflow: ellipsis;
    }

    footer {
      margin-top: 9px;
      color: var(--gray-700);
      font-size: 12px;

      .unreviewed {
        color: var(--color-warning-700);
        font-weight: 600;
      }

      button {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        border: 0;
        background: transparent;
        color: var(--main-700);
        cursor: pointer;
      }
    }
  }
}

.dataset-detail,
.sample-row {
  display: grid;
  gap: 10px;
}

.detail-filters {
  display: flex;
  gap: 8px;
}

.sample-row {
  padding: 11px;
  border-bottom: 1px solid var(--gray-100);

  p {
    margin: 0;
    color: var(--gray-700);
  }

  code {
    color: var(--gray-500);
    font-size: 11px;
  }
}

@media (max-width: 760px) {
  .dataset-grid {
    grid-template-columns: 1fr;
  }
}
</style>
