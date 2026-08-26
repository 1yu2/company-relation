const MIME_PREFERENCES = [
  'audio/webm;codecs=opus',
  'audio/ogg;codecs=opus',
  'audio/mp4',
  'audio/wav'
]

/** 选择浏览器和平台同时支持的录音 MIME。 */
export function resolveRecordingMimeType(allowedMimeTypes, isTypeSupported) {
  const allowed = new Set(allowedMimeTypes.map((value) => value.toLowerCase()))
  return MIME_PREFERENCES.find((candidate) => {
    const baseType = candidate.split(';', 1)[0]
    return allowed.has(baseType) && isTypeSupported(candidate)
  })
}

/** 将浏览器麦克风异常转换为可操作的用户提示。 */
export function permissionMessage(error) {
  if (['NotAllowedError', 'PermissionDeniedError'].includes(error?.name)) {
    return '麦克风权限被拒绝，请在浏览器设置中允许后重试。'
  }
  if (['NotFoundError', 'DevicesNotFoundError'].includes(error?.name)) {
    return '没有检测到可用麦克风，请检查设备连接。'
  }
  return '无法开始录音，请检查麦克风后重试。'
}
