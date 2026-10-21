import assert from 'node:assert/strict'
import test from 'node:test'
import * as evaluationView from '../../src/utils/evaluationView.js'

import {
  buildLatencyRows,
  buildMetricRows,
  buildRolloutRows,
  experimentIdentity,
  formatPValue,
  formatPercent,
  formatSigned,
  reasonLabel,
  taskSuccessStat
} from '../../src/utils/evaluationView.js'

const tasksById = new Map([
  [
    'task-b',
    {
      task_id: 'task-b',
      task_key: 'py-task-002',
      input: { query: '统计 token 数' },
      expected: { final_answer: { value: '5' } }
    }
  ],
  [
    'task-a',
    {
      task_id: 'task-a',
      task_key: 'py-task-001',
      input: { query: '平方求和' },
      expected: { final_answer: { value: '211' } }
    }
  ]
])

test('逐题结果按题号排序，失败原因取 task_pass 主因，并带出回答、工具和用量', () => {
  const experiment = {
    rollouts: [
      {
        rollout_id: 'r-b',
        task_id: 'task-b',
        attempt: 1,
        status: 'COMPLETED',
        actual_output: {
          final_answer: '```\n5\n```',
          tool_calls: [{ name: 'execute' }, { name: 'execute' }],
          usage: { total: 52000 },
          latency: { total_ms: 6200 }
        }
      },
      {
        rollout_id: 'r-a',
        task_id: 'task-a',
        attempt: 1,
        status: 'COMPLETED',
        actual_output: { final_answer: '211' }
      }
    ],
    metrics: [
      {
        rollout_id: 'r-b',
        metric_name: 'task_pass',
        status: 'failed',
        reason_code: 'answer_format_violation'
      },
      { rollout_id: 'r-b', metric_name: 'answer_value', status: 'passed', reason_code: null },
      { rollout_id: 'r-a', metric_name: 'task_pass', status: 'passed', reason_code: null }
    ]
  }

  const rows = buildRolloutRows(experiment, tasksById)

  assert.deepEqual(
    rows.map((row) => row.taskKey),
    ['py-task-001', 'py-task-002']
  )
  const failed = rows[1]
  assert.equal(failed.verdict, 'failed')
  assert.equal(failed.reason, 'answer_format_violation')
  assert.equal(failed.expected, '5')
  assert.deepEqual(failed.toolNames, ['execute'])
  assert.equal(failed.tokens, 52000)
  assert.equal(failed.latencyMs, 6200)
  assert.equal(failed.metrics.answer_value.status, 'passed')
})

test('P1 指标使用中文名，检索层排在结果层之后和轨迹层之前', () => {
  const rows = buildMetricRows(
    Object.fromEntries(
      [
        'tool_error_rate',
        'ndcg_at_k',
        'mrr',
        'recall_at_k',
        'hit_at_k',
        'answer_correct',
        'judge_correct',
        'failure_reported',
        'fault_injected'
      ].map((name) => [name, { status_counts: {} }])
    )
  )
  assert.deepEqual(
    rows.map((row) => row.layer),
    [
      'result',
      'result',
      'result',
      'retrieval',
      'retrieval',
      'retrieval',
      'retrieval',
      'trajectory',
      'trajectory'
    ]
  )
  for (const row of rows) assert.match(row.label, /[\u4e00-\u9fff]/)
  for (const code of [
    'failure_not_reported',
    'answer_point_missing',
    'answer_reference_missing',
    'relevant_chunks_missing',
    'retrieval_not_called',
    'retrieval_miss',
    'ranking_miss',
    'generation_error',
    'judge_failed',
    'judge_output_invalid',
    'judge_not_calibrated'
  ])
    assert.match(reasonLabel(code), /[\u4e00-\u9fff]/)
})

test('仅有故障注入样本时展示恢复率，恢复为零仍需显示', () => {
  assert.equal(evaluationView.faultRecoveryStat(undefined), null)
  assert.equal(evaluationView.faultRecoveryStat({ injected: 0, recovered: 0, rate: null }), null)
  assert.deepEqual(evaluationView.faultRecoveryStat({ injected: 5, recovered: 5, rate: 1 }), {
    label: '失败恢复率',
    value: '5/5',
    hint: '100%'
  })
  assert.deepEqual(evaluationView.faultRecoveryStat({ injected: 5, recovered: 0, rate: 0 }), {
    label: '失败恢复率',
    value: '0/5',
    hint: '0%'
  })
})

test('task_pass 中超过半数因 Judge 未校准才显示校准说明，不称为评测侧错误', () => {
  const aggregate = { 'pass@1': 1, measured_task_count: 1, not_measured_tasks: 2 }
  const counts = { passed: 1, not_measured: 2 }
  const stat = taskSuccessStat(aggregate, 1, counts, { judge_not_calibrated: 2 })
  assert.equal(stat.notice, 'LLM Judge 尚未完成人工校准，开放式题不计入通过率')
  assert.doesNotMatch(stat.hint, /评测侧错误/)
  assert.equal(taskSuccessStat(aggregate, 1, counts, { judge_not_calibrated: 1 }).notice, undefined)
  assert.equal(
    taskSuccessStat(aggregate, 1, { passed: 2, not_measured: 2 }, { judge_not_calibrated: 2 })
      .notice,
    undefined
  )
  assert.equal(taskSuccessStat(aggregate, 1, {}, {}).notice, undefined)
})

test('runner 失败的题没有评分时，用 rollout 状态和 runner 原因展示，而不是显示为通过', () => {
  const rows = buildRolloutRows(
    {
      rollouts: [
        {
          rollout_id: 'r',
          task_id: 'task-a',
          attempt: 1,
          status: 'TIMEOUT',
          reason_code: 'timeout',
          duration_ms: 300000,
          actual_output: null
        }
      ],
      metrics: []
    },
    tasksById
  )

  assert.equal(rows[0].verdict, 'TIMEOUT')
  assert.equal(rows[0].reason, 'timeout')
  assert.equal(rows[0].latencyMs, 300000)
  assert.deepEqual(rows[0].toolCalls, [])
})

test('指标明细先结果层后轨迹层，不重复展示 task_pass，也不把派生原因当成失败原因', () => {
  const rows = buildMetricRows({
    loop_detected: { pass_rate: 1, status_counts: { passed: 16 }, reason_counts: {} },
    task_pass: { pass_rate: 0.875, status_counts: { passed: 14, failed: 2 }, reason_counts: {} },
    answer_format: {
      pass_rate: 0.875,
      status_counts: { passed: 14, failed: 2 },
      reason_counts: { answer_format_violation: 2, answer_value_incorrect: 1 }
    },
    tool_safety: { pass_rate: 1, status_counts: { passed: 16 }, reason_counts: {} }
  })

  assert.deepEqual(
    rows.map((row) => row.name),
    ['answer_format', 'tool_safety', 'loop_detected']
  )
  assert.deepEqual(rows[0].reasons, [{ label: '格式不符（值正确）', count: 2 }])
  assert.equal(rows[2].layer, 'trajectory')
})

test('耗时只展示有样本的分布，旧实验没有快照时退回实验字段', () => {
  const rows = buildLatencyRows({
    latency_ms: {
      agent_total: { sample_count: 16, p50: 5900, p95: null, max: 7100 },
      agent_queue: { sample_count: 0 },
      agent_execution: { sample_count: 16, p50: 5600, p95: null, max: 6900 }
    }
  })
  assert.deepEqual(
    rows.map((row) => row.label),
    ['端到端', '执行']
  )

  const legacy = experimentIdentity({
    agent_slug: 'default-chatbot',
    config_snapshot: { note: '旧实验' }
  })
  assert.equal(legacy.agent, 'default-chatbot')
  assert.equal(legacy.model, '未记录')
  assert.deepEqual(legacy.evaluators, [])
})

test('数值格式：百分比去掉多余小数，p 值过小时不显示虚假精度，变化量带方向', () => {
  assert.equal(formatPercent(0.875), '87.5%')
  assert.equal(formatPercent(1), '100%')
  assert.equal(formatPercent(null), '—')
  assert.equal(formatPValue(1), '1.000')
  assert.equal(formatPValue(0.0004), '< 0.001')
  assert.equal(formatSigned(-0.5, formatPercent), '−50%')
  assert.equal(formatSigned(0, formatPercent), '±0%')
})

test('任务成功率按题计算 pass@1，多次尝试时不按尝试次数计数，并说明未评分题数', () => {
  const single = taskSuccessStat(
    { 'pass@1': 0.875, measured_task_count: 16, not_measured_tasks: 0 },
    1,
    { passed: 14, failed: 2 }
  )
  assert.deepEqual(single, { value: '87.5%', hint: '14 / 16 题通过' })

  // k=3 时按尝试计数会是 41/48；卡片应展示按题平均的 pass@1，并说明口径。
  const repeated = taskSuccessStat(
    { 'pass@1': 0.8541666666666666, measured_task_count: 16, not_measured_tasks: 0 },
    3,
    { passed: 41, failed: 7 }
  )
  assert.deepEqual(repeated, { value: '85.4%', hint: '16 题 · 每题 3 次尝试取平均' })

  const withInfraErrors = taskSuccessStat(
    { 'pass@1': 0.5, measured_task_count: 2, not_measured_tasks: 1 },
    1,
    { passed: 1, failed: 1 }
  )
  assert.equal(withInfraErrors.hint, '1 / 2 题通过 · 1 题因评测侧错误未评分')
})
