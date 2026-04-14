import { useAppStore } from '../stores/app'
import { createIssueStream, createRoadmapStream, fetchArtifactSnapshot } from '../api/repo'

/**
 * Insights 逻辑组合式函数 — Issue 摘要 & Commit Roadmap
 */
export function useInsights() {
  const store = useAppStore()

  let issueEventSource = null
  let roadmapEventSource = null
  let roadmapChunkBuffer = ''
  let roadmapFlushTimer = null
  const ROADMAP_FLUSH_MS = 80

  function flushRoadmapChunks() {
    if (!roadmapChunkBuffer) return
    store.roadmapContent += roadmapChunkBuffer
    roadmapChunkBuffer = ''
  }

  function scheduleRoadmapFlush() {
    if (roadmapFlushTimer) return
    roadmapFlushTimer = setTimeout(() => {
      roadmapFlushTimer = null
      flushRoadmapChunks()
    }, ROADMAP_FLUSH_MS)
  }

  function resetRoadmapBuffer() {
    if (roadmapFlushTimer) {
      clearTimeout(roadmapFlushTimer)
      roadmapFlushTimer = null
    }
    roadmapChunkBuffer = ''
  }

  async function loadIssuesSnapshot() {
    if (!store.repoUrl.trim()) {
      store.issueNotes = ''
      return null
    }
    try {
      const response = await fetchArtifactSnapshot(
        store.sessionId,
        store.repoUrl,
        'issues',
        store.language
      )
      const data = response?.data || {}
      if (response?.status === 'success' && data.exists && data.data?.content) {
        store.issueNotes = data.data.content
        return data.data.content
      }
      store.issueNotes = ''
      return null
    } catch (e) {
      console.error('loadIssuesSnapshot failed:', e)
      store.issueNotes = ''
      return null
    }
  }

  async function loadRoadmapSnapshot() {
    if (!store.repoUrl.trim()) {
      store.roadmapContent = ''
      return null
    }
    try {
      const response = await fetchArtifactSnapshot(
        store.sessionId,
        store.repoUrl,
        'roadmap',
        store.language
      )
      const data = response?.data || {}
      if (response?.status === 'success' && data.exists && data.data?.content) {
        store.roadmapContent = data.data.content
        return data.data.content
      }
      store.roadmapContent = ''
      return null
    } catch (e) {
      console.error('loadRoadmapSnapshot failed:', e)
      store.roadmapContent = ''
      return null
    }
  }

  function fetchIssues(options = {}) {
    const force = Boolean(options?.force)
    if (!store.repoUrl.trim()) {
      store.addLog('ℹ️ Enter a repository URL before fetching issues.', '#f59e0b')
      return
    }
    if (store.isIssueStreaming) return
    if (!force && store.issueNotes) return

    store.issueNotes = ''
    store.isIssueStreaming = true
    store.activeInsightTab = 'issues'
    store.addLog('📋 Fetching issues...', '#64748b')

    issueEventSource = createIssueStream(
      store.repoUrl,
      store.sessionId,
      store.language,
      force
    )

    issueEventSource.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.step === 'content_chunk') {
        store.issueNotes += data.chunk
      } else if (data.step === 'finish') {
        store.addLog(`✅ ${data.message}`, '#15803d')
        issueEventSource.close()
        issueEventSource = null
        store.isIssueStreaming = false
      } else if (data.step === 'error') {
        store.addLog(`❌ ${data.message}`, '#b91c1c')
        issueEventSource.close()
        issueEventSource = null
        store.isIssueStreaming = false
      } else if (data.message) {
        store.addLog(`👉 ${data.message}`)
      }
    }

    issueEventSource.onerror = () => {
      store.addLog('❌ Issue stream connection lost', '#b91c1c')
      issueEventSource.close()
      issueEventSource = null
      store.isIssueStreaming = false
    }
  }

  function fetchRoadmap(options = {}) {
    const force = Boolean(options?.force)
    if (!store.repoUrl.trim()) {
      store.addLog('ℹ️ Enter a repository URL before generating roadmap.', '#f59e0b')
      return
    }
    if (store.isRoadmapStreaming) return
    if (!force && store.roadmapContent) return

    resetRoadmapBuffer()
    store.roadmapContent = ''
    store.isRoadmapStreaming = true
    store.activeInsightTab = 'roadmap'
    store.addLog('🗺️ Fetching commits for roadmap...', '#64748b')

    roadmapEventSource = createRoadmapStream(
      store.repoUrl,
      store.sessionId,
      store.language,
      force
    )

    roadmapEventSource.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.step === 'content_chunk') {
        roadmapChunkBuffer += data.chunk
        scheduleRoadmapFlush()
      } else if (data.step === 'finish') {
        flushRoadmapChunks()
        store.addLog(`✅ ${data.message}`, '#15803d')
        roadmapEventSource.close()
        roadmapEventSource = null
        store.isRoadmapStreaming = false
        resetRoadmapBuffer()
      } else if (data.step === 'error') {
        flushRoadmapChunks()
        store.addLog(`❌ ${data.message}`, '#b91c1c')
        roadmapEventSource.close()
        roadmapEventSource = null
        store.isRoadmapStreaming = false
        resetRoadmapBuffer()
      } else if (data.message) {
        store.addLog(`👉 ${data.message}`)
      }
    }

    roadmapEventSource.onerror = () => {
      flushRoadmapChunks()
      store.addLog('❌ Roadmap stream connection lost', '#b91c1c')
      roadmapEventSource.close()
      roadmapEventSource = null
      store.isRoadmapStreaming = false
      resetRoadmapBuffer()
    }
  }

  return {
    loadIssuesSnapshot,
    loadRoadmapSnapshot,
    fetchIssues,
    fetchRoadmap
  }
}
