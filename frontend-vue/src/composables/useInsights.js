import { useAppStore } from '../stores/app'
import { createIssueStream, createRoadmapStream } from '../api/repo'

/**
 * Insights 逻辑组合式函数 — Issue 摘要 & Commit Roadmap
 */
export function useInsights() {
  const store = useAppStore()

  let issueEventSource = null
  let roadmapEventSource = null

  function fetchIssues() {
    if (!store.repoUrl.trim() || !store.sessionId) return
    if (store.isIssueStreaming) return

    store.issueNotes = ''
    store.isIssueStreaming = true
    store.activeInsightTab = 'issues'
    store.addLog('📋 Fetching issues...', '#64748b')

    issueEventSource = createIssueStream(
      store.repoUrl,
      store.sessionId,
      store.language
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

  function fetchRoadmap() {
    if (!store.repoUrl.trim() || !store.sessionId) return
    if (store.isRoadmapStreaming) return

    store.roadmapContent = ''
    store.isRoadmapStreaming = true
    store.activeInsightTab = 'roadmap'
    store.addLog('🗺️ Fetching commits for roadmap...', '#64748b')

    roadmapEventSource = createRoadmapStream(
      store.repoUrl,
      store.sessionId,
      store.language
    )

    roadmapEventSource.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.step === 'content_chunk') {
        store.roadmapContent += data.chunk
      } else if (data.step === 'finish') {
        store.addLog(`✅ ${data.message}`, '#15803d')
        roadmapEventSource.close()
        roadmapEventSource = null
        store.isRoadmapStreaming = false
      } else if (data.step === 'error') {
        store.addLog(`❌ ${data.message}`, '#b91c1c')
        roadmapEventSource.close()
        roadmapEventSource = null
        store.isRoadmapStreaming = false
      } else if (data.message) {
        store.addLog(`👉 ${data.message}`)
      }
    }

    roadmapEventSource.onerror = () => {
      store.addLog('❌ Roadmap stream connection lost', '#b91c1c')
      roadmapEventSource.close()
      roadmapEventSource = null
      store.isRoadmapStreaming = false
    }
  }

  return {
    fetchIssues,
    fetchRoadmap
  }
}
