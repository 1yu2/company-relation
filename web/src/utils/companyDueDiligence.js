export const TERMINAL_TASK_STATUSES = new Set(['success', 'failed', 'cancelled'])
export const REAL_DATABASE_CONFIRMATION = 'CONFIRM_READONLY_REAL_DATABASE_LOAD'
const BASE_URL = '/api/company-due-diligence'

export const QUICK_QUESTIONS = [
  '当前数据覆盖哪些企业？',
  '当前数据中哪些企业存在共同股东关系？',
  '企业基本信息、注册资本、法定代表人和经营状态是什么？',
  '当前股东和出资情况有哪些？',
  '该企业有哪些关联关系和最多三跳路径？',
  '融资情况有哪些？',
  '近三年的行政处罚、环保处罚和经营异常风险有哪些？',
  '专利、商标和软件著作权情况如何？'
]

export const FULL_REPORT_SHORTCUT = 'generate_full_due_diligence_report'

const STATUS_META = {
  available: ['可用', 'success'],
  unavailable: ['来源不可用', 'error'],
  resolved: ['唯一匹配', 'success'],
  ambiguous: ['多候选', 'warning'],
  not_found: ['未找到', 'default'],
  source_unavailable: ['来源不可用', 'error'],
  global_scope: ['全局统计', 'success'],
  needs_context: ['需要企业范围', 'warning'],
  complete: ['完成', 'success'],
  accepted: ['任务已接受', 'processing'],
  clarification: ['需要确认', 'warning'],
  partial: ['部分完成', 'warning'],
  no_data: ['无数据', 'default'],
  stale: ['数据过期', 'warning'],
  conflicted: ['数据冲突', 'error'],
  failed: ['失败', 'error'],
  cancelled: ['已取消', 'default'],
  pending: ['排队中', 'processing'],
  queued: ['排队中', 'processing'],
  running: ['运行中', 'processing'],
  success: ['已完成', 'success']
}

export function statusMeta(status) {
  const [label, color] = STATUS_META[status] || [status || '未知', 'default']
  return { label, color }
}

export function buildCompanySearchUrl(query, limit = 10) {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return `${BASE_URL}/companies/search?${params}`
}

export function buildInvestigationPayload(company, form) {
  const message = String(form.message || '').trim()
  const shortcut = form.shortcut || null
  if (!message && !shortcut) throw new Error('请输入调查要求')

  const payload = {
    hop_limit: Number(form.hopLimit || 3),
    top_k: Number(form.topK || 10),
    include_low_confidence: Boolean(form.includeLowConfidence)
  }
  if (company?.eid || company?.name || company?.credit_code || company?.stock_code) {
    payload.company_context = {
      eid: company.eid || null,
      name: company.name || null,
      credit_code: company.credit_code || null,
      stock_code: company.stock_code || null
    }
  }
  if (message) payload.question = message
  if (shortcut) payload.shortcut = shortcut
  if (form.startDate) payload.start_date = form.startDate
  if (form.asOf) payload.as_of = form.asOf
  return payload
}

export function presentationSections(result = {}) {
  const type = result.presentation?.type
  const components = new Set(result.presentation?.components || [])
  return {
    clarification:
      type === 'clarification' ||
      ['needs_context', 'not_found', 'source_unavailable'].includes(result.status),
    global: result.status === 'global_scope' || Boolean(result.global_result),
    report: type === 'topic_report' || type === 'full_report',
    facts: components.has('facts') || components.has('fact_table') || components.has('findings'),
    relations: components.has('relations') || components.has('paths'),
    evidence: components.has('evidence'),
    quality:
      components.has('data_quality') ||
      !['clarification', 'topic_report', 'full_report'].includes(type),
    sources: components.has('sources')
  }
}

export function reportProgress(detail) {
  const task = detail?.task || {}
  return {
    status: task.status || 'pending',
    progress: Number(task.progress || 0),
    message: task.message || '',
    terminal: TERMINAL_TASK_STATUSES.has(task.status)
  }
}

export function dataQualityItems(dataQuality = {}) {
  return [
    ['缺失字段', dataQuality.missing_fields || []],
    ['过期字段', dataQuality.stale_fields || []],
    ['冲突字段', dataQuality.conflicts || []],
    ['低置信度', dataQuality.low_confidence_items || []],
    ['警告', dataQuality.warnings || []]
  ].filter(([, values]) => values.length)
}

export function describeApiError(error) {
  const status = error?.response?.status
  if (status === 503) return '数据来源当前不可用，请稍后重试或检查服务状态。'
  if (status === 409) return error.message || '任务状态已变化，请刷新后重试。'
  if (status === 422) return error.message || '输入参数不符合查询要求。'
  return error?.message || '请求失败，请稍后重试。'
}

export function companyRefsFromDataset(dataset = {}) {
  const refs = []
  const seen = new Set()
  for (const sample of dataset.samples || []) {
    const reference = sample.qa_request?.company_ref || sample.report_request?.company_ref
    if (!reference) continue
    const key = JSON.stringify(reference)
    if (!seen.has(key)) {
      seen.add(key)
      refs.push(reference)
    }
  }
  return refs.slice(0, 20)
}

export function validatePerformanceForm(form) {
  const concurrency = Number(form.concurrency)
  const durationSeconds = Number(form.durationSeconds)
  const requestCount = Number(form.requestCount)
  if (!form.scenarios?.length) throw new Error('至少选择一个性能场景')
  if (form.safetyMode === 'safe_regression') {
    if (concurrency > 3) throw new Error('安全回归模式并发不得超过 3')
    if (durationSeconds > 60) throw new Error('安全回归模式持续时间不得超过 60 秒')
    if (requestCount > 100) throw new Error('安全回归模式请求数不得超过 100')
  } else if (form.confirmation !== REAL_DATABASE_CONFIRMATION) {
    throw new Error(`请输入确认口令 ${REAL_DATABASE_CONFIRMATION}`)
  }
  return {
    scenarios: form.scenarios,
    scope: 'global',
    sampling_policy: 'all_or_stratified',
    safety_mode: form.safetyMode,
    concurrency,
    request_count: requestCount,
    duration_seconds: durationSeconds,
    request_timeout_seconds: Number(form.requestTimeoutSeconds),
    real_database_confirmation:
      form.safetyMode === 'controlled_real_database' ? form.confirmation : null
  }
}

export function filterQualitySamples(samples = [], filters = {}) {
  return samples.filter((sample) => {
    if (filters.errorOnly && !sample.error && sample.metrics?.strict_passed) return false
    if (filters.firstError && sample.metrics?.first_error !== filters.firstError) return false
    if (filters.reviewStatus && sample.review_status !== filters.reviewStatus) return false
    if (filters.domain && sample.domain !== filters.domain) return false
    return true
  })
}

export function metricPercent(metric) {
  return metric?.rate == null ? '不适用' : `${(Number(metric.rate) * 100).toFixed(1)}%`
}
