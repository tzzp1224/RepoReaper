import { useAppStore, BTN_STATE } from '../stores/app'
import { checkRepoSession, createAnalysisStream } from '../api/repo'

/**
 * 分析逻辑组合式函数
 */
export function useAnalysis() {
  const store = useAppStore()
  
  /**
   * 处理分析按钮点击
   */
  async function handleAnalyzeClick() {
    if (!store.repoUrl.trim()) {
      alert('Please enter a GitHub repository URL')
      return
    }
    
    switch (store.buttonState) {
      case BTN_STATE.ANALYZE:
        await startAnalysis(false)
        break
      case BTN_STATE.GENERATE:
        await startAnalysis(true)
        break
      case BTN_STATE.REANALYZE:
        if (confirm('This will re-analyze the repository from scratch.\n\nContinue?')) {
          delete store.cachedReports[store.language]
          await startAnalysis(false)
        }
        break
    }
  }
  
  /**
   * 开始分析
   */
  async function startAnalysis(regenerateOnly = false) {
    store.hideHint()
    store.buttonState = BTN_STATE.ANALYZING
    store.chatEnabled = false
    
    // 获取 session ID
    if (!store.sessionId || store.repoUrl !== store.currentRepoUrl) {
      const result = await checkRepoSession(store.repoUrl, store.language)
      store.sessionId = result.session_id
    }
    store.currentRepoUrl = store.repoUrl
    
    // 清空报告
    store.currentReport = ''
    
    const actionText = regenerateOnly ? '📝 Generating report (reusing index)' : '🚀 Starting full analysis'
    store.clearLogs()
    store.addLog(`[System] ${actionText} (${store.language.toUpperCase()})...`)
    
    // 标记开始流式输出
    store.isStreaming = true
    
    // SSE 流
    const eventSource = createAnalysisStream(
      store.repoUrl,
      store.sessionId,
      store.language,
      regenerateOnly
    )
    
    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data)
      
      if (data.step === 'report_chunk') {
        store.currentReport += data.chunk
      } else if (data.step === 'finish') {
        store.addLog(`✅ ${data.message}`, '#15803d')
        eventSource.close()
        
        // 标记流式输出结束
        store.isStreaming = false
        
        store.cacheReport(store.language, store.currentReport)
        store.buttonState = BTN_STATE.REANALYZE
        store.setHint('reportReady', 'success')
        store.chatEnabled = true
        store.addChatMessage('ai', '🎉 Analysis complete! You can ask questions now.')
      } else if (data.step === 'error') {
        store.addLog(`❌ ${data.message}`, '#b91c1c')
        eventSource.close()
        
        // 标记流式输出结束
        store.isStreaming = false
        
        if (store.lastCheckResult?.has_index) {
          store.buttonState = BTN_STATE.GENERATE
        } else {
          store.buttonState = BTN_STATE.ANALYZE
        }
      } else {
        store.addLog(`👉 ${data.message}`)
      }
    }
    
    eventSource.onerror = () => {
      store.addLog('❌ Connection lost', '#b91c1c')
      eventSource.close()
      store.isStreaming = false
      store.buttonState = BTN_STATE.ANALYZE
    }
  }
  
  /**
   * 处理语言切换
   */
  async function handleLanguageChange(newLang) {
    store.language = newLang
    
    if (!store.repoUrl.trim()) return
    
    // 检查本地缓存
    const cached = store.getCachedReport(newLang)
    if (cached) {
      store.currentReport = cached
      store.buttonState = BTN_STATE.REANALYZE
      store.chatEnabled = true
      store.setHint('langSwitched', 'success')
      store.addLog(`🔄 Switched to ${newLang.toUpperCase()} report (from cache)`, '#0ea5e9')
      return
    }
    
    // 检查后端
    store.buttonState = BTN_STATE.CHECKING
    store.hideHint()
    store.addLog(`🔍 Checking ${newLang.toUpperCase()} report...`, '#64748b')
    
    try {
      const result = await checkRepoSession(store.repoUrl, newLang)
      store.sessionId = result.session_id
      store.lastCheckResult = result
      
      if (result.exists && result.report) {
        store.cacheReport(newLang, result.report)
        store.currentReport = result.report
        store.buttonState = BTN_STATE.REANALYZE
        store.chatEnabled = true
        store.setHint('langSwitched', 'success')
        store.addLog(`📦 Loaded ${newLang.toUpperCase()} report`, '#15803d')
      } else if (result.has_index) {
        store.currentReport = ''
        store.buttonState = BTN_STATE.GENERATE
        store.chatEnabled = false
        store.setHint('langNeedGenerate', 'info')
        store.addLog(`ℹ️ No ${newLang.toUpperCase()} report. Click Generate.`, '#f59e0b')
      } else {
        store.currentReport = ''
        store.buttonState = BTN_STATE.ANALYZE
        store.chatEnabled = false
        store.setHint('needAnalyze', 'warning')
      }
    } catch (e) {
      console.error('Language switch check failed:', e)
      store.buttonState = BTN_STATE.ANALYZE
    }
  }
  
  return {
    handleAnalyzeClick,
    startAnalysis,
    handleLanguageChange
  }
}
