<template>
  <div class="insight-panel">
    <div class="markdown-body" ref="contentRef">
      <div v-if="!store.issueNotes && !store.isIssueStreaming" class="placeholder">
        <div class="placeholder-icon">📋</div>
        <div class="placeholder-title">Issues Notebook</div>
        <div class="placeholder-text">Run analysis to surface identified code issues and suggestions.</div>
        <button class="fetch-btn" @click="fetchIssues" :disabled="!store.sessionId">
          📋 Fetch Issues
        </button>
      </div>
      <div v-else v-html="parsedHtml"></div>
    </div>
    <div v-if="store.isIssueStreaming" class="streaming-indicator">
      <span class="dot-pulse"></span> Generating issue summary...
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { useInsights } from '../composables/useInsights'
import { renderMarkdownSafe } from '../utils/markdownSafe'

const store = useAppStore()
const { fetchIssues } = useInsights()

const parsedHtml = computed(() => (store.issueNotes ? renderMarkdownSafe(store.issueNotes) : ''))
</script>

<style scoped>
.insight-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #faf9f6;
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
  height: 100%;
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 8px;
  padding: 32px;
  color: #a8a29e;
}

.placeholder-icon {
  width: 48px;
  height: 48px;
  border-radius: 16px;
  background: #f5f5f4;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 4px;
  color: transparent;
  font-size: 0;
}

.placeholder-icon::before {
  content: "";
  width: 20px;
  height: 16px;
  border: 2px solid #a8a29e;
  border-top-width: 1px;
  border-radius: 2px;
  box-shadow: inset 8px 0 0 #f5f5f4, inset 10px 0 0 #a8a29e;
}

.placeholder-title {
  font-size: 14px;
  font-weight: 500;
  color: #57534e;
}

.placeholder-text {
  font-size: 12px;
  color: #a8a29e;
}

.fetch-btn {
  margin-top: 6px;
  padding: 7px 12px;
  font-size: 0;
  font-weight: 500;
  color: #57534e;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.fetch-btn::after {
  content: "Fetch Issues";
  font-size: 12px;
}

.fetch-btn:hover:not(:disabled) {
  background: #f5f5f4;
  border-color: #d6d3d1;
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
  background: #1b7f48;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}
</style>
