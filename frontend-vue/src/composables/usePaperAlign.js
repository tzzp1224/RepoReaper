import { fetchPaperAlign } from '../api/repo'
import { useAppStore } from '../stores/app'

export function usePaperAlign() {
  const store = useAppStore()

  async function runPaperAlign() {
    const paperText = store.compiledPaperText
    if (!store.sessionId || !paperText) {
      return null
    }

    store.paperAlignLoading = true
    store.paperAlignError = ''

    try {
      const response = await fetchPaperAlign({
        session_id: store.sessionId,
        repo_url: store.repoUrl,
        paper_text: paperText,
        top_k: store.paperAlignTopK
      })

      if (response.status === 'success') {
        store.paperAlignResult = response.data
        return response.data
      }

      store.paperAlignError = response.error?.message || 'Paper alignment failed.'
      return null
    } catch (error) {
      store.paperAlignError = error.message || 'Paper alignment failed.'
      return null
    } finally {
      store.paperAlignLoading = false
    }
  }

  return { runPaperAlign }
}
