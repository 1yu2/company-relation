import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'
import { MessageProcessor } from '../../src/utils/messageProcessor.js'

let server
let useAgentStreamHandler

before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createServer({ server: { middlewareMode: true } })
  ;({ useAgentStreamHandler } = await server.ssrLoadModule(
    '/src/composables/useAgentStreamHandler.js'
  ))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

const toolEvent = (data, namespace = []) => ({
  status: 'stream_event',
  event: { method: 'tools', namespace, data }
})

test('nostream 智能体运行中由 tool-started/tool-finished 事件生成工具进度卡片', () => {
  const threadState = { onGoingConv: { msgChunks: {} } }
  const { handleStreamChunk } = useAgentStreamHandler({ getThreadState: () => threadState })
  const ongoingMessages = () =>
    MessageProcessor.convertToolResultToMessages(
      Object.values(threadState.onGoingConv.msgChunks).map(MessageProcessor.mergeMessageChunk)
    ).filter((message) => message.type !== 'tool')

  handleStreamChunk(
    toolEvent({
      event: 'tool-started',
      tool_call_id: 'call-1',
      tool_name: 'cdd_resolve_company',
      input: { name: '某公司' }
    }),
    'thread-1'
  )
  handleStreamChunk(
    toolEvent({ event: 'tool-started', tool_call_id: 'sub-call', tool_name: 'x' }, ['task:1']),
    'thread-1'
  )
  let [message, ...rest] = ongoingMessages()
  assert.equal(rest.length, 0)
  assert.equal(message.type, 'ai')
  assert.deepEqual(message.tool_calls[0].args, { name: '某公司' })
  assert.equal(message.tool_calls[0].tool_call_result, null)

  handleStreamChunk(
    toolEvent({
      event: 'tool-finished',
      tool_call_id: 'call-1',
      output: {
        type: 'tool',
        id: 'msg-1',
        tool_call_id: 'call-1',
        status: 'success',
        content: '{}'
      }
    }),
    'thread-1'
  )
  ;[message] = ongoingMessages()
  assert.equal(message.tool_calls[0].status, 'success')
})
