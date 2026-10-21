import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'
import { MessageProcessor } from '../../src/utils/messageProcessor.js'

let server
let BaseToolCall
let QueryKbTool

before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createServer({ server: { middlewareMode: true } })
  ;({ default: BaseToolCall } = await server.ssrLoadModule(
    '/src/components/ToolCallingResult/BaseToolCall.vue'
  ))
  ;({ default: QueryKbTool } = await server.ssrLoadModule(
    '/src/components/ToolCallingResult/tools/QueryKbTool.vue'
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

for (const { status, content, expected } of [
  {
    status: 'error',
    content: '检索失败: 知识库资源不存在\n<详情>',
    expected: /检索失败: 知识库资源不存在\n&lt;详情&gt;/
  },
  { status: 'success', content: '{"chunks":[]}', expected: /未找到相关知识库内容/ },
  {
    status: 'success',
    content: '{"entities":[{"entity_name":"Error: 失败处理说明"}]}',
    expected: /Error: 失败处理说明/
  }
]) {
  test(`知识库卡片按 ${status} 状态显示结果：${content}`, async () => {
    const messages = MessageProcessor.convertToolResultToMessages([
      { type: 'ai', tool_calls: [{ id: 'kb-call', name: 'query_kb', args: {} }] },
      { type: 'tool', tool_call_id: 'kb-call', status, content }
    ])
    const app = createSSRApp({
      render: () => h(QueryKbTool, {
        defaultExpanded: true,
        toolCall: messages[0].tool_calls[0]
      })
    })
    app.use(createPinia())
    app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
    const output = await renderToString(app)
    assert.match(output, expected)
    if (status === 'error') {
      assert.doesNotMatch(output, /未找到相关知识库内容/)
      assert.match(output, /tool-error/)
    } else {
      assert.doesNotMatch(output, /query-kb-error/)
      assert.match(output, /tool-success/)
    }
  })
}
