<template>
  <div class="report-container">
    <!-- 报告内容 -->
    <div class="markdown-body" ref="reportRef">
      <div v-if="!store.currentReport" class="placeholder">
        <div class="placeholder-icon" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 3.5h5.5L15 7v9.5H6z" />
            <path d="M11.5 3.5V7H15" />
            <path d="M8 10h5" />
            <path d="M8 13h5" />
          </svg>
        </div>
        <div class="placeholder-title">Project Analysis Report</div>
        <div class="placeholder-text">The project architecture report will be generated here.</div>
        📊 The project architecture report will be generated here.
      </div>
      <!-- 报告内容容器，由 JS 手动管理 innerHTML -->
      <div v-show="store.currentReport" ref="reportContentRef"></div>
    </div>
    
    <div v-if="store.isStreaming" class="streaming-indicator">
      <span class="dot-pulse"></span> Generating project analysis report...
    </div>
    <!-- 悬浮工具栏 -->
    <div v-if="store.currentReport" class="floating-toolbar">
      <button class="toolbar-btn" @click="downloadMarkdown" title="Download as Markdown">
        📄
      </button>
      <button class="toolbar-btn" @click="printReport" title="Print / Save as PDF">
        🖨️
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { marked } from 'marked'
import mermaid from 'mermaid'
import { useAppStore } from '../stores/app'
import {
  initializeMermaid,
  sanitizeMermaidCode,
  createMermaidErrorHtml,
  bindMermaidZoom
} from '../composables/useMermaidShared'

const store = useAppStore()
const reportRef = ref(null)
const reportContentRef = ref(null)

// === Mermaid 渲染状态管理 ===
let mermaidRenderTimeout = null
let isRendering = ref(false)
let lastRenderTime = 0
const RENDER_THROTTLE_MS = 400  // 节流间隔（最少间隔多久渲染一次）

// 存储已渲染的代码块 - key 是代码内容，value 是 { html: string, isError: boolean }
const renderedMermaidCache = new Map()

// 初始化 Mermaid，并在挂载时恢复已有报告内容（如从 PaperAlign 返回后重新挂载）
onMounted(() => {
  initializeMermaid()
  if (store.currentReport) {
    nextTick(() => {
      updateReportContent(store.currentReport)
      if (store.currentReport.includes('```mermaid')) {
        setTimeout(() => renderAllCompleteMermaidBlocks(true), 150)
      }
    })
  }
})

// 清理定时器
onUnmounted(() => {
  if (mermaidRenderTimeout) {
    clearTimeout(mermaidRenderTimeout)
    mermaidRenderTimeout = null
  }
})

/**
 * 获取 markdown 中所有完整的 mermaid 代码块内容集合
 */
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

/**
 * 同步恢复已缓存的 mermaid 图表（防止闪烁）
 * 在更新 innerHTML 后立即调用
 * 支持恢复成功渲染和失败渲染两种状态
 */
function restoreCachedMermaids(container) {
  if (!container) return
  
  const codeBlocks = container.querySelectorAll('code.language-mermaid')
  for (const code of codeBlocks) {
    const content = code.textContent.trim()
    if (renderedMermaidCache.has(content)) {
      const cached = renderedMermaidCache.get(content)
      const div = document.createElement('div')
      
      if (cached.isError) {
        // 恢复错误状态
        div.className = 'mermaid-error'
        div.innerHTML = cached.html
      } else {
        // 恢复成功渲染的图表
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
}

/**
 * 更新报告内容（手动管理 DOM，避免 v-html 导致的闪烁）
 */
function updateReportContent(markdown) {
  if (!reportContentRef.value) return
  
  // 1. 更新 HTML
  reportContentRef.value.innerHTML = marked.parse(markdown)
  
  // 2. 立即同步恢复已缓存的 mermaid（防止闪烁）
  restoreCachedMermaids(reportContentRef.value)
}

/**
 * 监听报告变化 - 手动管理 DOM 更新
 */
watch(() => store.currentReport, async (newVal, oldVal) => {
  // 如果报告被清空，清除缓存和定时器
  if (!newVal) {
    if (mermaidRenderTimeout) {
      clearTimeout(mermaidRenderTimeout)
      mermaidRenderTimeout = null
    }
    if (reportContentRef.value) {
      reportContentRef.value.innerHTML = ''
    }
    renderedMermaidCache.clear()
    lastRenderTime = 0
    console.log('[Mermaid] Report cleared, cache cleared')
    return
  }
  
  // 如果是新报告（旧值为空或不存在），清除缓存
  if (!oldVal) {
    renderedMermaidCache.clear()
    lastRenderTime = 0
    console.log('[Mermaid] New report started, cache cleared')
  }
  
  await nextTick()
  
  // 更新报告内容（会同步恢复已缓存的 mermaid）
  updateReportContent(newVal)
  
  // 检查是否包含 mermaid 代码块
  if (!newVal.includes('```mermaid')) return
  
  // 节流逻辑：渲染新的 mermaid 图表
  const now = Date.now()
  const timeSinceLastRender = now - lastRenderTime
  
  if (timeSinceLastRender >= RENDER_THROTTLE_MS) {
    // 可以立即渲染新图表（流式期间，不缓存错误）
    lastRenderTime = now
    await renderAllCompleteMermaidBlocks(false)
  } else {
    // 设置定时器在剩余时间后渲染新图表
    if (!mermaidRenderTimeout) {
      const remainingTime = RENDER_THROTTLE_MS - timeSinceLastRender
      mermaidRenderTimeout = setTimeout(async () => {
        mermaidRenderTimeout = null
        lastRenderTime = Date.now()
        await renderAllCompleteMermaidBlocks(false)
      }, remainingTime)
    }
  }
})

/**
 * 监听流式输出结束，确保最终渲染
 */
watch(() => store.isStreaming, async (isStreaming, wasStreaming) => {
  if (wasStreaming && !isStreaming) {
    console.log('[Mermaid] Streaming finished, final render...')
    
    // 清除定时器
    if (mermaidRenderTimeout) {
      clearTimeout(mermaidRenderTimeout)
      mermaidRenderTimeout = null
    }
    
    // 等待 DOM 完全更新后进行最终渲染
    await nextTick()
    setTimeout(async () => {
      await renderAllCompleteMermaidBlocks(true)  // 最终渲染，缓存错误
    }, 150)
  }
})

/**
 * 渲染所有完整的 Mermaid 代码块
 * @param {boolean} isFinalRender - 是否为最终渲染（流式结束后）
 * 核心逻辑：
 * 1. 从 markdown 源码中提取所有完整的代码块
 * 2. 查找 DOM 中所有 code.language-mermaid 元素
 * 3. 只渲染内容在完整列表中且未被缓存的代码块
 */
async function renderAllCompleteMermaidBlocks(isFinalRender = false) {
  if (!reportContentRef.value) return
  if (isRendering.value) {
    console.log('[Mermaid] Already rendering, scheduling retry...')
    mermaidRenderTimeout = setTimeout(() => renderAllCompleteMermaidBlocks(), 200)
    return
  }
  
  const markdown = store.currentReport
  if (!markdown) return
  
  // 获取 markdown 中所有完整的代码块
  const completeCodes = getCompleteMermaidCodes(markdown)
  
  if (completeCodes.size === 0) return
  
  // 查找 DOM 中所有未渲染的 code.language-mermaid 元素
  const codeBlocks = reportContentRef.value.querySelectorAll('code.language-mermaid')
  
  if (codeBlocks.length === 0) return
  
  // 找出需要渲染的代码块（内容在完整列表中且未被缓存的）
  const blocksToRender = []
  for (const codeBlock of codeBlocks) {
    const code = codeBlock.textContent.trim()
    // 只渲染完整且未缓存的代码块
    if (completeCodes.has(code) && !renderedMermaidCache.has(code)) {
      blocksToRender.push(codeBlock)
    }
  }
  
  if (blocksToRender.length === 0) return
  
  console.log(`[Mermaid] Rendering ${blocksToRender.length} complete block(s)...`)
  
  isRendering.value = true
  
  try {
    for (let i = 0; i < blocksToRender.length; i++) {
      const codeBlock = blocksToRender[i]
      
      // 再次检查元素是否还在 DOM 中（可能被后续更新移除）
      if (!codeBlock.parentElement) continue
      
      // 让出主线程，避免卡顿
      await new Promise(resolve => {
        if (window.requestIdleCallback) {
          requestIdleCallback(resolve, { timeout: 50 })
        } else {
          setTimeout(resolve, 10)
        }
      })
      
      // 检查是否报告已被清空
      if (!store.currentReport || !reportContentRef.value) {
        console.log('[Mermaid] Report cleared, stopping render')
        break
      }
      
      await renderSingleCodeBlock(codeBlock, isFinalRender)
    }
    
    console.log('[Mermaid] Render complete')
  } catch (e) {
    console.error('[Mermaid] Render error:', e)
  } finally {
    isRendering.value = false
  }
}

/**
 * 渲染单个代码块
 * @param {Element} codeBlock - 代码块元素
 * @param {boolean} isFinalRender - 是否为最终渲染（流式结束后），只有最终渲染才缓存错误
 */
async function renderSingleCodeBlock(codeBlock, isFinalRender = false) {
  const originalCode = codeBlock.textContent.trim()
  const pre = codeBlock.parentElement
  
  if (!pre || pre.tagName !== 'PRE') return
  
  // 检查缓存 - 如果这段代码已经处理过（成功或失败），跳过
  if (renderedMermaidCache.has(originalCode)) {
    return
  }
  
  const code = sanitizeMermaidCode(originalCode)
  
  const div = document.createElement('div')
  div.id = `mermaid-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`
  div.className = 'mermaid'
  div.dataset.originalCode = originalCode
  div.textContent = code
  
  pre.replaceWith(div)
  
  try {
    await mermaid.run({ nodes: [div] })
    
    const svg = div.querySelector('svg')
    if (svg) {
      // 缓存成功渲染结果
      renderedMermaidCache.set(originalCode, { html: div.innerHTML, isError: false })
      bindMermaidZoom(div, (contentHtml) => emit('openModal', contentHtml))
    }
    // 注意：如果 mermaid.run 成功但没有 SVG，不做任何处理
    // 让下一次渲染周期再尝试（因为没有加入缓存）
  } catch (e) {
    console.error('[Mermaid] Render failed:', e)
    
    if (isFinalRender) {
      // 最终渲染失败，缓存错误状态
      const errorHtml = createMermaidErrorHtml(originalCode)
      renderedMermaidCache.set(originalCode, { html: errorHtml, isError: true })
      div.className = 'mermaid-error'
      div.innerHTML = errorHtml
    } else {
      // 流式期间失败，不缓存，恢复为代码块，让后续重试
      const newPre = document.createElement('pre')
      const newCode = document.createElement('code')
      newCode.className = 'language-mermaid'
      newCode.textContent = originalCode
      newPre.appendChild(newCode)
      div.replaceWith(newPre)
    }
  }
}

const emit = defineEmits(['openModal'])

// 下载 Markdown
function downloadMarkdown() {
  if (!store.currentReport) return
  
  const blob = new Blob([store.currentReport], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  
  const repoName = store.currentRepoUrl.split('/').pop() || 'report'
  a.download = `${repoName}_analysis.md`
  
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// 打印报告
function printReport() {
  if (!store.currentReport) return
  
  const repoName = store.currentRepoUrl.split('/').pop() || 'report'
  const processedHtml = marked.parse(store.currentReport).replace(
    /<pre class="mermaid">[\s\S]*?<\/pre>/g,
    '<div class="mermaid-placeholder">📊 Mermaid diagram (view in browser)</div>'
  )
  
  const printWindow = window.open('', '_blank')
  const htmlContent = `
<!DOCTYPE html>
<html>
<head>
<title>${repoName} - Analysis Report</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.6; max-width: 900px; margin: 0 auto; padding: 40px; color: #1e293b; }
h1, h2, h3 { color: #0f172a; margin-top: 1.5em; }
h1 { border-bottom: 2px solid #e2e8f0; padding-bottom: 0.3em; }
h2 { border-bottom: 1px solid #e2e8f0; padding-bottom: 0.2em; }
code { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
pre { background: #f8fafc; padding: 16px; border-radius: 8px; overflow-x: auto; border: 1px solid #e2e8f0; }
pre code { background: none; padding: 0; }
table { width: 100%; border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #e2e8f0; padding: 10px 12px; text-align: left; }
th { background: #f8fafc; font-weight: 600; }
.mermaid-placeholder { background: #fef3c7; border: 1px dashed #f59e0b; padding: 20px; text-align: center; color: #92400e; border-radius: 8px; margin: 1em 0; }
@media print { body { padding: 20px; } pre { white-space: pre-wrap; word-wrap: break-word; } }
</style>
</head>
<body>
${processedHtml}
<script>window.print();<\/script>
</body>
</html>`
  
  printWindow.document.write(htmlContent)
  printWindow.document.close()
}
</script>

<style scoped>
.report-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  background: transparent;
  border-radius: 0;
  border: 0;
}

.floating-toolbar {
  position: absolute;
  top: 10px;
  right: 16px;
  display: flex;
  gap: 6px;
  z-index: 10;
  opacity: 1;
  transition: opacity 0.2s;
}

.toolbar-btn {
  min-width: 42px;
  height: 36px;
  padding: 0 12px;
  font-size: 13px;
  background: rgba(255, 255, 255, 0.96);
  color: #44403c;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}

.toolbar-btn:hover {
  background: #f5f5f4;
  border-color: #d6d3d1;
}

.markdown-body {
  flex: 1;
  padding: 18px 24px 28px;
  overflow-y: auto;
  font-size: 15px;
  line-height: 1.7;
  color: var(--text-primary);
  background: #faf9f6;
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
  font-size: 0;
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
  line-height: 1.4;
  color: #57534e;
}

.placeholder-text {
  font-size: 12px;
  line-height: 1.4;
  color: #a8a29e;
}

/* Mermaid 样式 */
.markdown-body :deep(.mermaid) {
  display: flex;
  justify-content: center;
  margin: 20px 0;
  background: var(--bg-color);
  padding: 10px;
  border-radius: 8px;
  cursor: zoom-in;
  transition: transform 0.2s;
  overflow-x: auto;
}

.markdown-body :deep(.mermaid:hover) {
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

/* Mermaid 错误样式 */
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

/* Mermaid 加载中样式 */
.markdown-body :deep(.mermaid-pending) {
  display: flex;
  justify-content: center;
  align-items: center;
  margin: 20px 0;
  background: linear-gradient(135deg, #f0f9ff, #e0f2fe);
  padding: 40px;
  border-radius: 8px;
  border: 1px dashed #7dd3fc;
}

.markdown-body :deep(.mermaid-loading) {
  color: #0369a1;
  font-size: 14px;
  animation: mermaidPulse 1.5s ease-in-out infinite;
}

@keyframes mermaidPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
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
