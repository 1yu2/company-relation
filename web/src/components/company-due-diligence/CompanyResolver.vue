<script setup>
import { computed, ref } from 'vue'
import { Building2, CheckCircle2, Search } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

import { companyDueDiligenceApi } from '@/apis/company_due_diligence_api'
import { describeApiError, statusMeta } from '@/utils/companyDueDiligence'

const emit = defineEmits(['resolved'])

const query = ref('')
const loading = ref(false)
const candidates = ref([])
const resolutionStatus = ref(null)
const selectedCompany = ref(null)

const status = computed(() => statusMeta(resolutionStatus.value))

const searchCompanies = async () => {
  const keyword = query.value.trim()
  if (keyword.length < 2) {
    message.warning('请输入至少 2 个字符')
    return
  }
  loading.value = true
  selectedCompany.value = null
  try {
    const result = await companyDueDiligenceApi.searchCompanies(keyword)
    candidates.value = result.candidates || []
    resolutionStatus.value = result.status
    if (candidates.value.length === 1) await selectCandidate(candidates.value[0])
  } catch (error) {
    candidates.value = []
    resolutionStatus.value = error?.response?.status === 503 ? 'source_unavailable' : 'failed'
    message.error(describeApiError(error))
  } finally {
    loading.value = false
  }
}

const selectCandidate = async (candidate) => {
  loading.value = true
  try {
    const result = await companyDueDiligenceApi.resolveCompany({ eid: candidate.eid })
    resolutionStatus.value = result.status
    selectedCompany.value = result.company || null
    if (selectedCompany.value) emit('resolved', selectedCompany.value)
  } catch (error) {
    resolutionStatus.value = error?.response?.status === 503 ? 'source_unavailable' : 'failed'
    message.error(describeApiError(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="resolver-card">
    <div class="section-title">
      <Building2 size="18" />
      <div>
        <h2>确认企业</h2>
        <p>按企业名称、统一社会信用代码或股票代码搜索。</p>
      </div>
    </div>

    <a-input-search
      v-model:value="query"
      size="large"
      allow-clear
      :loading="loading"
      placeholder="输入企业名称、信用代码或股票代码"
      @search="searchCompanies"
      @press-enter="searchCompanies"
    >
      <template #enterButton><Search size="17" /></template>
    </a-input-search>

    <div v-if="resolutionStatus" class="resolver-status">
      <a-tag :color="status.color">{{ status.label }}</a-tag>
      <span v-if="resolutionStatus === 'ambiguous'">请选择正确的企业候选。</span>
      <span v-else-if="resolutionStatus === 'not_found'">没有找到匹配企业。</span>
      <span v-else-if="resolutionStatus === 'source_unavailable'">企业主数据当前不可用。</span>
    </div>

    <div v-if="selectedCompany" class="selected-company">
      <CheckCircle2 size="18" />
      <div>
        <strong>{{ selectedCompany.name }}</strong>
        <span>{{ selectedCompany.credit_code || '无信用代码' }} · {{ selectedCompany.stock_code || '非上市企业' }}</span>
      </div>
    </div>

    <div v-else-if="candidates.length > 1" class="candidate-list">
      <button
        v-for="candidate in candidates"
        :key="candidate.eid"
        type="button"
        class="candidate-item"
        @click="selectCandidate(candidate)"
      >
        <span>
          <strong>{{ candidate.name }}</strong>
          <small>{{ candidate.credit_code || candidate.stock_code || candidate.eid }}</small>
        </span>
        <a-tag :color="candidate.confidence === 'high' ? 'green' : 'orange'">
          {{ candidate.confidence }}
        </a-tag>
      </button>
    </div>
  </section>
</template>

<style scoped lang="less">
.resolver-card {
  display: grid;
  gap: 16px;
}

.section-title {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  color: var(--main-700);

  h2 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 17px;
  }

  p {
    margin: 3px 0 0;
    color: var(--gray-600);
    font-size: 13px;
  }
}

.resolver-status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--gray-700);
  font-size: 13px;
}

.selected-company {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--color-success-100);
  border-radius: 10px;
  background: var(--color-success-10);
  color: var(--color-success-700);

  div {
    display: grid;
    gap: 2px;
  }

  strong {
    color: var(--gray-1000);
  }

  span {
    color: var(--gray-600);
    font-size: 12px;
  }
}

.candidate-list {
  display: grid;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
}

.candidate-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 11px 12px;
  border: 1px solid var(--gray-200);
  border-radius: 9px;
  background: var(--gray-0);
  text-align: left;
  cursor: pointer;

  &:hover {
    border-color: var(--main-200);
    background: var(--main-5);
  }

  span {
    display: grid;
    gap: 3px;
  }

  small {
    color: var(--gray-600);
  }
}
</style>
