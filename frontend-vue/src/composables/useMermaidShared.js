import mermaid from 'mermaid'

let isMermaidInitialized = false

/**
 * 初始化 Mermaid（全局仅初始化一次）
 */
export function initializeMermaid(options = {}) {
  if (isMermaidInitialized) return

  mermaid.initialize({
    startOnLoad: false,
    theme: 'neutral',
    securityLevel: 'loose',
    flowchart: {
      htmlLabels: true,
      useMaxWidth: true
    },
    sequence: {
      useMaxWidth: true
    },
    ...options
  })

  isMermaidInitialized = true
}

/**
 * 预处理 Mermaid 代码，自动修复中文渲染问题
 * - 为未加引号的中文节点添加引号
 * - 处理特殊字符
 */
export function sanitizeMermaidCode(code) {
  const lines = code.split('\n')

  return lines.map(line => {
    if (line.trim().startsWith('%%') || line.trim() === '') {
      return line
    }

    line = line.replace(/(\w+)\[([^\]"]+)\]/g, (match, id, text) => {
      if (/[\u4e00-\u9fa5]/.test(text) || /[()（）：:,，]/.test(text)) {
        return `${id}["${text}"]`
      }
      return match
    })

    line = line.replace(/(\w+)\(([^)"]+)\)/g, (match, id, text) => {
      if (/[\u4e00-\u9fa5]/.test(text) || /[[\]{}：:,，]/.test(text)) {
        return `${id}("${text}")`
      }
      return match
    })

    line = line.replace(/(\w+)\{([^}"]+)\}/g, (match, id, text) => {
      if (/[\u4e00-\u9fa5]/.test(text) || /[[\]()：:,，]/.test(text)) {
        return `${id}{"${text}"}`
      }
      return match
    })

    line = line.replace(/(\|)([^|"]+)(\|)/g, (match, p1, text, p2) => {
      if (/[\u4e00-\u9fa5]/.test(text)) {
        return `|"${text}"|`
      }
      return match
    })

    line = line.replace(/(->|-->>?|<<--)([^:]+):\s*([^"'\n]+)$/g, (match, arrow, target, msg) => {
      if (/[\u4e00-\u9fa5]/.test(msg) && !msg.startsWith('"')) {
        return `${arrow}${target}: "${msg.trim()}"`
      }
      return match
    })

    return line
  }).join('\n')
}

/**
 * 为 Mermaid 图表绑定点击放大行为
 */
export function bindMermaidZoom(div, onOpenModal) {
  if (!div || typeof onOpenModal !== 'function') return

  div.style.cursor = 'zoom-in'
  div.style.overflowX = 'auto'

  const svg = div.querySelector('svg')
  if (svg) {
    svg.style.maxWidth = '100%'
  }

  div.onclick = () => {
    onOpenModal(div.innerHTML)
  }
}

/**
 * 生成 Mermaid 渲染失败 UI
 */
export function createMermaidErrorHtml(originalCode) {
  return `
    <div class="mermaid-error-header">⚠️ 图表渲染失败</div>
    <details>
      <summary>查看原始 Mermaid 代码</summary>
      <pre class="mermaid-source"><code>${escapeHtml(originalCode)}</code></pre>
    </details>
    <div class="mermaid-error-tip">提示: 请检查代码语法，中文文本需用双引号包裹</div>
  `
}

/**
 * HTML 转义
 */
export function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}
