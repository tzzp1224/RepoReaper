<template>
  <div class="chat-panel">
    <!-- 聊天历史 -->
    <div class="chat-history" ref="historyRef">
      <div 
        v-for="msg in store.chatMessages" 
        :key="msg.id"
        :class="['msg', msg.role]"
      >
        <div class="msg-content" v-html="renderMarkdown(msg.content)"></div>
      </div>
    </div>
    
    <!-- 输入栏 -->
    <div class="chat-input-bar">
      <input 
        type="text" 
        v-model="inputText"
        :placeholder="inputPlaceholder"
        :disabled="!store.chatEnabled || store.isChatGenerating"
        @keypress.enter="handleSend"
      />
      <button 
        :class="{ 'btn-stop': store.isChatGenerating }"
        :disabled="!store.chatEnabled && !store.isChatGenerating"
        @click="handleButtonClick"
      >
        {{ store.isChatGenerating ? 'Stop' : 'Send' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { marked } from 'marked'
import { useAppStore } from '../stores/app'
import { useChat } from '../composables/useChat'

const store = useAppStore()
const { sendMessage, stopGeneration } = useChat()

const inputText = ref('')
const historyRef = ref(null)

const inputPlaceholder = computed(() => {
  return store.chatEnabled 
    ? 'Please enter your question...' 
    : 'Waiting for analysis to complete...'
})

function renderMarkdown(content) {
  return marked.parse(content)
}

function handleSend() {
  if (inputText.value.trim() && !store.isChatGenerating) {
    sendMessage(inputText.value)
    inputText.value = ''
  }
}

function handleButtonClick() {
  if (store.isChatGenerating) {
    stopGeneration()
  } else {
    handleSend()
  }
}

// 自动滚动
watch(() => store.chatMessages, async () => {
  await nextTick()
  if (historyRef.value) {
    historyRef.value.scrollTop = historyRef.value.scrollHeight
  }
}, { deep: true })
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.msg {
  max-width: 85%;
  padding: 14px 20px;
  border-radius: 16px;
  font-size: 16px;
  line-height: 1.6;
  word-wrap: break-word;
  box-shadow: 0 2px 4px rgba(0,0,0,0.02);
}

.msg.user {
  align-self: flex-end;
  background: #e0f2fe;
  color: #0c4a6e;
  border-bottom-right-radius: 4px;
}

.msg.ai {
  align-self: flex-start;
  background: #ffffff;
  border: 1px solid var(--border-color);
  border-bottom-left-radius: 4px;
}

.msg-content :deep(p) {
  margin: 0.5em 0;
}

.msg-content :deep(p:first-child) {
  margin-top: 0;
}

.msg-content :deep(p:last-child) {
  margin-bottom: 0;
}

.msg-content :deep(code) {
  background: #f0f9ff;
  color: #0284c7;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.9em;
}

.msg-content :deep(pre) {
  background: #f1f5f9;
  padding: 16px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.5em 0;
}

.msg-content :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
}

/* 输入栏 */
.chat-input-bar {
  padding: 20px;
  border-top: 1px solid var(--border-color);
  background: #ffffff;
  display: flex;
  gap: 12px;
}

.chat-input-bar input {
  flex: 1;
  padding: 12px 16px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  outline: none;
  font-size: 16px;
  transition: all 0.2s;
  background: #f8fafc;
}

.chat-input-bar input:focus {
  border-color: var(--primary-color);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.chat-input-bar input:disabled {
  background: #f1f5f9;
  cursor: not-allowed;
}

.chat-input-bar button {
  background: var(--primary-color);
  color: white;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 500;
  font-size: 16px;
  transition: background 0.2s;
  white-space: nowrap;
}

.chat-input-bar button:hover:not(:disabled) {
  background: #1d4ed8;
}

.chat-input-bar button:disabled {
  background: #93c5fd;
  cursor: not-allowed;
}

.chat-input-bar button.btn-stop {
  background: #dc2626;
}

.chat-input-bar button.btn-stop:hover {
  background: #b91c1c;
}
</style>
