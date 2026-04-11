<template>
  <div class="insight-panel">
    <div class="markdown-body" ref="contentRef">
      <div v-if="!store.roadmapContent && !store.isRoadmapStreaming" class="placeholder">
        <div class="placeholder-icon">🗺️</div>
        <div class="placeholder-text">Click the button below to generate a feature roadmap from recent commits.</div>
        <button class="fetch-btn" @click="fetchRoadmap" :disabled="!store.sessionId">
          🗺️ Generate Roadmap
        </button>
      </div>
      <div v-else ref="htmlRef"></div>
    </div>
    <div v-if="store.isRoadmapStreaming" class="streaming-indicator">
      <span class="dot-pulse"></span> Generating commit roadmap...
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted } from 'vue'
import { marked } from 'marked'
import mermaid from 'mermaid'
import { useAppStore } from '../stores/app'
import { useInsights } from '../composables/useInsights'

const store = useAppStore()
const { fetchRoadmap } = useInsights()
const contentRef = ref(null)
const htmlRef = ref(null)

let renderTimeout = null
let mermaidRendered = false

onMounted(() => {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'neutral',
    securityLevel: 'loose'
  })
})

async function renderMermaidBlocks() {
  if (!htmlRef.value) return

  const codeBlocks = htmlRef.value.querySelectorAll('code.language-mermaid')
  if (codeBlocks.length === 0) return

  for (const codeBlock of codeBlocks) {
    const pre = codeBlock.parentElement
    if (!pre || pre.tagName !== 'PRE') continue

    const code = codeBlock.textContent.trim()
    const div = document.createElement('div')
    div.id = `mermaid-rm-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`
    div.className = 'mermaid'
    div.textContent = code
    pre.replaceWith(div)

    try {
      await mermaid.run({ nodes: [div] })
      const svg = div.querySelector('svg')
      if (svg) {
        div.style.overflowX = 'auto'
        svg.style.maxWidth = '100%'
      }
      mermaidRendered = true
    } catch (e) {
      console.error('[Mermaid/Roadmap] Render failed:', e)
      div.innerHTML = `<pre style="color:#dc2626;white-space:pre-wrap;">Mermaid render error:\n${code}</pre>`
    }
  }
}

function updateHtml(markdown) {
  if (!htmlRef.value || !markdown) return
  htmlRef.value.innerHTML = marked.parse(markdown)
}

watch(() => store.roadmapContent, async (val) => {
  if (!val) {
    mermaidRendered = false
    return
  }
  await nextTick()
  updateHtml(val)
})

watch(() => store.isRoadmapStreaming, async (streaming, was) => {
  if (was && !streaming && store.roadmapContent) {
    mermaidRendered = false
    await nextTick()
    updateHtml(store.roadmapContent)
    setTimeout(() => renderMermaidBlocks(), 300)
  }
})

watch(() => store.activeInsightTab, async (tab) => {
  if (tab === 'roadmap' && store.roadmapContent && !store.isRoadmapStreaming && !mermaidRendered) {
    await nextTick()
    updateHtml(store.roadmapContent)
    setTimeout(() => renderMermaidBlocks(), 100)
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

.markdown-body :deep(.mermaid) {
  display: flex;
  justify-content: center;
  margin: 20px 0;
  padding: 10px;
  border-radius: 8px;
  overflow-x: auto;
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
  background: linear-gradient(135deg, #0ea5e9, #06b6d4);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.fetch-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.4);
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
  background: #0ea5e9;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}
</style>
