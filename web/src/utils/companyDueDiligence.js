export const TERMINAL_TASK_STATUSES = new Set(['success', 'failed', 'cancelled'])
export const REAL_DATABASE_CONFIRMATION = 'CONFIRM_READONLY_REAL_DATABASE_LOAD'
const BASE_URL = '/api/company-due-diligence'

export const REPORT_SECTIONS = [
  ['identity', '企业身份'],
  ['business', '经营概况'],
  ['shareholders', '股东与出资'],
  ['relations', '关联关系'],
  ['products', '产品与业务'],
  ['financing', '融资情况'],
  ['financials', '财务指标'],
  ['compliance', '合规情况'],
  ['intellectual_property', '知识产权'],
  ['news', '新闻舆情'],
  ['risks', '风险事项'],
  ['data_quality', '数据质量']
]

export const QUICK_QUESTIONS = [
  '企业基本信息、注册资本、法定代表人和经营状态是什么？',
  '当前股东和出资情况有哪些？',
  '融资情况有哪些？',
  '行政处罚、环保处罚和经营异常风险有哪些？',
  '专利、商标和软件著作权情况如何？'
]

const STATUS_META = {
  available: ['可用', 'success'],
  unavailable: ['来源不可用', 'error'],
  resolved: ['唯一匹配', 'success'],
  ambiguous: ['多候选', 'warning'],
  not_found: ['未找到', 'default'],
  source_unavailable: ['来源不可用', 'error'],
  complete: ['完成', 'success'],
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

export function buildQuestionPayload(company, form) {
  if (!company?.eid) throw new Error('请先确认唯一企业')
  const question = String(form.question || '').trim()
  if (!question) throw new Error('请输入问题')
  return {
    company_ref: { eid: company.eid },
    question,
    mode: 'qa',
    as_of: form.asOf || null,
    hop_limit: Number(form.hopLimit || 3),
    top_k: Number(form.topK || 10),
    include_low_confidence: Boolean(form.includeLowConfidence)
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

export function validatePerformanceForm(form, dataset) {
  const concurrency = Number(form.concurrency)
  const durationSeconds = Number(form.durationSeconds)
  const requestCount = Number(form.requestCount)
  if (!form.scenarios?.length) throw new Error('至少选择一个性能场景')
  if (!companyRefsFromDataset(dataset).length) throw new Error('数据集没有可用于性能评测的企业引用')
  if (form.safetyMode === 'safe_regression') {
    if (concurrency > 3) throw new Error('安全回归模式并发不得超过 3')
    if (durationSeconds > 60) throw new Error('安全回归模式持续时间不得超过 60 秒')
    if (requestCount > 100) throw new Error('安全回归模式请求数不得超过 100')
  } else if (form.confirmation !== REAL_DATABASE_CONFIRMATION) {
    throw new Error(`请输入确认口令 ${REAL_DATABASE_CONFIRMATION}`)
  }
  return {
    scenarios: form.scenarios,
    company_refs: companyRefsFromDataset(dataset),
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
