<template>
  <div class="insight-panel">
    <div v-if="store.issueNotes" class="panel-toolbar">
      <button class="refresh-btn" :disabled="!canRefresh" @click="refreshIssues">
        Refresh
      </button>
    </div>
    <div class="markdown-body" ref="contentRef">
      <div v-if="!store.issueNotes && !store.isIssueStreaming" class="placeholder">
        <div class="placeholder-icon" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4.5 4.5h4.8a2 2 0 0 1 1.4.58l.3.3a2 2 0 0 0 1.4.58h3v9H10.6a2.5 2.5 0 0 0-1.77.73l-.23.23-.23-.23A2.5 2.5 0 0 0 6.6 15H4.5z" />
            <path d="M10 5v10.5" />
          </svg>
        </div>
        <div class="placeholder-title">Issues Notebook</div>
        <div class="placeholder-text">Issue summary will be generated here.</div>
        <button class="fetch-btn" @click="generateIssues" :disabled="!canRefresh">
          Generate Issues
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
import { computed, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { useInsights } from '../composables/useInsights'
import { renderMarkdownSafe } from '../utils/markdownSafe'

const store = useAppStore()
const { fetchIssues, loadIssuesSnapshot } = useInsights()

const parsedHtml = computed(() => (store.issueNotes ? renderMarkdownSafe(store.issueNotes) : ''))
const canRefresh = computed(() => Boolean(store.repoUrl.trim()) && !store.isIssueStreaming)

function generateIssues() {
  fetchIssues({ force: true })
}

function refreshIssues() {
  fetchIssues({ force: true })
}

watch(
  () => [store.sessionId, store.language],
  () => {
    if (store.isIssueStreaming) return
    if (!store.repoUrl.trim()) return
    loadIssuesSnapshot()
  },
  { immediate: true }
)
</script>

<style scoped>
.insight-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #faf9f6;
}

.panel-toolbar {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color);
  background: #fff;
  display: flex;
  justify-content: flex-end;
}

.refresh-btn {
  padding: 7px 12px;
  font-size: 12px;
  font-weight: 500;
  color: #57534e;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  background: #f5f5f4;
  border-color: #d6d3d1;
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
  color: #78716c;
}

.placeholder-icon svg {
  width: 20px;
  height: 20px;
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
  font-size: 12px;
  font-weight: 500;
  color: #57534e;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
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
