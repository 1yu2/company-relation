import assert from 'node:assert/strict'
import test from 'node:test'

import { markdownToSpeechText } from '../../src/utils/voice_text.js'

test('朗读文本移除 Markdown 标记并保留可见语义', () => {
  const markdown = `# 风险结论

**结论**：[查看来源](https://example.com)

| 股东 | 比例 |
| --- | --- |
| 甲公司 | 60% |

\`注册资本\``

  assert.equal(
    markdownToSpeechText(markdown),
    '风险结论\n\n结论：查看来源\n\n股东，比例\n甲公司，60%\n\n注册资本'
  )
})

test('朗读文本只保留代码块正文而不朗读围栏', () => {
  assert.equal(markdownToSpeechText('```python\nprint("hello")\n```'), 'print("hello")')
})
