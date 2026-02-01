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
    securityLevel: 'loose'
  })
})

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
  
  blocks.forEach((block, i) => {
    const code = block.textContent
    const pre = block.parentElement
    
    const div = document.createElement('div')
    div.id = `mermaid-${Date.now()}-${i}`
    div.className = 'mermaid'
    div.textContent = code
    
    pre.replaceWith(div)
    divsToRender.push(div)
  })
  
  try {
    await mermaid.run({ nodes: divsToRender })
    
    divsToRender.forEach(div => {
      const svg = div.querySelector('svg')
      if (svg) {
        div.style.cursor = 'zoom-in'
        div.style.overflowX = 'auto'
        svg.style.maxWidth = '100%'
        
        div.onclick = () => {
          emit('openModal', div.innerHTML)
        }
      }
    })
  } catch (e) {
    console.error('Mermaid rendering failed:', e)
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
</style>
