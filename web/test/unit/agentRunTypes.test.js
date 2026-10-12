import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'vite'

const serverPromise = createServer({ server: { middlewareMode: true } })

test('Run 状态契约接受已知状态并显式拒绝未知状态', async () => {
  const server = await serverPromise
  try {
    const { assertRunStatus, isRunStatus } = await server.ssrLoadModule('/src/types/agent_run.js')
    assert.equal(assertRunStatus('completed'), 'completed')
    assert.equal(isRunStatus('interrupted'), true)
    assert.equal(isRunStatus('unknown'), false)
    assert.throws(() => assertRunStatus('unknown'), /Unknown Run status/)
  } finally {
    await server.close()
  }
})
