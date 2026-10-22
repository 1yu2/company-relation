import assert from 'node:assert/strict'
import test from 'node:test'

import { MessageProcessor } from '../../src/utils/messageProcessor.js'

test('交付物只归属于调用 present_artifacts 的对话', () => {
  const artifactConversation = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'present_artifacts',
            tool_call_result: { content: '已将交付物展示给用户' },
            args: JSON.stringify({
              filepaths: [
                '/home/gem/user-data/outputs/bubble_sort.py',
                '/home/gem/user-data/outputs/bubble_sort.js'
              ]
            })
          },
          {
            function: { name: 'present_artifacts' },
            status: 'success',
            args: { filepaths: ['/home/gem/user-data/outputs/bubble_sort.py'] }
          }
        ]
      }
    ]
  }
  const laterConversation = {
    messages: [{ type: 'human', content: '运行 Python 的' }]
  }

  assert.deepEqual(MessageProcessor.extractArtifactsFromConversation(artifactConversation), [
    '/home/gem/user-data/outputs/bubble_sort.py',
    '/home/gem/user-data/outputs/bubble_sort.js'
  ])
  assert.deepEqual(MessageProcessor.extractArtifactsFromConversation(laterConversation), [])
})

for (const status of ['error', 'success']) {
  test(`流式工具结果将 ${status} 状态同步到工具卡片`, () => {
    const response = { type: 'tool', tool_call_id: 'call-1', status, content: 'output' }
    const messages = MessageProcessor.convertToolResultToMessages([
      { type: 'ai', tool_calls: [{ id: 'call-1', name: 'execute' }] }, response
    ])
    assert.equal(messages[0].tool_calls[0].status, status)
    assert.equal(messages[0].tool_calls[0].tool_call_result, response)
  })
}

test('工具进度占位在真实 AI 消息已含同一工具调用时被丢弃', () => {
  const progress = (id) => ({
    type: 'ai',
    tool_progress: true,
    tool_calls: [{ id, name: 'cdd_resolve_company', args: {} }]
  })
  const messages = MessageProcessor.convertToolResultToMessages([
    { type: 'ai', content: '', tool_calls: [{ id: 'call-1', function: { name: 'search' } }] },
    progress('call-1'),
    progress('call-2'),
    { type: 'tool', tool_call_id: 'call-2', status: 'success', content: 'ok' }
  ])
  assert.deepEqual(
    messages.filter((message) => message.type === 'ai').map((message) => message.tool_calls[0].id),
    ['call-1', 'call-2']
  )
  assert.equal(messages[1].tool_calls[0].status, 'success')
})

test('审批恢复后历史中已有的工具调用不再生成进度占位', () => {
  const history = [{ type: 'ai', tool_calls: [{ id: 'call-1', name: 'execute', args: {} }] }]
  const messages = MessageProcessor.convertToolResultToMessages(
    [
      { type: 'ai', tool_progress: true, tool_calls: [{ id: 'call-1', name: 'execute', args: {} }] }
    ],
    history
  )
  assert.deepEqual(messages, [])
})
