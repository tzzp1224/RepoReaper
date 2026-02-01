import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { checkRepoSession } from '../api/repo'

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
  
  // === 按钮状态 ===
  const buttonState = ref(BTN_STATE.ANALYZE)
  
  // === 报告缓存 ===
  const cachedReports = ref({})  // { en: '...', zh: '...' }
  const currentReport = ref('')
  
  // === 提示系统 ===
  const hint = ref({ key: '', type: 'info' })
  
  // === 日志 ===
  const logs = ref([{ text: '[System] Ready to enter...', color: 'inherit' }])
  
  // === 聊天 ===
  const chatMessages = ref([
    { role: 'ai', content: '👋 Hi! Once the analysis is done, ask me anything about the code.' }
  ])
  const chatEnabled = ref(false)
  const isChatGenerating = ref(false)
  
  // === 检查结果缓存 ===
  const lastCheckResult = ref(null)
  
  // === 计算属性 ===
  const langLabel = computed(() => language.value === 'zh' ? '中文' : 'EN')
  const shortSessionId = computed(() => 
    sessionId.value ? sessionId.value.slice(-8) : '...'
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
  
  function cacheReport(lang, report) {
    cachedReports.value[lang] = report
  }
  
  function getCachedReport(lang) {
    return cachedReports.value[lang]
  }
  
  function clearCache() {
    cachedReports.value = {}
  }
  
  async function checkUrl() {
    if (!repoUrl.value.trim()) {
      buttonState.value = BTN_STATE.ANALYZE
      hideHint()
      return null
    }
    
    // URL 变化时清空缓存
    if (repoUrl.value !== currentRepoUrl.value) {
      clearCache()
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
      buttonState.value = BTN_STATE.REANALYZE
      chatEnabled.value = true
      setHint('reportReady', 'success')
      addLog(`✅ Found ${language.value.toUpperCase()} report (cached)`, '#15803d')
    } else if (result.has_index) {
      // 有索引无报告
      currentReport.value = ''
      buttonState.value = BTN_STATE.GENERATE
      chatEnabled.value = false
      setHint('canGenerate', 'info')
      addLog(`📚 Index found. Click Generate to create ${language.value.toUpperCase()} report.`, '#0ea5e9')
    } else {
      // 全新仓库
      currentReport.value = ''
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
    buttonState,
    cachedReports,
    currentReport,
    hint,
    logs,
    chatMessages,
    chatEnabled,
    isChatGenerating,
    lastCheckResult,
    
    // Computed
    langLabel,
    shortSessionId,
    buttonText,
    buttonDisabled,
    buttonClass,
    
    // Actions
    addLog,
    clearLogs,
    setHint,
    hideHint,
    addChatMessage,
    updateChatMessage,
    cacheReport,
    getCachedReport,
    clearCache,
    checkUrl,
    applyCheckResult
  }
})
