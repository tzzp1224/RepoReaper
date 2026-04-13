import { fetchReproScore } from '../api/repo'
import { useAppStore } from '../stores/app'

export function useScore() {
  const store = useAppStore()

  async function loadScore() {
    if (!store.sessionId && !store.repoUrl.trim()) {
      store.scoreError = 'Analyze a repository first.'
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
      store.scoreError = message
      return null
    } catch (error) {
      store.scoreError = error.message || 'Failed to load reproducibility score.'
      return null
    } finally {
      store.scoreLoading = false
    }
  }

  return { loadScore }
}
