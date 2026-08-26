import assert from 'node:assert/strict'
import test from 'node:test'

import { permissionMessage, resolveRecordingMimeType } from '../../src/utils/voice_recording.js'

test('录音格式选择同时服从浏览器与平台能力', () => {
  const supported = new Set(['audio/ogg;codecs=opus', 'audio/wav'])
  const mimeType = resolveRecordingMimeType(['audio/webm', 'audio/ogg'], (candidate) =>
    supported.has(candidate)
  )

  assert.equal(mimeType, 'audio/ogg;codecs=opus')
})

test('麦克风异常返回可恢复提示', () => {
  assert.equal(
    permissionMessage({ name: 'NotAllowedError' }),
    '麦克风权限被拒绝，请在浏览器设置中允许后重试。'
  )
  assert.equal(
    permissionMessage({ name: 'NotFoundError' }),
    '没有检测到可用麦克风，请检查设备连接。'
  )
})
