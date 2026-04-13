<template>
  <div class="chat-shell">
    <div class="chat-header">
      <div class="chat-header-main">
        <div class="chat-title">Repo Chat</div>
        <div class="chat-status">
          <span class="chat-dot"></span>
          {{ repoLabel }}
        </div>
      </div>
      <svg class="sparkles-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M11 2.8 12.1 6l3.1 1.1-3.1 1.1L11 11.4 9.9 8.2 6.8 7.1 9.9 6z" />
        <path d="M5.3 10.6 6 12.7l2.1.7-2.1.7-.7 2.1-.7-2.1-2.1-.7 2.1-.7z" />
      </svg>
    </div>

    <div ref="historyRef" class="chat-history">
      <div class="system-row">
        <span class="system-pill">{{ systemStatusText }}</span>
      </div>

      <div
        v-for="(msg, index) in store.chatMessages"
        :key="msg.id || index"
        :class="['chat-message', msg.role]"
      >
        <div v-if="msg.role === 'user'" class="user-bubble">
          <div class="user-text">{{ msg.content }}</div>
        </div>

        <div v-else-if="msg.role === 'system'" class="system-row">
          <span class="system-pill">{{ msg.content }}</span>
        </div>

        <div v-else class="assistant-bubble">
          <div class="assistant-text" v-html="renderMarkdown(normalizeAssistantContent(msg.content))"></div>
        </div>
      </div>
    </div>

    <div v-if="showSuggestions" class="suggestions">
      <p>Suggested questions</p>
      <button
        v-for="question in suggestedQuestions"
        :key="question"
        type="button"
        @click="handleSuggestion(question)"
      >
        {{ question }}
      </button>
    </div>

    <div class="chat-input-shell">
      <div class="chat-input-wrap">
        <textarea
          v-model="inputText"
          rows="1"
          :placeholder="inputPlaceholder"
          :disabled="!store.chatEnabled || store.isChatGenerating"
          @keypress.enter.exact.prevent="handleSend"
        ></textarea>
        <button
          type="button"
          :disabled="!store.chatEnabled && !store.isChatGenerating"
          :class="{ stop: store.isChatGenerating }"
          @click="handleButtonClick"
        >
          <span v-if="store.isChatGenerating">Stop</span>
          <svg v-else viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M4 10 16 4l-3 12-3-5z" />
            <path d="m10 11 6-7" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { marked } from 'marked'
import { useAppStore } from '../stores/app'
import { useChat } from '../composables/useChat'

const store = useAppStore()
const { sendMessage, stopGeneration } = useChat()

const inputText = ref('')
const historyRef = ref(null)

const repoLabel = computed(() => store.repoUrl.trim() || 'No repository selected')
const systemStatusText = computed(() => (
  store.chatEnabled ? 'Analysis complete - ready to chat.' : 'Analyze a repository first.'
))
const inputPlaceholder = computed(() => {
  return store.chatEnabled
    ? 'Ask anything about the repository...'
    : 'Analyze a repository first...'
})
const suggestedQuestions = [
  '这个项目主要的功能模块是什么？',
  '项目的技术栈和依赖有哪些？',
  'RAG 检索流程是怎么设计的？'
]
const showSuggestions = computed(() => store.chatMessages.length <= 2 && !store.isChatGenerating)

function renderMarkdown(content) {
  return marked.parse(content || '')
}

function normalizeAssistantContent(content) {
  const source = content || ''
  if (source.includes('Once the analysis is done')) {
    return 'Hi! Once the analysis is done, ask me anything about the code.'
  }
  return source
}

function handleSend() {
  if (!inputText.value.trim() || store.isChatGenerating) return
  sendMessage(inputText.value)
  inputText.value = ''
}

function handleButtonClick() {
  if (store.isChatGenerating) {
    stopGeneration()
    return
  }
  handleSend()
}

function handleSuggestion(question) {
  sendMessage(question)
}

watch(
  () => store.chatMessages,
  async () => {
    await nextTick()
    if (historyRef.value) {
      historyRef.value.scrollTop = historyRef.value.scrollHeight
    }
  },
  { deep: true }
)
</script>

<style scoped>
.chat-shell {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--shell-bg);
  min-height: 0;
  min-width: 0;
  box-sizing: border-box;
}

.chat-header {
  width: 100%;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color);
  background: #fff;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 12px;
}

.chat-header-main {
  flex: 1;
  min-width: 0;
}

.chat-title {
  font-size: 14px;
  font-weight: 600;
  color: #1c1917;
}

.chat-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: #78716c;
  min-width: 0;
}

.chat-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: #10b981;
}

.sparkles-icon {
  width: 16px;
  height: 16px;
  color: #d6d3d1;
  flex-shrink: 0;
}

.chat-history {
  flex: 1;
  width: 100%;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  box-sizing: border-box;
}

.chat-message.user {
  display: flex;
  justify-content: flex-end;
  min-width: 0;
}

.chat-message.ai {
  display: flex;
  justify-content: flex-start;
  min-width: 0;
}

.system-row {
  display: flex;
  justify-content: center;
  min-width: 0;
}

.system-pill {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 4px 12px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: #f5f5f4;
  color: #a8a29e;
  font-size: 12px;
  line-height: 1.2;
  text-align: center;
}

.user-bubble {
  max-width: 88%;
  min-width: 0;
  background: #292524;
  color: #fff;
  border-radius: 12px 12px 4px 12px;
  padding: 11px 12px;
}

.assistant-bubble {
  max-width: 100%;
  min-width: 0;
  color: #44403c;
}

.user-text,
.assistant-text {
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.assistant-text {
  color: #44403c;
}

.assistant-text :deep(p) {
  margin: 0.45em 0;
}

.assistant-text :deep(p:first-child) {
  margin-top: 0;
}

.assistant-text :deep(p:last-child) {
  margin-bottom: 0;
}

.suggestions {
  width: 100%;
  padding: 0 16px 8px;
  min-width: 0;
  box-sizing: border-box;
}

.suggestions p {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 500;
  color: #a8a29e;
}

.suggestions button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 6px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: #fff;
  color: #57534e;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.suggestions button + button {
  margin-top: 4px;
}

.suggestions button:hover {
  background: #fafaf9;
  border-color: #d6d3d1;
}

.chat-input-shell {
  width: 100%;
  border-top: 1px solid var(--border-color);
  background: #fff;
  padding: 12px;
  min-width: 0;
  box-sizing: border-box;
}

.chat-input-wrap {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 8px;
  border: 1px solid var(--border-color);
  background: #fafaf9;
  border-radius: 12px;
  padding: 8px 8px 8px 12px;
  min-width: 0;
  box-sizing: border-box;
}

.chat-input-wrap textarea {
  flex: 1;
  min-width: 0;
  resize: none;
  border: 0;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: #1c1917;
  line-height: 24px;
  min-height: 24px;
  max-height: 120px;
}

.chat-input-wrap button {
  border: 0;
  background: #292524;
  color: #fff;
  border-radius: 8px;
  width: 30px;
  height: 30px;
  padding: 0;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.chat-input-wrap button.stop {
  width: auto;
  padding: 0 10px;
  background: #b91c1c;
}

.chat-input-wrap button svg {
  width: 14px;
  height: 14px;
}

.chat-input-wrap button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
