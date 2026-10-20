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
