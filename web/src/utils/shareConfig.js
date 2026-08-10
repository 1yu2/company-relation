export function getShareConfigLabel(shareConfig) {
  const config = shareConfig || {}
  if (config.access_level === 'department') {
    return `部门共享(${config.department_ids?.length || 0})`
  }
  if (config.access_level === 'user') {
    return `指定用户(${config.user_uids?.length || 0})`
  }
  return '全局共享'
}
