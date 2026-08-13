import { apiAdminDelete, apiAdminGet } from './base'

const BASE_URL = '/api/system/external-company-data'

export const externalCompanyDataApi = {
  getHealth() {
    return apiAdminGet(`${BASE_URL}/health`)
  },

  getCache() {
    return apiAdminGet(`${BASE_URL}/cache`)
  },

  clearCache(providerName = '') {
    const query = providerName ? `?provider_name=${encodeURIComponent(providerName)}` : ''
    return apiAdminDelete(`${BASE_URL}/cache${query}`)
  },

  getAudit(limit = 20) {
    return apiAdminGet(`${BASE_URL}/audit?limit=${limit}`)
  }
}
