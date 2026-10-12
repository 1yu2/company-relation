/** @typedef {'pending'|'running'|'completed'|'failed'|'cancelled'|'interrupted'|'cancel_requested'} RunStatus */
/** @typedef {'queued'|'dispatched'|'failed'|'cancelled'|'rejected'} RequestStatus */

/** @typedef {{status: string, paused_reason?: string|null, continueInFlight?: boolean}} QueueSnapshot */
/** @typedef {{run_id: string, request_id?: string, status?: RunStatus}} RunCreatedEvent */

const KNOWN_RUN_STATUSES = new Set([
  'pending',
  'running',
  'completed',
  'failed',
  'cancelled',
  'interrupted',
  'cancel_requested'
])

/**
 * 校验后端传来的 Run 状态，避免未知状态静默落入普通分支。
 * @param {unknown} value
 * @returns {RunStatus}
 */
export function assertRunStatus(value) {
  if (typeof value !== 'string' || !KNOWN_RUN_STATUSES.has(value)) {
    throw new Error(`Unknown Run status: ${String(value)}`)
  }
  return /** @type {RunStatus} */ (value)
}

/** @param {unknown} value @returns {value is RunStatus} */
export const isRunStatus = (value) => typeof value === 'string' && KNOWN_RUN_STATUSES.has(value)
