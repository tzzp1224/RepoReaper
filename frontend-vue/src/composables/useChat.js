import { ref } from 'vue'
import { useAppStore } from '../stores/app'
import { sendChatMessage } from '../api/repo'
import { marked } from 'marked'

/**
 * 聊天逻辑组合式函数
 */
export function useChat() {
  const store = useAppStore()
  const abortController = ref(null)
  
  /**
   * 发送消息
   */
  async function sendMessage(query) {
    if (!query.trim() || store.isChatGenerating) return
    
    if (!store.sessionId) {
      store.addChatMessage('ai', '❌ Please analyze a repository first.')
      return
    }
    
    // 添加用户消息
    store.addChatMessage('user', query)
    
    // 创建 AI 消息占位
    const msgId = store.addChatMessage('ai', '...')
    
    // 开始生成
    store.isChatGenerating = true
    abortController.value = new AbortController()
    
    try {
      const res = await sendChatMessage(
        query,
        store.sessionId,
        store.repoUrl,
        abortController.value.signal
      )
      
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let fullText = ''
      
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        
        const chunk = decoder.decode(value, { stream: true })
        fullText += chunk
        store.updateChatMessage(msgId, fullText)
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        store.updateChatMessage(msgId, store.chatMessages.find(m => m.id === msgId)?.content + '\n\n⏹️ Stopped')
      } else {
        store.updateChatMessage(msgId, `❌ Error: ${err.message}`)
      }
    } finally {
      store.isChatGenerating = false
      abortController.value = null
    }
  }
  
  /**
   * 停止生成
   */
  function stopGeneration() {
    abortController.value?.abort()
  }
  
  return {
    sendMessage,
    stopGeneration
  }
}
