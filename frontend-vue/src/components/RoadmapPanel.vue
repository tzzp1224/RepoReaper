<template>
  <div class="insight-panel">
    <div class="markdown-body" ref="contentRef">
      <div v-if="!store.roadmapContent && !store.isRoadmapStreaming" class="placeholder">
        <div class="placeholder-icon" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5" cy="10" r="1.8" />
            <circle cx="15" cy="6" r="1.8" />
            <circle cx="15" cy="14" r="1.8" />
            <path d="M6.8 10h3.4a2 2 0 0 0 1.41-.59L13.2 7.8" />
            <path d="M10.2 10a2 2 0 0 1 1.41.59l1.59 1.61" />
          </svg>
        </div>
        <div class="placeholder-title">Commit Roadmap</div>
        <div class="placeholder-text">AI-generated improvement roadmap will appear here after analysis.</div>
        <button class="fetch-btn" @click="fetchRoadmap" :disabled="!store.sessionId">
          🗺️ Generate Roadmap
        </button>
      </div>
      <div v-show="store.roadmapContent || store.isRoadmapStreaming" ref="htmlRef"></div>
    </div>
    <div v-if="store.isRoadmapStreaming" class="streaming-indicator">
      <span class="dot-pulse"></span> Generating commit roadmap...
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import mermaid from 'mermaid'
import { useAppStore } from '../stores/app'
import { useInsights } from '../composables/useInsights'
import { renderMarkdownSafe } from '../utils/markdownSafe'
import {
  initializeMermaid,
  sanitizeMermaidCode,
  createMermaidErrorHtml,
  bindMermaidZoom
} from '../composables/useMermaidShared'

const store = useAppStore()
const { fetchRoadmap } = useInsights()
const contentRef = ref(null)
const htmlRef = ref(null)
const emit = defineEmits(['openModal'])

const RENDER_THROTTLE_MS = 400
let mermaidRenderTimeout = null
const isRendering = ref(false)
let lastRenderTime = 0
const renderedMermaidCache = new Map()

// 初始化 Mermaid，并在挂载时恢复已有内容（如从 PaperAlign 返回后重新挂载）
onMounted(() => {
  initializeMermaid()
  if (store.roadmapContent && !store.isRoadmapStreaming) {
    nextTick(() => {
      updateHtml(store.roadmapContent)
      if (store.roadmapContent.includes('```mermaid')) {
        setTimeout(() => renderAllCompleteMermaidBlocks(true), 150)
      }
    })
  }
})

onUnmounted(() => {
  clearMermaidRenderTimeout()
})

function clearMermaidRenderTimeout() {
  if (mermaidRenderTimeout) {
    clearTimeout(mermaidRenderTimeout)
    mermaidRenderTimeout = null
  }
}

function getCompleteMermaidCodes(markdown) {
  if (!markdown) return new Set()

  const codes = new Set()
  const mermaidBlockRegex = /```mermaid\s*\n([\s\S]*?)```/g
  let match

  while ((match = mermaidBlockRegex.exec(markdown)) !== null) {
    const code = match[1].trim()
    if (code.length > 0) {
      codes.add(code)
    }
  }

  return codes
}

function restoreCachedMermaids(container) {
  if (!container) return

  const codeBlocks = container.querySelectorAll('code.language-mermaid')
  for (const code of codeBlocks) {
    const content = code.textContent.trim()
    if (!renderedMermaidCache.has(content)) continue

    const cached = renderedMermaidCache.get(content)
    const div = document.createElement('div')

    if (cached.isError) {
      div.className = 'mermaid-error'
      div.innerHTML = cached.html
    } else {
      div.className = 'mermaid'
      div.innerHTML = cached.html
      div.dataset.originalCode = content
      bindMermaidZoom(div, (contentHtml) => emit('openModal', contentHtml))
    }

    const pre = code.parentElement
    if (pre && pre.tagName === 'PRE') {
      pre.replaceWith(div)
    }
  }
}

function updateHtml(markdown) {
  if (!htmlRef.value) return
  htmlRef.value.innerHTML = renderMarkdownSafe(markdown || '')
  restoreCachedMermaids(htmlRef.value)
}

watch(() => store.roadmapContent, async (newVal, oldVal) => {
  if (!newVal) {
    clearMermaidRenderTimeout()
    if (htmlRef.value) {
      htmlRef.value.innerHTML = ''
    }
    renderedMermaidCache.clear()
    lastRenderTime = 0
    return
  }

  if (!oldVal) {
    renderedMermaidCache.clear()
    lastRenderTime = 0
  }

  await nextTick()
  updateHtml(newVal)

  if (!newVal.includes('```mermaid')) return

  const now = Date.now()
  const timeSinceLastRender = now - lastRenderTime

  if (timeSinceLastRender >= RENDER_THROTTLE_MS) {
    lastRenderTime = now
    await renderAllCompleteMermaidBlocks(false)
  } else if (!mermaidRenderTimeout) {
    const remainingTime = RENDER_THROTTLE_MS - timeSinceLastRender
    mermaidRenderTimeout = setTimeout(async () => {
      mermaidRenderTimeout = null
      lastRenderTime = Date.now()
      await renderAllCompleteMermaidBlocks(false)
    }, remainingTime)
  }
})

watch(() => store.isRoadmapStreaming, async (streaming, was) => {
  if (was && !streaming && store.roadmapContent) {
    clearMermaidRenderTimeout()
    await nextTick()
    updateHtml(store.roadmapContent)
    setTimeout(async () => {
      await renderAllCompleteMermaidBlocks(true)
    }, 150)
  }
})

watch(() => store.activeInsightTab, async (tab) => {
  if (tab === 'roadmap' && store.roadmapContent && !store.isRoadmapStreaming) {
    await nextTick()
    updateHtml(store.roadmapContent)
    setTimeout(async () => {
      await renderAllCompleteMermaidBlocks(true)
    }, 100)
  }
})

async function renderAllCompleteMermaidBlocks(isFinalRender = false) {
  if (!htmlRef.value) return

  if (isRendering.value) {
    clearMermaidRenderTimeout()
    mermaidRenderTimeout = setTimeout(() => renderAllCompleteMermaidBlocks(isFinalRender), 200)
    return
  }

  const markdown = store.roadmapContent
  if (!markdown) return

  const completeCodes = getCompleteMermaidCodes(markdown)
  if (completeCodes.size === 0) return

  const codeBlocks = htmlRef.value.querySelectorAll('code.language-mermaid')
  if (codeBlocks.length === 0) return

  const blocksToRender = []
  for (const codeBlock of codeBlocks) {
    const code = codeBlock.textContent.trim()
    if (completeCodes.has(code) && !renderedMermaidCache.has(code)) {
      blocksToRender.push(codeBlock)
    }
  }

  if (blocksToRender.length === 0) return

  isRendering.value = true

  try {
    for (const codeBlock of blocksToRender) {
      if (!codeBlock.parentElement) continue

      await new Promise(resolve => {
        if (window.requestIdleCallback) {
          requestIdleCallback(resolve, { timeout: 50 })
        } else {
          setTimeout(resolve, 10)
        }
      })

      if (!store.roadmapContent || !htmlRef.value) {
        break
      }

      await renderSingleCodeBlock(codeBlock, isFinalRender)
    }
  } catch (e) {
    console.error('[Mermaid/Roadmap] Render failed:', e)
  } finally {
    isRendering.value = false
  }
}

async function renderSingleCodeBlock(codeBlock, isFinalRender = false) {
  const originalCode = codeBlock.textContent.trim()
  const pre = codeBlock.parentElement

  if (!pre || pre.tagName !== 'PRE') return
  if (renderedMermaidCache.has(originalCode)) return

  const code = sanitizeMermaidCode(originalCode)
  const div = document.createElement('div')
  div.id = `mermaid-rm-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`
  div.className = 'mermaid'
  div.dataset.originalCode = originalCode
  div.textContent = code
  pre.replaceWith(div)

  try {
    await mermaid.run({ nodes: [div] })

    const svg = div.querySelector('svg')
    if (svg) {
      renderedMermaidCache.set(originalCode, { html: div.innerHTML, isError: false })
      bindMermaidZoom(div, (contentHtml) => emit('openModal', contentHtml))
    }
  } catch (e) {
    if (isFinalRender) {
      const errorHtml = createMermaidErrorHtml(originalCode)
      renderedMermaidCache.set(originalCode, { html: errorHtml, isError: true })
      div.className = 'mermaid-error'
      div.innerHTML = errorHtml
    } else {
      const newPre = document.createElement('pre')
      const newCode = document.createElement('code')
      newCode.className = 'language-mermaid'
      newCode.textContent = originalCode
      newPre.appendChild(newCode)
      div.replaceWith(newPre)
    }
  }
}
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

.markdown-body :deep(.mermaid) {
  display: flex;
  justify-content: center;
  margin: 20px 0;
  padding: 10px;
  border-radius: 8px;
  overflow-x: auto;
  cursor: zoom-in;
  transition: all 0.2s;
}

.markdown-body :deep(.mermaid:hover) {
  background: #f8fafc;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.markdown-body :deep(.mermaid-error) {
  margin: 20px 0;
  padding: 16px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
}

.markdown-body :deep(.mermaid-error-header) {
  color: #dc2626;
  font-weight: 600;
  margin-bottom: 12px;
}

.markdown-body :deep(.mermaid-error details) {
  margin: 8px 0;
}

.markdown-body :deep(.mermaid-error summary) {
  cursor: pointer;
  color: #4b5563;
  font-size: 14px;
}

.markdown-body :deep(.mermaid-source) {
  background: #1f2937;
  color: #e5e7eb;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin-top: 8px;
  font-size: 13px;
}

.markdown-body :deep(.mermaid-error-tip) {
  color: #6b7280;
  font-size: 13px;
  margin-top: 8px;
  font-style: italic;
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
  color: #a8a29e;
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
  content: "Generate Roadmap";
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
