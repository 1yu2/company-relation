<template>
  <div class="external-data-settings-section">
    <div class="section-title">企业外部数据补数</div>
    <p class="section-description">
      控制企业尽调外部 Provider 的启停、超时、缓存和限流。外部数据仅作为内部事实的补充。
    </p>

    <section class="operations-card">
      <header class="operations-header">
        <div>
          <h4>运行状态</h4>
          <p>查看 Provider 健康、内存缓存统计和最近的脱敏查询审计。</p>
        </div>
        <a-button size="small" :loading="loadingOperations" @click="loadOperations">
          刷新状态
        </a-button>
      </header>

      <div class="cache-summary">
        <div>
          <span>缓存总数</span>
          <strong>{{ cacheStats.total }}</strong>
        </div>
        <div>
          <span>已过期</span>
          <strong>{{ cacheStats.expired }}</strong>
        </div>
        <a-popconfirm
          title="确认清理全部企业外部数据缓存？"
          ok-text="清理"
          cancel-text="取消"
          @confirm="clearCache"
        >
          <a-button size="small" danger :loading="clearingCache">清理缓存</a-button>
        </a-popconfirm>
      </div>

      <div class="provider-list">
        <div v-if="!providerHealth.length" class="empty-state">暂无 Provider 健康状态</div>
        <div v-for="provider in providerHealth" :key="provider.provider_name" class="provider-row">
          <div>
            <strong>{{ provider.provider_name }}</strong>
            <span>{{ provider.supported_topics?.join('、') || '未声明主题' }}</span>
          </div>
          <a-tag :color="statusMeta(provider.status).color">
            {{ statusMeta(provider.status).label }}
          </a-tag>
        </div>
      </div>

      <div class="audit-section">
        <h5>最近查询审计</h5>
        <div v-if="!auditEntries.length" class="empty-state">暂无外部查询记录</div>
        <div v-for="entry in auditEntries" :key="auditKey(entry)" class="audit-row">
          <div class="audit-main">
            <strong>{{ entry.provider_name }}</strong>
            <span>{{ entry.topics?.join('、') || '未指定主题' }}</span>
          </div>
          <div class="audit-result">
            <a-tag :color="statusMeta(entry.status).color">
              {{ statusMeta(entry.status).label }}
            </a-tag>
            <span v-if="entry.error_code">{{ entry.error_code }}</span>
            <span>{{ formatLatency(entry.latency_ms) }}</span>
            <span>{{ formatDateTime(entry.queried_at) }}</span>
          </div>
        </div>
      </div>
    </section>

    <div class="option-list">
      <section v-for="option in configOptions" :key="option.key" class="option-card">
        <header class="option-header">
          <div>
            <h4>{{ option.name }}</h4>
            <p>{{ option.description }}</p>
          </div>
          <div class="option-actions" :class="{ editing: editingKey === option.key }">
            <template v-if="editingKey === option.key">
              <a-button size="small" :disabled="savingOption === option.key" @click="cancelEditing">
                取消
              </a-button>
              <a-button
                type="primary"
                size="small"
                class="save-button"
                :loading="savingOption === option.key"
                @click="saveOption(option)"
              >
                保存
              </a-button>
            </template>
            <a-button v-else size="small" :disabled="Boolean(editingKey)" @click="startEditing(option)">
              编辑
            </a-button>
          </div>
        </header>

        <div v-if="editingKey === option.key" class="option-fields">
          <label v-for="field in option.params.fields" :key="field.key" class="option-field">
            <span class="setting-label">{{ field.label }}</span>
            <a-switch
              v-if="field.type === 'boolean'"
              v-model:checked="draftValue[field.key]"
              checked-children="启用"
              un-checked-children="关闭"
            />
            <a-input-number
              v-else-if="field.type === 'number'"
              v-model:value="draftValue[field.key]"
              :min="0"
              :precision="0"
              style="width: 100%"
            />
            <a-input v-else v-model:value="draftValue[field.key]" allow-clear />
            <small>{{ field.help }}</small>
          </label>
        </div>

        <div v-else class="option-fields option-values">
          <div v-for="field in option.params.fields" :key="field.key" class="option-field">
            <span class="setting-label">{{ field.label }}</span>
            <a-switch
              v-if="field.type === 'boolean'"
              :checked="Boolean(getFieldValue(option, field))"
              disabled
            />
            <span v-else class="field-value">{{ formatFieldValue(getFieldValue(option, field), field) }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { configOptionsApi } from '@/apis/system_api'
import { externalCompanyDataApi } from '@/apis/external_company_data_api'

const configOptions = ref([])
const editingKey = ref('')
const draftValue = ref({})
const savingOption = ref('')
const loadingOperations = ref(false)
const clearingCache = ref(false)
const providerHealth = ref([])
const cacheStats = ref({ total: 0, expired: 0 })
const auditEntries = ref([])

const PROVIDER_STATUS_META = {
  healthy: { label: '健康', color: 'green' },
  degraded: { label: '降级', color: 'orange' },
  not_configured: { label: '未配置', color: 'default' },
  unavailable: { label: '不可用', color: 'red' },
  error: { label: '异常', color: 'red' }
}

const getFieldValue = (option, field) => {
  const value = option.value?.[field.key]
  return value !== undefined && value !== '' ? value : field.default
}

const formatFieldValue = (value, field) => {
  if (value === undefined || value === null || value === '') return '未配置'
  if (field.type === 'boolean') return value ? '启用' : '关闭'
  return String(value)
}

const loadConfigOptions = async () => {
  try {
    const data = await configOptionsApi.getOptions()
    configOptions.value = (data.options || [])
      .filter((option) => option.key === 'external_company_data_opts')
      .map((option) => ({
        ...option,
        value: { ...(option.value || {}) }
      }))
  } catch (error) {
    message.error(error.message || '加载企业外部数据配置失败')
  }
}

const statusMeta = (status) => PROVIDER_STATUS_META[status] || { label: status || '未知', color: 'default' }

const formatLatency = (latencyMs) => `${Number(latencyMs || 0).toFixed(1)} ms`

const formatDateTime = (value) => {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

const auditKey = (entry) =>
  `${entry.provider_name}-${entry.queried_at}-${entry.topics?.join(',') || ''}-${entry.status}`

const loadOperations = async () => {
  loadingOperations.value = true
  try {
    const [healthData, cacheData, auditData] = await Promise.all([
      externalCompanyDataApi.getHealth(),
      externalCompanyDataApi.getCache(),
      externalCompanyDataApi.getAudit()
    ])
    providerHealth.value = healthData.providers || []
    cacheStats.value = cacheData.cache || { total: 0, expired: 0 }
    auditEntries.value = auditData.entries || []
  } catch (error) {
    message.error(error.message || '加载企业外部数据运行状态失败')
  } finally {
    loadingOperations.value = false
  }
}

const clearCache = async () => {
  clearingCache.value = true
  try {
    const data = await externalCompanyDataApi.clearCache()
    message.success(`已清理 ${data.cleared || 0} 条缓存`)
    await loadOperations()
  } catch (error) {
    message.error(error.message || '清理企业外部数据缓存失败')
  } finally {
    clearingCache.value = false
  }
}

const startEditing = (option) => {
  editingKey.value = option.key
  draftValue.value = Object.fromEntries(
    option.params.fields.map((field) => [field.key, getFieldValue(option, field)])
  )
}

const cancelEditing = () => {
  editingKey.value = ''
  draftValue.value = {}
}

const saveOption = async (option) => {
  savingOption.value = option.key
  try {
    const data = await configOptionsApi.updateOption(option.key, draftValue.value)
    Object.assign(option, data.option, {
      value: { ...(data.option.value || {}) }
    })
    cancelEditing()
    message.success('企业外部数据配置已保存')
  } catch (error) {
    message.error(error.message || '保存企业外部数据配置失败')
  } finally {
    savingOption.value = ''
  }
}

onMounted(() => {
  loadConfigOptions()
  loadOperations()
})
</script>

<style lang="less" scoped>
.external-data-settings-section {
  color: var(--gray-900);
}

.section-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
}

.section-description {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.operations-card,
.option-card {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.operations-card {
  margin-top: 12px;
}

.operations-header,
.cache-summary,
.provider-row,
.audit-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.operations-header {
  align-items: flex-start;

  h4,
  p {
    margin: 0;
  }

  h4 {
    font-size: 14px;
    font-weight: 500;
  }

  p {
    margin-top: 3px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.cache-summary {
  justify-content: flex-start;
  margin-top: 16px;
  padding: 12px;
  border-radius: 6px;
  background: var(--gray-50);

  div {
    display: grid;
    min-width: 72px;
    gap: 3px;
  }

  span {
    color: var(--color-text-secondary);
    font-size: 12px;
  }

  strong {
    font-size: 18px;
  }

  :deep(.ant-popconfirm) {
    margin-left: auto;
  }
}

.provider-list,
.audit-section {
  margin-top: 16px;
}

.provider-row,
.audit-row {
  padding: 10px 0;
  border-top: 1px solid var(--gray-100);
}

.provider-row > div,
.audit-main {
  display: grid;
  gap: 3px;

  span {
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.audit-section h5 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 500;
}

.audit-result {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.empty-state {
  padding: 12px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: center;
}

.option-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.option-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;

  h4,
  p {
    margin: 0;
  }

  h4 {
    font-size: 14px;
    font-weight: 500;
  }

  p {
    margin-top: 3px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
}

.option-actions {
  display: flex;
  flex: none;
  gap: 8px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease;
}

.option-card:hover .option-actions,
.option-card:focus-within .option-actions,
.option-actions.editing {
  opacity: 1;
  pointer-events: auto;
}

.option-fields {
  display: grid;
  gap: 12px;
}

.option-field {
  display: grid;
  gap: 6px;

  small {
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.5;
  }
}

.setting-label {
  font-size: 13px;
  font-weight: 500;
}

.field-value {
  min-height: 32px;
  padding: 6px 11px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  color: var(--color-text-secondary);
  line-height: 18px;
}

@media (max-width: 720px) {
  .audit-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .audit-result {
    flex-wrap: wrap;
    justify-content: flex-start;
  }
}
</style>
