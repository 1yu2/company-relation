import { onBeforeUnmount, ref } from 'vue'

export function useTaskPolling(fetchDetail, { interval = 1500, isTerminal } = {}) {
  const detail = ref(null)
  const polling = ref(false)
  const error = ref(null)
  let timer = null
  let generation = 0

  const stop = () => {
    generation += 1
    if (timer) clearTimeout(timer)
    timer = null
    polling.value = false
  }

  const schedule = (id, currentGeneration) => {
    timer = setTimeout(() => poll(id, currentGeneration), interval)
  }

  const poll = async (id, currentGeneration = generation) => {
    if (!id || currentGeneration !== generation) return
    try {
      detail.value = await fetchDetail(id)
      error.value = null
      if (isTerminal?.(detail.value)) {
        stop()
        return
      }
      schedule(id, currentGeneration)
    } catch (requestError) {
      error.value = requestError
      stop()
    }
  }

  const start = async (id) => {
    stop()
    generation += 1
    polling.value = true
    await poll(id, generation)
  }

  onBeforeUnmount(stop)

  return { detail, polling, error, start, stop }
}
