import { apiAdminGet, apiAdminPost } from './base.js'
import { buildCompanySearchUrl } from '../utils/companyDueDiligence.js'

const BASE_URL = '/api/company-due-diligence'
const EVALUATION_URL = `${BASE_URL}/evaluations`

export const companyDueDiligenceApi = {
  getHealth() {
    return apiAdminGet(`${BASE_URL}/health`)
  },

  searchCompanies(query, limit = 10) {
    return apiAdminGet(buildCompanySearchUrl(query, limit))
  },

  resolveCompany(reference) {
    return apiAdminPost(`${BASE_URL}/companies/resolve`, reference)
  },

  queryCompany(payload) {
    return apiAdminPost(`${BASE_URL}/query`, payload)
  },

  createReport(payload) {
    return apiAdminPost(`${BASE_URL}/reports`, payload)
  },

  listReports(limit = 50) {
    return apiAdminGet(`${BASE_URL}/reports?limit=${limit}`)
  },

  getReport(reportId) {
    return apiAdminGet(`${BASE_URL}/reports/${encodeURIComponent(reportId)}`)
  },

  cancelReport(reportId) {
    return apiAdminPost(`${BASE_URL}/reports/${encodeURIComponent(reportId)}/cancel`, {})
  },

  retryReportSection(reportId, section) {
    return apiAdminPost(
      `${BASE_URL}/reports/${encodeURIComponent(reportId)}/sections/${encodeURIComponent(section)}/retry`,
      {}
    )
  },

  downloadReport(reportId, format) {
    return apiAdminGet(
      `${BASE_URL}/reports/${encodeURIComponent(reportId)}/download?format=${format}`,
      {},
      'blob'
    )
  },

  listEvaluationDatasets() {
    return apiAdminGet(`${EVALUATION_URL}/datasets`)
  },

  getEvaluationDataset(datasetId) {
    return apiAdminGet(`${EVALUATION_URL}/datasets/${encodeURIComponent(datasetId)}`)
  },

  createQualityRun(payload) {
    return apiAdminPost(`${EVALUATION_URL}/runs`, payload)
  },

  listQualityRuns(limit = 50) {
    return apiAdminGet(`${EVALUATION_URL}/runs?limit=${limit}`)
  },

  getQualityRun(runId) {
    return apiAdminGet(`${EVALUATION_URL}/runs/${encodeURIComponent(runId)}`)
  },

  cancelQualityRun(runId) {
    return apiAdminPost(`${EVALUATION_URL}/runs/${encodeURIComponent(runId)}/cancel`, {})
  },

  downloadQualityRun(runId, format) {
    return apiAdminGet(
      `${EVALUATION_URL}/runs/${encodeURIComponent(runId)}/download?format=${format}`,
      {},
      'blob'
    )
  },

  createPerformanceRun(payload) {
    return apiAdminPost(`${EVALUATION_URL}/performance-runs`, payload)
  },

  listPerformanceRuns(limit = 50) {
    return apiAdminGet(`${EVALUATION_URL}/performance-runs?limit=${limit}`)
  },

  getPerformanceRun(runId) {
    return apiAdminGet(`${EVALUATION_URL}/performance-runs/${encodeURIComponent(runId)}`)
  },

  cancelPerformanceRun(runId) {
    return apiAdminPost(`${EVALUATION_URL}/performance-runs/${encodeURIComponent(runId)}/cancel`, {})
  },

  downloadPerformanceRun(runId, format) {
    return apiAdminGet(
      `${EVALUATION_URL}/performance-runs/${encodeURIComponent(runId)}/download?format=${format}`,
      {},
      'blob'
    )
  }
}
