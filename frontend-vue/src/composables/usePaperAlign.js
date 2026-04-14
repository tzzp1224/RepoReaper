import { fetchPaperAlign, streamPaperAlign } from '../api/repo'
import { useAppStore } from '../stores/app'

const PAPER_TEXT_LIMIT = 6000
const GENERIC_ERROR_MESSAGE = 'Paper alignment failed. Please retry.'
const STREAM_FALLBACK_NOTICE = 'Alignment connection interrupted, retried with non-stream mode.'
const STREAM_FALLBACK_FAILED_MESSAGE = 'Alignment connection interrupted, and retry with non-stream mode failed.'

function createAppError(kind, message, userMessage = '') {
  const error = new Error(message || GENERIC_ERROR_MESSAGE)
  error.kind = kind
  error.userMessage = userMessage || ''
  return error
}

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

  function pushTransportFallbackDiagnostic(stage, message, detail = '') {
    store.addPaperAlignDiagnostic({
      type: 'transport_fallback',
      stage,
      message,
      detail,
      timestamp: new Date().toISOString()
    })
  }

  function sanitizePaperText(rawText) {
    return String(rawText || '').trim().slice(0, PAPER_TEXT_LIMIT)
  }

  async function readResponseErrorMessage(response) {
    let message = `HTTP ${response.status}`
    try {
      const payload = await response.json()
      message = payload?.error?.message || payload?.message || message
    } catch (_) {
      // ignore malformed JSON error body
    }
    return message
  }

  function extractResultPayload(payload) {
    if (payload?.status === 'error') {
      const message = payload?.error?.message || GENERIC_ERROR_MESSAGE
      throw createAppError('server', message, message)
    }

    if (payload?.status === 'success' && payload?.data && typeof payload.data === 'object') {
      return payload.data
    }

    if (payload && typeof payload === 'object' && (Array.isArray(payload.alignment_items) || Array.isArray(payload.missing_claims))) {
      return payload
    }

    throw createAppError('transport', 'Unexpected response payload shape.')
  }

  async function parseJsonResponse(response) {
    if (!response.ok) {
      const message = await readResponseErrorMessage(response)
      throw createAppError('server', message, message)
    }

    let payload = null
    try {
      payload = await response.json()
    } catch (_) {
      throw createAppError('transport', 'Response body is not valid JSON.')
    }
    return normalizeResultForUi(extractResultPayload(payload))
  }

  function handleStreamEvent(event) {
    if (event?.status === 'error') {
      const message = event?.error?.message || GENERIC_ERROR_MESSAGE
      throw createAppError('server', message, message)
    }

    pushDiagnostic(event)

    if (event?.type === 'error') {
      const message = event.message || GENERIC_ERROR_MESSAGE
      throw createAppError('server', message, message)
    }

    if (event?.type === 'final' && event?.data) {
      return normalizeResultForUi(event.data)
    }
    return null
  }

  async function parseStreamResponse(response) {
    if (!response.ok) {
      const message = await readResponseErrorMessage(response)
      throw createAppError('server', message, message)
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase()
    if (contentType.includes('application/json')) {
      return parseJsonResponse(response)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw createAppError('transport', 'Paper alignment stream reader is unavailable.')
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

        const maybeResult = handleStreamEvent(event)
        if (maybeResult) {
          finalResult = maybeResult
          store.paperAlignResult = finalResult
        }
      }
    }

    const tail = buffer.trim()
    if (tail) {
      try {
        const event = JSON.parse(tail)
        const maybeResult = handleStreamEvent(event)
        if (maybeResult) {
          finalResult = maybeResult
          store.paperAlignResult = finalResult
        }
      } catch (_) {
        // ignore malformed tail
      }
    }

    if (finalResult) return finalResult
    throw createAppError('transport', 'Paper alignment stream finished without final result.')
  }

  function shouldFallbackToNonStream(error) {
    return error?.kind !== 'server'
  }

  async function requestPaperAlign(payload) {
    try {
      const streamResponse = await streamPaperAlign(payload)
      return await parseStreamResponse(streamResponse)
    } catch (streamError) {
      if (!shouldFallbackToNonStream(streamError)) {
        throw streamError
      }

      pushTransportFallbackDiagnostic('start', STREAM_FALLBACK_NOTICE, streamError?.message || '')

      try {
        const fallbackResponse = await fetchPaperAlign(payload)
        const fallbackResult = await parseJsonResponse(fallbackResponse)
        store.paperAlignResult = fallbackResult
        pushTransportFallbackDiagnostic('done', 'Fallback to non-stream mode succeeded.')
        return fallbackResult
      } catch (fallbackError) {
        pushTransportFallbackDiagnostic('failed', 'Fallback to non-stream mode failed.', fallbackError?.message || '')
        throw createAppError('transport', fallbackError?.message || STREAM_FALLBACK_FAILED_MESSAGE, STREAM_FALLBACK_FAILED_MESSAGE)
      }
    }
  }

  function resolveUserErrorMessage(error) {
    if (error?.userMessage) return error.userMessage
    if (error?.kind === 'server' && error?.message) return error.message
    return GENERIC_ERROR_MESSAGE
  }

  async function runPaperAlign() {
    const rawPaperText = String(store.compiledPaperText || '')
    const paperText = sanitizePaperText(rawPaperText)
    if (!store.sessionId || !paperText) {
      return null
    }

    store.paperAlignLoading = true
    store.paperAlignError = ''
    store.resetPaperAlignDiagnostics()
    store.addPaperAlignDiagnostic({
      type: 'stage',
      stage: 'input',
      message: `Prepared paper text (${paperText.length}/${rawPaperText.length} chars, max ${PAPER_TEXT_LIMIT})`,
      input_chars: rawPaperText.length,
      effective_chars: paperText.length,
      timestamp: new Date().toISOString()
    })

    const payload = {
      session_id: store.sessionId,
      repo_url: store.repoUrl,
      paper_text: paperText,
      top_k: store.paperAlignTopK
    }

    try {
      const result = await requestPaperAlign(payload)
      store.paperAlignResult = result
      return result
    } catch (error) {
      store.paperAlignError = resolveUserErrorMessage(error)
      return null
    } finally {
      store.paperAlignLoading = false
    }
  }

  return { runPaperAlign }
}
