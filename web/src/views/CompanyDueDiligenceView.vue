<script setup>
import { ref } from 'vue'
import { Building2, FileStack, SearchCheck } from 'lucide-vue-next'
import { message } from 'ant-design-vue'

import { companyDueDiligenceApi } from '@/apis/company_due_diligence_api'
import CompanyResolver from '@/components/company-due-diligence/CompanyResolver.vue'
import QueryResult from '@/components/company-due-diligence/QueryResult.vue'
import QuestionPanel from '@/components/company-due-diligence/QuestionPanel.vue'
import ReportWorkspace from '@/components/company-due-diligence/ReportWorkspace.vue'
import { buildInvestigationPayload, describeApiError } from '@/utils/companyDueDiligence'

const company = ref(null)
const investigationResult = ref(null)
const querying = ref(false)

const handleResolved = (resolvedCompany) => {
  company.value = resolvedCompany
  investigationResult.value = null
}

const investigateCompany = async (form) => {
  querying.value = true
  try {
    investigationResult.value = await companyDueDiligenceApi.investigateCompany(
      buildInvestigationPayload(company.value, form)
    )
  } catch (error) {
    message.error(describeApiError(error))
  } finally {
    querying.value = false
  }
}
</script>

<template>
  <main class="due-diligence-page">
    <header class="page-hero">
      <div class="hero-icon"><Building2 size="28" /></div>
      <div>
        <h1>企业尽调工作台</h1>
        <p>确认企业后直接描述调查要求，系统自动拆解意图、调用工具并选择输出形式。</p>
      </div>
    </header>

    <div class="workspace-grid">
      <aside class="workspace-card resolver-pane">
        <CompanyResolver @resolved="handleResolved" />
      </aside>

      <section class="workspace-card query-pane">
        <div class="panel-heading">
          <SearchCheck size="18" />
          <h2>智能调查</h2>
        </div>
        <QuestionPanel :disabled="!company" :loading="querying" @submit="investigateCompany" />
      </section>
    </div>

    <section v-if="querying" class="workspace-card loading-card">
      <a-spin size="large" tip="正在理解意图并执行受控调查工具…" />
    </section>
    <section v-else-if="investigationResult" class="workspace-card">
      <QueryResult :result="investigationResult" />
    </section>

    <section class="workspace-card">
      <div class="panel-heading">
        <FileStack size="18" />
        <h2>报告任务</h2>
      </div>
      <ReportWorkspace :investigation="investigationResult" />
    </section>
  </main>
</template>

<style scoped lang="less">
.due-diligence-page {
  display: grid;
  gap: 18px;
  min-height: 100%;
  padding: var(--page-padding);
  background: var(--gray-25);
}

.page-hero {
  display: flex;
  align-items: center;
  gap: 14px;

  h1 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 24px;
  }

  p {
    margin: 5px 0 0;
    color: var(--gray-600);
  }
}

.hero-icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 13px;
  background: var(--main-50);
  color: var(--main-700);
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(300px, 0.85fr) minmax(460px, 1.65fr);
  gap: 18px;
}

.workspace-card {
  padding: 18px;
  border: 1px solid var(--gray-200);
  border-radius: 14px;
  background: var(--gray-0);
  box-shadow: 0 4px 18px var(--shadow-1);
}

.panel-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  color: var(--main-700);

  h2 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 17px;
  }
}

.loading-card {
  display: grid;
  min-height: 180px;
  place-items: center;
}

@media (max-width: 1000px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 600px) {
  .due-diligence-page {
    gap: 12px;
  }

  .workspace-card {
    padding: 14px;
  }

  .page-hero h1 {
    font-size: 20px;
  }
}
</style>
