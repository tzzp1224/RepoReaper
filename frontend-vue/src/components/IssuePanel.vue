<template>
  <div class="insight-panel">
    <div class="markdown-body" ref="contentRef">
      <div v-if="!store.issueNotes && !store.isIssueStreaming" class="placeholder">
        <div class="placeholder-icon">📋</div>
        <div class="placeholder-text">Click the button below to fetch and summarize issues.</div>
        <button class="fetch-btn" @click="fetchIssues" :disabled="!store.sessionId">
          📋 Fetch Issues
        </button>
      </div>
      <div v-else ref="htmlRef"></div>
    </div>
    <div v-if="store.isIssueStreaming" class="streaming-indicator">
      <span class="dot-pulse"></span> Generating issue summary...
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { marked } from 'marked'
import { useAppStore } from '../stores/app'
import { useInsights } from '../composables/useInsights'

const store = useAppStore()
const { fetchIssues } = useInsights()
const contentRef = ref(null)
const htmlRef = ref(null)

watch(() => store.issueNotes, async (val) => {
  if (!val) return
  await nextTick()
  if (htmlRef.value) {
    htmlRef.value.innerHTML = marked.parse(val)
  }
})
</script>

<style scoped>
.insight-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-color);
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

.markdown-body {
  flex: 1;
  padding: 20px 24px;
  overflow-y: auto;
  font-size: 15px;
  line-height: 1.7;
  color: var(--text-primary);
}

.placeholder {
  text-align: center;
  color: #94a3b8;
  margin-top: 60px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.placeholder-icon {
  font-size: 40px;
}

.placeholder-text {
  font-size: 16px;
}

.fetch-btn {
  margin-top: 8px;
  padding: 10px 24px;
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.fetch-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4);
}

.fetch-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.streaming-indicator {
  padding: 8px 16px;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary);
  border-top: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.dot-pulse {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #6366f1;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}
</style>
