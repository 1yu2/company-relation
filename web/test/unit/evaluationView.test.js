import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildLatencyRows,
  buildMetricRows,
  buildRolloutRows,
  experimentIdentity,
  formatPValue,
  formatPercent,
  formatSigned
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
