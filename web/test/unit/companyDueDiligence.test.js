import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildInvestigationPayload,
  buildCompanySearchUrl,
  companyRefsFromDataset,
  filterQualitySamples,
  dataQualityItems,
  describeApiError,
  FULL_REPORT_SHORTCUT,
  metricPercent,
  presentationSections,
  REAL_DATABASE_CONFIRMATION,
  reportProgress,
  validatePerformanceForm,
  statusMeta
} from '../../src/utils/companyDueDiligence.js'

test('company resolution states distinguish resolved ambiguous and not found', () => {
  assert.deepEqual(statusMeta('resolved'), { label: '唯一匹配', color: 'success' })
  assert.deepEqual(statusMeta('ambiguous'), { label: '多候选', color: 'warning' })
  assert.deepEqual(statusMeta('not_found'), { label: '未找到', color: 'default' })
  assert.deepEqual(statusMeta('source_unavailable'), { label: '来源不可用', color: 'error' })
  assert.deepEqual(statusMeta('global_scope'), { label: '全局统计', color: 'success' })
  assert.deepEqual(statusMeta('needs_context'), { label: '需要企业范围', color: 'warning' })
})

test('investigation payload keeps company context and natural language contract', () => {
  assert.deepEqual(
    buildInvestigationPayload(
      { eid: 'eid-1', name: '测试企业', credit_code: 'credit-1' },
      {
        message: ' 当前股东有哪些？ ',
        startDate: '2023-08-10',
        asOf: '2026-08-10',
        hopLimit: 2,
        topK: 8,
        includeLowConfidence: true
      }
    ),
    {
      company_context: {
        eid: 'eid-1',
        name: '测试企业',
        credit_code: 'credit-1',
        stock_code: null
      },
      question: '当前股东有哪些？',
      start_date: '2023-08-10',
      as_of: '2026-08-10',
      hop_limit: 2,
      top_k: 8,
      include_low_confidence: true
    }
  )
})

test('investigation payload supports report shortcut and rejects empty intent', () => {
  assert.deepEqual(
    buildInvestigationPayload(
      { eid: 'eid-1', name: '测试企业' },
      { shortcut: FULL_REPORT_SHORTCUT, hopLimit: 3, topK: 10 }
    ),
    {
      company_context: { eid: 'eid-1', name: '测试企业', credit_code: null, stock_code: null },
      shortcut: FULL_REPORT_SHORTCUT,
      hop_limit: 3,
      top_k: 10,
      include_low_confidence: false
    }
  )
  assert.deepEqual(buildInvestigationPayload(null, { message: '测试' }), {
    question: '测试',
    hop_limit: 3,
    top_k: 10,
    include_low_confidence: false
  })
  assert.throws(
    () => buildInvestigationPayload({ eid: 'eid-1' }, { message: '   ' }),
    /请输入调查要求/
  )
})

test('presentation routes only relevant result components', () => {
  assert.deepEqual(
    presentationSections({
      presentation: {
        type: 'relation_result',
        components: ['summary', 'paths', 'relations', 'sources']
      }
    }),
    {
      clarification: false,
      global: false,
      report: false,
      facts: false,
      relations: true,
      evidence: false,
      quality: true,
      sources: true
    }
  )
  assert.equal(presentationSections({ presentation: { type: 'topic_report' } }).report, true)
})

test('report progress distinguishes active terminal and failed states', () => {
  assert.deepEqual(
    reportProgress({ task: { status: 'running', progress: 48, message: '运行中' } }),
    {
      status: 'running',
      progress: 48,
      message: '运行中',
      terminal: false
    }
  )
  assert.equal(reportProgress({ task: { status: 'success', progress: 100 } }).terminal, true)
  assert.equal(reportProgress({ task: { status: 'cancelled' } }).terminal, true)
})

test('data quality keeps missing stale conflict and source warnings separate', () => {
  assert.deepEqual(
    dataQualityItems({
      missing_fields: ['legal_representative'],
      stale_fields: ['news'],
      conflicts: ['registered_capital'],
      low_confidence_items: ['path-1'],
      warnings: ['Neo4j unavailable']
    }),
    [
      ['缺失字段', ['legal_representative']],
      ['过期字段', ['news']],
      ['冲突字段', ['registered_capital']],
      ['低置信度', ['path-1']],
      ['警告', ['Neo4j unavailable']]
    ]
  )
})

test('API URL and error mapping preserve query and source unavailable meaning', () => {
  assert.equal(
    buildCompanySearchUrl('广东 测试', 5),
    '/api/company-due-diligence/companies/search?q=%E5%B9%BF%E4%B8%9C+%E6%B5%8B%E8%AF%95&limit=5'
  )
  assert.match(describeApiError({ response: { status: 503 } }), /数据来源当前不可用/)
  assert.equal(describeApiError({ response: { status: 422 }, message: '参数错误' }), '参数错误')
})

test('dataset company references are deduplicated across QA and report samples', () => {
  const dataset = {
    samples: [
      { qa_request: { company_ref: { eid: 'eid-1' } } },
      { report_request: { company_ref: { eid: 'eid-1' } } },
      { qa_request: { company_ref: { name: '企业二' } } }
    ]
  }
  assert.deepEqual(companyRefsFromDataset(dataset), [{ eid: 'eid-1' }, { name: '企业二' }])
})

test('performance form enforces safe limits and real database confirmation', () => {
  assert.throws(
    () =>
      validatePerformanceForm({
        scenarios: ['enterprise_profile'],
        safetyMode: 'safe_regression',
        concurrency: 4,
        requestCount: 5,
        durationSeconds: 30,
        requestTimeoutSeconds: 60
      }),
    /并发不得超过 3/
  )
  assert.throws(
    () =>
      validatePerformanceForm({
        scenarios: ['enterprise_profile'],
        safetyMode: 'controlled_real_database',
        concurrency: 4,
        requestCount: 5,
        durationSeconds: 30,
        requestTimeoutSeconds: 60,
        confirmation: 'wrong'
      }),
    new RegExp(REAL_DATABASE_CONFIRMATION)
  )
  assert.deepEqual(
    validatePerformanceForm({
      scenarios: ['enterprise_profile'],
      safetyMode: 'safe_regression',
      concurrency: 1,
      requestCount: 5,
      durationSeconds: 30,
      requestTimeoutSeconds: 60
    }),
    {
      scenarios: ['enterprise_profile'],
      scope: 'global',
      sampling_policy: 'all_or_stratified',
      safety_mode: 'safe_regression',
      concurrency: 1,
      request_count: 5,
      duration_seconds: 30,
      request_timeout_seconds: 60,
      real_database_confirmation: null
    }
  )
})

test('quality result filters errors and formats backend metric rates', () => {
  const samples = [
    {
      sample_id: 'pass',
      domain: 'identity',
      review_status: 'human_verified',
      metrics: { strict_passed: true }
    },
    {
      sample_id: 'fail',
      domain: 'graph',
      review_status: 'source_derived_unreviewed',
      metrics: { strict_passed: false, first_error: 'graph_projection' }
    }
  ]
  assert.deepEqual(
    filterQualitySamples(samples, { errorOnly: true }).map((item) => item.sample_id),
    ['fail']
  )
  assert.deepEqual(
    filterQualitySamples(samples, { firstError: 'graph_projection' }).map((item) => item.sample_id),
    ['fail']
  )
  assert.equal(metricPercent({ rate: 0.875 }), '87.5%')
  assert.equal(metricPercent({ rate: null }), '不适用')
})
