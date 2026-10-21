/**
 * 评测页面的展示投影：把实验摘要、指标和任务整理成页面直接使用的行数据和用户语言标签。
 * 只做选择与格式化，不重新计算后端已经聚合好的指标。
 */

/** 指标的用户语言名称与所属层；结果层决定任务是否成功，轨迹层只反映过程质量。 */
export const METRIC_META = {
  task_pass: { label: '任务成功', layer: 'summary' },
  tool_safety: { label: '未调用禁用工具', layer: 'result' },
  tool_selection: { label: '按要求调用工具', layer: 'result' },
  answer_value: { label: '答案值正确', layer: 'result' },
  answer_format: { label: '答案格式符合', layer: 'result' },
  failure_reported: { label: '如实报告失败', layer: 'result' },
  answer_correct: { label: '回答正确', layer: 'result' },
  judge_correct: { label: 'Judge 判定正确', layer: 'result' },
  hit_at_k: { label: '前 K 条命中率', layer: 'retrieval' },
  recall_at_k: { label: '前 K 条召回率', layer: 'retrieval' },
  mrr: { label: '平均倒数排名', layer: 'retrieval' },
  ndcg_at_k: { label: '前 K 条归一化折损增益', layer: 'retrieval' },
  fault_injected: { label: '故障注入占比', layer: 'trajectory' },
  tool_call_budget: { label: '工具调用不超预算', layer: 'trajectory' },
  loop_detected: { label: '无重复调用循环', layer: 'trajectory' },
  duplicate_call_rate: { label: '重复调用占比', layer: 'trajectory' },
  tool_error_rate: { label: '工具报错占比', layer: 'trajectory' },
  answer_match: { label: '答案匹配', layer: 'result' },
  tool_policy: { label: '工具策略', layer: 'result' },
  trace_contract: { label: '轨迹契约', layer: 'trajectory' }
}

const REASON_LABELS = {
  answer_value_mismatch: '答案错误',
  answer_format_violation: '格式不符（值正确）',
  required_tool_missing: '未按要求调用工具',
  forbidden_tool_called: '调用了禁用工具',
  tool_budget_exceeded: '工具调用超预算',
  tool_call_loop: '同参数重复调用',
  answer_mismatch: '答案不匹配',
  trace_contract_violation: '轨迹不符合约定',
  timeout: '超时',
  runner_error: '运行出错',
  cancelled: '已取消',
  agent_run_failed: 'Agent 运行失败',
  agent_run_cancelled: 'Agent 运行被取消',
  agent_run_interrupted: 'Agent 运行中断',
  agent_run_rejected: 'Agent 拒绝执行',
  expected_answer_missing: '题目缺少期望答案',
  answer_value_incorrect: '答案错误，未评格式',
  failure_not_reported: '未如实报告失败',
  answer_point_missing: '缺少答案要点',
  answer_reference_missing: '缺少答案参考',
  relevant_chunks_missing: '缺少相关分块标注',
  retrieval_not_called: '未调用检索',
  retrieval_miss: '检索未命中',
  ranking_miss: '相关内容排名靠后',
  generation_error: '回答生成错误',
  // judge_failed 是 Judge 判定回答不通过；Judge 调用失败或超时走评测侧错误（runner_error），不会产生这个原因码
  judge_failed: 'Judge 判定未通过',
  judge_output_invalid: 'Judge 输出无效',
  judge_not_calibrated: 'Judge 尚未完成人工校准',
  run_failed: 'Run 运行失败',
  final_answer_missing: '缺少最终回答',
  tool_error: '工具报错',
  user_disliked: '用户点踩'
}

const STATUS_META = {
  DRAFT: { label: '未运行', tone: 'neutral' },
  QUEUED: { label: '排队中', tone: 'info' },
  RUNNING: { label: '运行中', tone: 'info' },
  AGGREGATING: { label: '汇总中', tone: 'info' },
  CANCELLING: { label: '取消中', tone: 'warning' },
  FINISHED: { label: '已完成', tone: 'success' },
  FAILED: { label: '失败', tone: 'error' },
  CANCELLED: { label: '已取消', tone: 'neutral' },
  COMPLETED: { label: '已完成', tone: 'success' },
  TIMEOUT: { label: '超时', tone: 'error' },
  passed: { label: '通过', tone: 'success' },
  failed: { label: '未通过', tone: 'error' },
  not_measured: { label: '未评分', tone: 'neutral' },
  unavailable: { label: '数据不可用', tone: 'warning' },
  pending_review: { label: '待复核', tone: 'warning' },
  promoted: { label: '已生成草稿', tone: 'neutral' }
}

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'AGGREGATING', 'CANCELLING'])

export const isActiveExperiment = (status) => ACTIVE_STATUSES.has(status)
export const statusMeta = (status) =>
  STATUS_META[status] || { label: status || '未知', tone: 'neutral' }
export const reasonLabel = (code) => (code ? REASON_LABELS[code] || code : '')
export const metricLabel = (name) => METRIC_META[name]?.label || name

export const formatPercent = (value) =>
  typeof value === 'number' ? `${(value * 100).toFixed(1).replace(/\.0$/, '')}%` : '—'

export const formatMs = (value) => {
  if (typeof value !== 'number') return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`
}

export const formatCount = (value) =>
  typeof value === 'number' ? Math.round(value).toLocaleString('zh-CN') : '—'

export const formatSigned = (value, formatter) => {
  if (typeof value !== 'number') return '—'
  return `${value > 0 ? '+' : value < 0 ? '−' : '±'}${formatter(Math.abs(value))}`
}

/** p 值保留三位有效小数；< 0.001 时不显示具体值，避免暗示过度精确。 */
export const formatPValue = (value) => {
  if (typeof value !== 'number') return '—'
  return value < 0.001 ? '< 0.001' : value.toFixed(3)
}

/**
 * 任务成功率卡片：按题计算的 pass@1（每题多次尝试先取平均，再对题平均），而不是按尝试次数统计。
 * 超时等 Agent 失败已由后端计为未通过；只有评测侧错误没有评分，单独说明题数，不混入分母。
 */
export const taskSuccessStat = (aggregate, maxAttempts, taskPassCounts, reasonCounts = {}) => {
  const measured = aggregate?.measured_task_count ?? 0
  const unscored = aggregate?.not_measured_tasks ?? 0
  const uncalibrated = reasonCounts.judge_not_calibrated || 0
  const attempts = Object.values(taskPassCounts || {}).reduce((sum, count) => sum + count, 0)
  const hint =
    (maxAttempts || 1) > 1
      ? `${measured} 题 · 每题 ${maxAttempts} 次尝试取平均`
      : `${taskPassCounts?.passed || 0} / ${measured} 题通过`
  return {
    value: formatPercent(aggregate?.['pass@1']),
    hint: unscored
      ? `${hint} · ${unscored} 题${uncalibrated ? '' : '因评测侧错误'}未评分`
      : hint,
    ...(uncalibrated > attempts / 2
      ? { notice: 'LLM Judge 尚未完成人工校准，开放式题不计入通过率' }
      : {})
  }
}

/** 只有实际注入故障的实验才显示恢复率，直接使用后端汇总口径。 */
export const faultRecoveryStat = (recovery) =>
  recovery?.injected > 0
    ? {
        label: '失败恢复率',
        value: `${recovery.recovered}/${recovery.injected}`,
        hint: formatPercent(recovery.rate)
      }
    : null

/** 从实验快照中取页面需要的身份信息：Agent、实际模型、数据集版本和评分器。 */
export const experimentIdentity = (experiment) => {
  const snapshot = experiment?.config_snapshot || {}
  const captured = snapshot.captured || {}
  return {
    agent: captured.agent?.agent_slug || experiment?.agent_slug || experiment?.runner || '—',
    model: captured.agent?.model_spec || snapshot.model_spec || '未记录',
    note: snapshot.note || '',
    taskCount: captured.dataset?.task_count,
    evaluators: Object.values(captured.evaluators || {})
  }
}

/** 把逐题 rollout 与题目、指标拼成一行，失败原因优先用 task_pass 的主因，其次用 runner 原因。 */
export const buildRolloutRows = (experiment, tasksById) => {
  const metricsByRollout = new Map()
  for (const metric of experiment?.metrics || []) {
    if (!metricsByRollout.has(metric.rollout_id)) metricsByRollout.set(metric.rollout_id, {})
    metricsByRollout.get(metric.rollout_id)[metric.metric_name] = metric
  }
  return (experiment?.rollouts || [])
    .map((rollout) => {
      const task = tasksById.get(rollout.task_id) || {}
      const metrics = metricsByRollout.get(rollout.rollout_id) || {}
      const output = rollout.actual_output || {}
      const toolCalls = output.tool_calls || []
      const verdict =
        metrics.task_pass?.status ||
        (rollout.status === 'COMPLETED' ? 'not_measured' : rollout.status)
      return {
        rolloutId: rollout.rollout_id,
        taskKey: task.task_key || rollout.task_id,
        attempt: rollout.attempt,
        query: task.input?.query || '',
        expected: task.expected?.final_answer?.value ?? null,
        verdict,
        reason: metrics.task_pass?.reason_code || rollout.reason_code || '',
        errorMessage: rollout.error_message || '',
        finalAnswer: output.final_answer ?? '',
        toolCalls,
        toolNames: [...new Set(toolCalls.map((call) => call.name))],
        latencyMs: output.latency?.total_ms ?? rollout.duration_ms ?? null,
        tokens: output.usage?.total ?? null,
        metrics
      }
    })
    .sort(
      (left, right) => left.taskKey.localeCompare(right.taskKey) || left.attempt - right.attempt
    )
}

/** 指标明细按 结果层 → 检索层 → 轨迹层 排序，未登记的指标排在最后。 */
export const buildMetricRows = (breakdown) => {
  const order = ['result', 'retrieval', 'trajectory']
  return Object.entries(breakdown || {})
    .filter(([name]) => METRIC_META[name]?.layer !== 'summary')
    .map(([name, item]) => ({
      name,
      label: metricLabel(name),
      layer: METRIC_META[name]?.layer || 'other',
      passRate: item.pass_rate,
      measuredMean: item.measured_mean,
      counts: item.status_counts || {},
      reasons: Object.entries(item.reason_counts || {})
        .filter(([code]) => !['answer_value_incorrect', 'expected_answer_missing'].includes(code))
        .map(([code, count]) => ({ label: reasonLabel(code), count }))
    }))
    .sort((left, right) => {
      const rank = (row) => (order.includes(row.layer) ? order.indexOf(row.layer) : order.length)
      return rank(left) - rank(right)
    })
}

/** 耗时分布的展示行；样本不足时 P95 由后端返回 null，页面显示“样本不足”。 */
export const buildLatencyRows = (performance) => {
  const latency = performance?.latency_ms || {}
  return [
    { label: '端到端', item: latency.agent_total },
    { label: '排队', item: latency.agent_queue },
    { label: '执行', item: latency.agent_execution }
  ].filter((row) => row.item?.sample_count)
}
