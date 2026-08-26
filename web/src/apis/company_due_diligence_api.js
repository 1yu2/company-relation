import { apiAdminGet, apiAdminPost } from './base.js'
import { buildCompanySearchUrl } from '../utils/companyDueDiligence.js'

const BASE_URL = '/api/company-due-diligence'

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

  investigateCompany(payload) {
    return apiAdminPost(`${BASE_URL}/investigations`, payload)
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
  }
}
