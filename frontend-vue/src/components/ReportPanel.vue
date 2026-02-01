<template>
  <div class="report-container">
    <!-- 报告内容 -->
    <div class="markdown-body" ref="reportRef">
      <div v-if="!store.currentReport" class="placeholder">
        📊 The project architecture report will be generated here.
      </div>
      <div v-else v-html="renderedReport"></div>
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
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { marked } from 'marked'
import mermaid from 'mermaid'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const reportRef = ref(null)

// 初始化 Mermaid
onMounted(() => {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'neutral',
    securityLevel: 'loose',
    // 改进的配置以支持中文
    flowchart: {
      htmlLabels: true,
      useMaxWidth: true
    },
    sequence: {
      useMaxWidth: true
    }
  })
})

/**
 * 预处理 Mermaid 代码，自动修复中文渲染问题
 * - 为未加引号的中文节点添加引号
 * - 处理特殊字符
 */
function sanitizeMermaidCode(code) {
  let lines = code.split('\n')
  
  return lines.map(line => {
    // 跳过注释和空行
    if (line.trim().startsWith('%%') || line.trim() === '') {
      return line
    }
    
    // 处理 graph/flowchart 节点定义: A[文本] -> A["文本"]
    // 匹配 节点ID[文本] 或 节点ID(文本) 或 节点ID{文本} 等形式
    line = line.replace(/(\w+)\[([^\]"]+)\]/g, (match, id, text) => {
      // 如果文本包含中文或特殊字符且未被引号包裹
      if (/[\u4e00-\u9fa5]/.test(text) || /[()（）：:,，]/.test(text)) {
        return `${id}["${text}"]`
      }
      return match
    })
    
    // 处理圆角节点 A(文本)
    line = line.replace(/(\w+)\(([^)"]+)\)/g, (match, id, text) => {
      if (/[\u4e00-\u9fa5]/.test(text) || /[[\]{}：:,，]/.test(text)) {
        return `${id}("${text}")`
      }
      return match
    })
    
    // 处理菱形节点 A{文本}
    line = line.replace(/(\w+)\{([^}"]+)\}/g, (match, id, text) => {
      if (/[\u4e00-\u9fa5]/.test(text) || /[[\]()：:,，]/.test(text)) {
        return `${id}{"${text}"}`
      }
      return match
    })
    
    // 处理连线标签 -->|文本| 或 --|文本|-->
    line = line.replace(/(\|)([^|"]+)(\|)/g, (match, p1, text, p2) => {
      if (/[\u4e00-\u9fa5]/.test(text)) {
        return `|"${text}"|`
      }
      return match
    })
    
    // 处理 sequenceDiagram 中的消息文本
    // User->>API: 发起请求 -> User->>API: "发起请求"
    line = line.replace(/(->|-->>?|<<--)([^:]+):\s*([^"'\n]+)$/g, (match, arrow, target, msg) => {
      if (/[\u4e00-\u9fa5]/.test(msg) && !msg.startsWith('"')) {
        return `${arrow}${target}: "${msg.trim()}"`
      }
      return match
    })
    
    return line
  }).join('\n')
}

// 渲染 Markdown
const renderedReport = computed(() => {
  return marked.parse(store.currentReport)
})

// 监听报告变化，渲染 Mermaid
watch(() => store.currentReport, async (newVal) => {
  if (newVal) {
    await nextTick()
    renderMermaid()
  }
})

async function renderMermaid() {
  if (!reportRef.value) return
  
  const blocks = reportRef.value.querySelectorAll('code.language-mermaid')
  if (blocks.length === 0) return
  
  const divsToRender = []
  
  // 存储原始代码用于降级显示
  const originalCodes = []
  
  blocks.forEach((block, i) => {
    let code = block.textContent
    originalCodes.push(code) // 保存原始代码
    // 预处理 Mermaid 代码，修复中文问题
    code = sanitizeMermaidCode(code)
    
    const pre = block.parentElement
    
    const div = document.createElement('div')
    div.id = `mermaid-${Date.now()}-${i}`
    div.className = 'mermaid'
    div.textContent = code
    div.dataset.originalCode = originalCodes[i] // 存储原始代码到元素上
    
    pre.replaceWith(div)
    divsToRender.push(div)
  })
  
  // 逐个渲染，单个失败不影响其他图表
  for (let i = 0; i < divsToRender.length; i++) {
    const div = divsToRender[i]
    try {
      await mermaid.run({ nodes: [div] })
      
      const svg = div.querySelector('svg')
      if (svg) {
        div.style.cursor = 'zoom-in'
        div.style.overflowX = 'auto'
        svg.style.maxWidth = '100%'
        
        div.onclick = () => {
          emit('openModal', div.innerHTML)
        }
      }
    } catch (e) {
      console.error(`Mermaid rendering failed for diagram ${i}:`, e)
      // 渲染失败时显示降级内容
      const errorDiv = document.createElement('div')
      errorDiv.className = 'mermaid-error'
      errorDiv.innerHTML = `
        <div class="mermaid-error-header">⚠️ 图表渲染失败</div>
        <details>
          <summary>查看原始 Mermaid 代码</summary>
          <pre class="mermaid-source"><code>${escapeHtml(div.dataset.originalCode || div.textContent)}</code></pre>
        </details>
        <div class="mermaid-error-tip">提示: 请检查代码语法，中文文本需用双引号包裹</div>
      `
      div.replaceWith(errorDiv)
    }
  }
}

// HTML 转义函数
function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
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
  background: var(--bg-color);
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

.floating-toolbar {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 6px;
  z-index: 10;
  opacity: 0.7;
  transition: opacity 0.2s;
}

.report-container:hover .floating-toolbar {
  opacity: 1;
}

.toolbar-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  font-size: 16px;
  background: rgba(255, 255, 255, 0.9);
  color: #334155;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.toolbar-btn:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
  transform: scale(1.05);
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
  margin-top: 80px;
  font-size: 18px;
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
</style>
