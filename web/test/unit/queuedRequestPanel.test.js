import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'

let server
let QueuedRequestPanel

before(async () => {
  server = await createServer({ server: { middlewareMode: true } })
  ;({ default: QueuedRequestPanel } = await server.ssrLoadModule(
    '/src/components/QueuedRequestPanel.vue'
  ))
})

after(async () => {
  await server?.close()
})

/** 用真实 Vue SSR 验证队列状态到按钮可见性和禁用状态的映射。 */
const renderPanel = (props) =>
  renderToString(
    createSSRApp({
      render: () => h(QueuedRequestPanel, { canSteer: () => false, canCancel: () => true, ...props })
    })
  )

test('空队列不展示面板，切换快照后只显示新线程请求', async () => {
  assert.doesNotMatch(await renderPanel({ requests: [] }), /aria-label="排队请求"/)
  const output = await renderPanel({
    requests: [{ request_id: 'thread-b-request', content: '新线程请求' }]
  })
  assert.match(output, /新线程请求/)
  assert.match(output, /aria-label="删除排队请求：新线程请求"/)
})

test('暂停提示和继续按钮沿用父线程的进行中状态', async () => {
  const output = await renderPanel({
    requests: [{ request_id: 'request-1', content: '后续问题' }],
    queueSnapshot: { status: 'paused' },
    pausedMessage: '当前任务已停止，后续队列已暂停。',
    continueInFlight: true
  })
  assert.match(output, /当前任务已停止，后续队列已暂停。/)
  assert.match(output, /class="queued-request-continue" disabled/)
})

test('引导和取消权限及进行中禁用状态由父组件决定', async () => {
  const request = { request_id: 'request-1', content: '待执行问题', queue_policy: 'enqueue' }
  const output = await renderPanel({
    requests: [request],
    canSteer: () => true,
    canCancel: () => true,
    steeringRequestIds: new Set(['request-1']),
    cancellingRequestIds: new Set(['request-1'])
  })
  assert.match(output, /class="queued-request-steer" disabled/)
  assert.match(output, /class="queued-request-delete lucide-icon-btn" disabled/)

  const interrupted = await renderPanel({
    requests: [{ ...request, queue_policy: 'steer' }],
    queueSnapshot: { status: 'interrupted' },
    canCancel: () => false
  })
  assert.match(interrupted, /当前任务正在等待回答或审批/)
  assert.match(interrupted, /引导 · 下一条执行/)
  assert.doesNotMatch(interrupted, /class="queued-request-steer"/)
  assert.doesNotMatch(interrupted, /class="queued-request-delete/)
})
