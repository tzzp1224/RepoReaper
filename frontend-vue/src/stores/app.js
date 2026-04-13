import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { checkRepoSession } from '../api/repo'
import { extractCompiledPaperText, mergePaperHighlight, normalizePaperHighlights } from '../utils/paperHighlights'

const INITIAL_CHAT_MESSAGE = '👋 Hi! Once the analysis is done, ask me anything about the code.'

function createInitialChatMessage() {
  return {
    id: `msg-welcome-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    role: 'ai',
    content: INITIAL_CHAT_MESSAGE
  }
}

// 按钮状态枚举
export const BTN_STATE = {
  ANALYZE: 'analyze',
  GENERATE: 'generate',
  REANALYZE: 'reanalyze',
  CHECKING: 'checking',
  ANALYZING: 'analyzing'
}

export const useAppStore = defineStore('app', () => {
  // === 核心状态 ===
  const repoUrl = ref('')
  const language = ref('en')
  const sessionId = ref(null)
  const currentRepoUrl = ref('')  // 已分析的 URL
  const hasAnalyzedContext = ref(false)
  
  // === 按钮状态 ===
  const buttonState = ref(BTN_STATE.ANALYZE)
  
  // === 报告缓存 ===
  const cachedReports = ref({})  // { en: '...', zh: '...' }
  const currentReport = ref('')
  
  // === 流式输出状态 ===
  const isStreaming = ref(false)  // 标记是否正在流式输出报告
  
  // === 提示系统 ===
  const hint = ref({ key: '', type: 'info' })
  
  // === 日志 ===
  const logs = ref([{ text: '[System] Ready to enter...', color: 'inherit' }])
  
  // === 聊天 ===
  const chatMessages = ref([
    createInitialChatMessage()
  ])
  const chatEnabled = ref(false)
  const isChatGenerating = ref(false)
  
  // === 检查结果缓存 ===
  const lastCheckResult = ref(null)
  
  // === Insights: Issue 摘要 & Commit Roadmap ===
  const activeInsightTab = ref('report')  // 'report' | 'score' | 'issues' | 'roadmap'
  const issueNotes = ref('')
  const roadmapContent = ref('')
  const isIssueStreaming = ref(false)
  const isRoadmapStreaming = ref(false)

  const scoreResult = ref(null)
  const scoreLoading = ref(false)
  const scoreError = ref('')

  const paperAlignText = ref('')
  const paperAlignTopK = ref(5)
  const paperAlignResult = ref(null)
  const paperAlignLoading = ref(false)
  const paperAlignError = ref('')
  const paperUploadedFileName = ref('')
  const paperHighlightMode = ref(false)
  const paperHighlights = ref([])
  const paperPdfFile = ref(null)
  const paperPdfPages = ref([])
  const paperSelections = ref([])
  const paperSelectionMode = ref('text')
  
  // === 计算属性 ===
  const langLabel = computed(() => language.value === 'zh' ? '中文' : 'EN')
  const shortSessionId = computed(() => 
    sessionId.value ? sessionId.value.slice(-8) : '........'
  )
  
  const buttonText = computed(() => {
    switch (buttonState.value) {
      case BTN_STATE.ANALYZE: return '🔍 Analyze'
      case BTN_STATE.GENERATE: return `🌐 Generate ${langLabel.value}`
      case BTN_STATE.REANALYZE: return '🔄 Reanalyze'
      case BTN_STATE.CHECKING: return '⏳ Checking...'
      case BTN_STATE.ANALYZING: return '⏳ Analyzing...'
      default: return 'Analyze'
    }
  })
  
  const buttonDisabled = computed(() => 
    [BTN_STATE.CHECKING, BTN_STATE.ANALYZING].includes(buttonState.value)
  )
  
  const buttonClass = computed(() => {
    const map = {
      [BTN_STATE.ANALYZE]: 'btn-analyze',
      [BTN_STATE.GENERATE]: 'btn-generate',
      [BTN_STATE.REANALYZE]: 'btn-reanalyze',
      [BTN_STATE.CHECKING]: 'btn-checking',
      [BTN_STATE.ANALYZING]: 'btn-checking'
    }
    return map[buttonState.value] || 'btn-analyze'
  })

  const isCurrentRepoContext = computed(() =>
    Boolean(repoUrl.value.trim()) &&
    Boolean(currentRepoUrl.value) &&
    repoUrl.value === currentRepoUrl.value
  )

  const canUseAnalyzedContext = computed(() =>
    hasAnalyzedContext.value &&
    isCurrentRepoContext.value &&
    Boolean(sessionId.value)
  )

  const compiledPaperText = computed(() => {
    if (paperSelectionMode.value === 'pdf' && paperSelections.value.length > 0) {
      return paperSelections.value.map(s => s.text).join('\n\n')
    }
    return extractCompiledPaperText(paperAlignText.value, paperHighlights.value)
  })

  // === Actions ===
  function addLog(text, color = 'inherit') {
    logs.value.push({ text, color })
  }
  
  function clearLogs() {
    logs.value = []
  }
  
  function setHint(key, type = 'info') {
    hint.value = { key, type }
  }
  
  function hideHint() {
    hint.value = { key: '', type: 'info' }
  }
  
  function addChatMessage(role, content) {
    const id = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
    chatMessages.value.push({ id, role, content })
    return id
  }
  
  function updateChatMessage(id, content) {
    const msg = chatMessages.value.find(m => m.id === id)
    if (msg) msg.content = content
  }

  function resetChatMessages() {
    chatMessages.value = [createInitialChatMessage()]
    isChatGenerating.value = false
  }
  
  function cacheReport(lang, report) {
    cachedReports.value[lang] = report
  }
  
  function getCachedReport(lang) {
    return cachedReports.value[lang]
  }
  
  function clearCache() {
    cachedReports.value = {}
  }

  function resetScoreState() {
    scoreResult.value = null
    scoreLoading.value = false
    scoreError.value = ''
  }

  function resetInsightsState() {
    issueNotes.value = ''
    roadmapContent.value = ''
    isIssueStreaming.value = false
    isRoadmapStreaming.value = false
  }

  function resetPaperAlignState() {
    paperAlignText.value = ''
    paperAlignTopK.value = 5
    paperAlignResult.value = null
    paperAlignLoading.value = false
    paperAlignError.value = ''
    paperUploadedFileName.value = ''
    paperHighlightMode.value = false
    paperHighlights.value = []
    paperPdfFile.value = null
    paperPdfPages.value = []
    paperSelections.value = []
    paperSelectionMode.value = 'text'
  }

  function setPaperUploadedFileName(name) {
    paperUploadedFileName.value = name || ''
  }

  function setPaperHighlightMode(enabled) {
    paperHighlightMode.value = Boolean(enabled)
  }

  function setPaperHighlights(highlights) {
    paperHighlights.value = normalizePaperHighlights(highlights)
  }

  function addPaperHighlight(start, end) {
    paperHighlights.value = mergePaperHighlight(
      paperHighlights.value,
      start,
      end,
      `hl-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    )
  }

  function removePaperHighlight(id) {
    paperHighlights.value = paperHighlights.value.filter(item => item.id !== id)
  }

  function clearPaperHighlights() {
    paperHighlights.value = []
  }

  function setPaperPdfFile(file) {
    paperPdfFile.value = file
  }

  function setPaperPdfPages(pages) {
    paperPdfPages.value = pages
  }

  function addPaperSelection(text, meta = {}) {
    const cleaned = (text || '').replace(/\s+/g, ' ').trim()
    if (!cleaned) return

    const pageNumber = meta?.pageNumber ?? null
    const duplicate = paperSelections.value.some(item => {
      return item.text === cleaned && (item.meta?.pageNumber ?? null) === pageNumber
    })
    if (duplicate) return

    paperSelections.value.unshift({
      id: `sel-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      text: cleaned,
      meta: { ...meta }
    })
  }

  function removePaperSelection(id) {
    paperSelections.value = paperSelections.value.filter(item => item.id !== id)
  }

  function clearPaperSelections() {
    paperSelections.value = []
  }
  
  async function checkUrl() {
    if (!repoUrl.value.trim()) {
      buttonState.value = BTN_STATE.ANALYZE
      hideHint()
      hasAnalyzedContext.value = false
      currentRepoUrl.value = ''
      return null
    }
    
    // URL 变化时清空缓存
    if (repoUrl.value !== currentRepoUrl.value) {
      clearCache()
      resetInsightsState()
      resetScoreState()
      resetPaperAlignState()
      hasAnalyzedContext.value = false
      currentRepoUrl.value = ''
      chatEnabled.value = false
    }
    
    buttonState.value = BTN_STATE.CHECKING
    addLog('🔍 Checking repository status...', '#64748b')
    
    try {
      const result = await checkRepoSession(repoUrl.value, language.value)
      sessionId.value = result.session_id
      lastCheckResult.value = result
      applyCheckResult(result)
      return result
    } catch (e) {
      console.error('Check failed:', e)
      buttonState.value = BTN_STATE.ANALYZE
      return null
    }
  }
  
  function applyCheckResult(result) {
    if (result.exists && result.report) {
      // 有报告
      currentReport.value = result.report
      cacheReport(language.value, result.report)
      hasAnalyzedContext.value = true
      currentRepoUrl.value = repoUrl.value
      buttonState.value = BTN_STATE.REANALYZE
      chatEnabled.value = true
      setHint('reportReady', 'success')
      addLog(`✅ Found ${language.value.toUpperCase()} report (cached)`, '#15803d')
    } else if (result.has_index) {
      // 有索引无报告
      currentReport.value = ''
      hasAnalyzedContext.value = true
      currentRepoUrl.value = repoUrl.value
      buttonState.value = BTN_STATE.GENERATE
      chatEnabled.value = false
      setHint('canGenerate', 'info')
      addLog(`📚 Index found. Click Generate to create ${language.value.toUpperCase()} report.`, '#0ea5e9')
    } else {
      // 全新仓库
      currentReport.value = ''
      hasAnalyzedContext.value = false
      currentRepoUrl.value = ''
      buttonState.value = BTN_STATE.ANALYZE
      chatEnabled.value = false
      setHint('needAnalyze', 'warning')
      addLog('🆕 New repository. Click Analyze to start.', '#64748b')
    }
  }
  
  return {
    // State
    repoUrl,
    language,
    sessionId,
    currentRepoUrl,
    hasAnalyzedContext,
    buttonState,
    cachedReports,
    currentReport,
    isStreaming,
    hint,
    logs,
    chatMessages,
    chatEnabled,
    isChatGenerating,
    lastCheckResult,
    
    // Insights
    activeInsightTab,
    issueNotes,
    roadmapContent,
    isIssueStreaming,
    isRoadmapStreaming,
    scoreResult,
    scoreLoading,
    scoreError,
    paperAlignText,
    paperAlignTopK,
    paperAlignResult,
    paperAlignLoading,
    paperAlignError,
    paperUploadedFileName,
    paperHighlightMode,
    paperHighlights,
    paperPdfFile,
    paperPdfPages,
    paperSelections,
    paperSelectionMode,
    
    // Computed
    langLabel,
    shortSessionId,
    buttonText,
    buttonDisabled,
    buttonClass,
    canUseAnalyzedContext,
    compiledPaperText,
    
    // Actions
    addLog,
    clearLogs,
    setHint,
    hideHint,
    addChatMessage,
    updateChatMessage,
    resetChatMessages,
    cacheReport,
    getCachedReport,
    clearCache,
    resetInsightsState,
    resetScoreState,
    resetPaperAlignState,
    setPaperUploadedFileName,
    setPaperHighlightMode,
    setPaperHighlights,
    addPaperHighlight,
    removePaperHighlight,
    clearPaperHighlights,
    setPaperPdfFile,
    setPaperPdfPages,
    addPaperSelection,
    removePaperSelection,
    clearPaperSelections,
    checkUrl,
    applyCheckResult
  }
})
