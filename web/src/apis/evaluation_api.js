import { apiGet, apiPost } from './base'

/**
 * 平台级评测 API。
 * 所有来源（Agent/Tool/MCP、RAG、Graph）使用同一套资源查询协议。
 */
export const evaluationApi = {
  listDatasets: (sourceKind, { limit = 50, cursor = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (sourceKind) params.set('source_kind', sourceKind)
    if (cursor) params.set('cursor', cursor)
    return apiGet(`/api/platform-evaluations/datasets?${params.toString()}`)
  },
  getDataset: (datasetId) => apiGet(`/api/platform-evaluations/datasets/${encodeURIComponent(datasetId)}`),
  listVersions: (datasetId) => apiGet(`/api/platform-evaluations/datasets/${encodeURIComponent(datasetId)}/versions`),
  getVersion: (versionId) => apiGet(`/api/platform-evaluations/versions/${encodeURIComponent(versionId)}`),
  listExperiments: (sourceKind, { limit = 50, cursor = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (sourceKind) params.set('source_kind', sourceKind)
    if (cursor) params.set('cursor', cursor)
    return apiGet(`/api/platform-evaluations/experiments?${params.toString()}`)
  },
  getExperiment: (experimentId) => apiGet(`/api/platform-evaluations/experiments/${encodeURIComponent(experimentId)}`),
  listRollouts: (experimentId, { limit = 100, cursor = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (cursor) params.set('cursor', cursor)
    return apiGet(`/api/platform-evaluations/experiments/${encodeURIComponent(experimentId)}/rollouts?${params.toString()}`)
  },
  getTrace: (rolloutId) => apiGet(`/api/platform-evaluations/rollouts/${encodeURIComponent(rolloutId)}/trace`),
  getMetrics: (experimentId) => apiGet(`/api/platform-evaluations/experiments/${encodeURIComponent(experimentId)}/metrics`),
  getFailures: (experimentId) => apiGet(`/api/platform-evaluations/experiments/${encodeURIComponent(experimentId)}/failures`),
  compareExperiments: (baseExperimentId, candidateExperimentId) => {
    const params = new URLSearchParams({
      base_experiment_id: baseExperimentId,
      candidate_experiment_id: candidateExperimentId
    })
    return apiGet(`/api/platform-evaluations/experiments/compare?${params.toString()}`)
  },
  createDataset: (payload) => apiPost('/api/platform-evaluations/datasets', payload),
  createVersion: (datasetId, payload) => apiPost(`/api/platform-evaluations/datasets/${encodeURIComponent(datasetId)}/versions`, payload),
  createExperiment: (payload) => apiPost('/api/platform-evaluations/experiments', payload),
  runExperiment: (experimentId) => apiPost(`/api/platform-evaluations/experiments/${encodeURIComponent(experimentId)}/run`, {}),
  cancelExperiment: (experimentId) => apiPost(`/api/platform-evaluations/experiments/${encodeURIComponent(experimentId)}/cancel`, {})
}
