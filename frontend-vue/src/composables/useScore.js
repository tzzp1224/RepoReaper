import { fetchReproScore } from '../api/repo'
import { useAppStore } from '../stores/app'

export function useScore() {
  const store = useAppStore()

  function isNoContextMessage(message = '') {
    const normalized = String(message).toLowerCase()
    return normalized.includes('no analyzed context') || normalized.includes('run /analyze first')
  }

  async function loadScore() {
    if (!store.canUseAnalyzedContext) {
      store.scoreError = ''
      return null
    }

    if (!store.sessionId && !store.repoUrl.trim()) {
      store.scoreError = ''
      return null
    }

    store.scoreLoading = true
    store.scoreError = ''

    try {
      const response = await fetchReproScore(store.sessionId, store.repoUrl)
      if (response.status === 'success') {
        store.scoreResult = response.data
        return response.data
      }
      const message = response.error?.message || 'Failed to load reproducibility score.'
      if (isNoContextMessage(message)) {
        store.scoreResult = null
        store.scoreError = ''
        return null
      }
      store.scoreError = message
      return null
    } catch (error) {
      const message = error.message || 'Failed to load reproducibility score.'
      if (isNoContextMessage(message)) {
        store.scoreResult = null
        store.scoreError = ''
        return null
      }
      store.scoreError = message
      return null
    } finally {
      store.scoreLoading = false
    }
  }

  return { loadScore }
}
