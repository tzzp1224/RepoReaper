import { streamPaperAlign } from '../api/repo'
import { useAppStore } from '../stores/app'

export function usePaperAlign() {
  const store = useAppStore()

  function normalizeResultForUi(raw) {
    if (!raw || typeof raw !== 'object') return raw

    const normalized = {
      ...raw,
      alignment_items: Array.isArray(raw.alignment_items) ? raw.alignment_items.map(item => {
        if (!item || typeof item !== 'object') return item
        return {
          ...item,
          status: item.status === 'insufficient_evidence' ? 'missing' : item.status
        }
      }) : [],
      missing_claims: Array.isArray(raw.missing_claims) ? raw.missing_claims.map(item => {
        if (!item || typeof item !== 'object') return item
        return {
          ...item,
          status: item.status === 'insufficient_evidence' ? 'missing' : (item.status || 'missing')
        }
      }) : []
    }

    return normalized
  }

  function pushDiagnostic(event) {
    if (!event || typeof event !== 'object') return
    if (event.type === '_end') return
    if (event.type === 'final') {
      store.addPaperAlignDiagnostic({
        type: 'stage',
        stage: 'complete',
        message: 'Alignment completed',
        timestamp: event.timestamp
      })
      return
    }
    store.addPaperAlignDiagnostic(event)
  }

  async function runPaperAlign() {
    const paperText = store.compiledPaperText
    if (!store.sessionId || !paperText) {
      return null
    }

    store.paperAlignLoading = true
    store.paperAlignError = ''
    store.resetPaperAlignDiagnostics()

    try {
      const response = await streamPaperAlign({
        session_id: store.sessionId,
        repo_url: store.repoUrl,
        paper_text: paperText,
        top_k: store.paperAlignTopK
      })

      if (!response.ok) {
        let errorText = `HTTP ${response.status}`
        try {
          const payload = await response.json()
          errorText = payload?.error?.message || errorText
        } catch (_) {
          // ignore JSON parse errors
        }
        store.paperAlignError = errorText
        return null
      }

      const reader = response.body?.getReader()
      if (!reader) {
        store.paperAlignError = 'Paper alignment stream is unavailable.'
        return null
      }

      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      let finalResult = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const text = line.trim()
          if (!text) continue

          let event = null
          try {
            event = JSON.parse(text)
          } catch (_) {
            continue
          }

          if (event?.status === 'error') {
            store.paperAlignError = event?.error?.message || 'Paper alignment failed.'
            continue
          }

          pushDiagnostic(event)

          if (event?.type === 'error') {
            store.paperAlignError = event.message || 'Paper alignment failed.'
            continue
          }

          if (event?.type === 'final' && event?.data) {
            finalResult = normalizeResultForUi(event.data)
            store.paperAlignResult = finalResult
          }
        }
      }

      const tail = buffer.trim()
      if (tail) {
        try {
          const event = JSON.parse(tail)
          if (event?.status === 'error') {
            store.paperAlignError = event?.error?.message || 'Paper alignment failed.'
          } else {
            pushDiagnostic(event)
            if (event?.type === 'final' && event?.data) {
              finalResult = normalizeResultForUi(event.data)
              store.paperAlignResult = finalResult
            }
          }
        } catch (_) {
          // ignore malformed tail
        }
      }

      if (finalResult) return finalResult

      if (!store.paperAlignError) {
        store.paperAlignError = 'Paper alignment finished without final result.'
      }
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
