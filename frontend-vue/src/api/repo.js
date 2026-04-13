/**
 * 仓库 API 服务
 */

const API_BASE = ''

/**
 * 检查仓库状态
 */
export async function checkRepoSession(repoUrl, language = 'en') {
  try {
    const response = await fetch(`${API_BASE}/api/repo/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: repoUrl, language })
    })
    return await response.json()
  } catch (e) {
    console.error('Check repo session failed:', e)
    return { exists: false, has_index: false, available_languages: [] }
  }
}

/**
 * 创建分析 SSE 连接
 */
export function createAnalysisStream(url, sessionId, language, regenerateOnly) {
  const params = new URLSearchParams({
    url,
    session_id: sessionId,
    language,
    regenerate_only: regenerateOnly.toString()
  })
  return new EventSource(`${API_BASE}/analyze?${params}`)
}

/**
 * 创建 Issue 摘要 SSE 连接
 */
export function createIssueStream(url, sessionId, language) {
  const params = new URLSearchParams({ url, session_id: sessionId, language })
  return new EventSource(`${API_BASE}/api/insights/issues?${params}`)
}

/**
 * 创建 Commit Roadmap SSE 连接
 */
export function createRoadmapStream(url, sessionId, language) {
  const params = new URLSearchParams({ url, session_id: sessionId, language })
  return new EventSource(`${API_BASE}/api/insights/commits?${params}`)
}

/**
 * 发送聊天消息（流式）
 */
export async function sendChatMessage(query, sessionId, repoUrl, signal) {
  return fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, session_id: sessionId, repo_url: repoUrl }),
    signal
  })
}

export async function fetchReproScore(sessionId, repoUrl, options = {}) {
  const response = await fetch(`${API_BASE}/api/repro/score`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId || undefined,
      repo_url: repoUrl || undefined,
      ...options
    })
  })
  return await response.json()
}

export async function fetchPaperAlign(payload) {
  const response = await fetch(`${API_BASE}/api/paper/align`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  return await response.json()
}
