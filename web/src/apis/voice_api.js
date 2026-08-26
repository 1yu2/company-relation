import { apiGet, apiPost } from './base'

/** 查询当前部署实际启用的语音能力。 */
export function getVoiceCapabilities() {
  return apiGet('/api/voice/capabilities')
}

/** 上传一次临时录音并返回可编辑转写草稿。 */
export function transcribeVoice(file, language = '') {
  const formData = new FormData()
  formData.append('file', file)
  if (language) {
    formData.append('language', language)
  }
  return apiPost('/api/voice/transcriptions', formData)
}

/** 将已有助手文字或合法选区转换为音频响应。 */
export function synthesizeVoice(payload, signal) {
  return apiPost('/api/voice/speech', payload, { signal }, true, 'blob')
}

/** 上报不含正文的语音交互事件。 */
export function recordVoiceMetric(event) {
  return apiPost('/api/voice/metrics', { event })
}
