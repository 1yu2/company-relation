import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'
import { MessageProcessor } from '../../src/utils/messageProcessor.js'

let server
let BaseToolCall

before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createServer({ server: { middlewareMode: true } })
  ;({ default: BaseToolCall } = await server.ssrLoadModule(
    '/src/components/ToolCallingResult/BaseToolCall.vue'
  ))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

for (const status of ['error', 'success']) {
  test(`工具 ${status} 状态保留输出并显示对应图标`, async () => {
    const messages = MessageProcessor.convertToolResultToMessages([
      { type: 'ai', tool_calls: [{ id: 'call-1', name: 'execute', args: {} }] },
      { type: 'tool', tool_call_id: 'call-1', status, content: 'ZeroDivisionError: division by zero' }
    ])
    const app = createSSRApp({
      render: () => h(BaseToolCall, {
        defaultExpanded: true,
        toolCall: messages[0].tool_calls[0]
      })
    })
    app.use(createPinia())
    const output = await renderToString(app)
    assert.match(output, /ZeroDivisionError: division by zero/)
    assert.match(output, status === 'error' ? /执行失败/ : /执行完成/)
    assert.match(output, status === 'error' ? /tool-error/ : /tool-success/)
    assert.doesNotMatch(output, /tool-loading/)
  })
}
