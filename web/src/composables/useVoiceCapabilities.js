import { computed, ref } from 'vue'
import { getVoiceCapabilities } from '@/apis/voice_api'

const capabilities = ref(null)
const loading = ref(false)
const error = ref(null)
let pendingRequest = null

/** 在所有聊天入口之间共享一次语音能力查询。 */
export function useVoiceCapabilities() {
  const load = async ({ force = false } = {}) => {
    if (capabilities.value && !force) return capabilities.value
    if (pendingRequest && !force) return pendingRequest

    loading.value = true
    error.value = null
    pendingRequest = getVoiceCapabilities()
      .then((result) => {
        capabilities.value = result
        return result
      })
      .catch((requestError) => {
        error.value = requestError
        throw requestError
      })
      .finally(() => {
        loading.value = false
        pendingRequest = null
      })
    return pendingRequest
  }

  return {
    capabilities,
    loading,
    error,
    asrEnabled: computed(() => Boolean(capabilities.value?.asr?.enabled)),
    ttsEnabled: computed(() => Boolean(capabilities.value?.tts?.enabled)),
    load
  }
}
