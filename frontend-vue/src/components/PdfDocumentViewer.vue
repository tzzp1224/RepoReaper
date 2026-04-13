<template>
  <div class="pdf-viewer">
    <div v-if="loadingDocument" class="pdf-state">Loading PDF...</div>
    <div v-else-if="error" class="pdf-state error">{{ error }}</div>
    <div v-else-if="!pages.length" class="pdf-state">Open a local PDF to start selecting passages.</div>
    <div v-else class="pdf-pages">
      <div v-if="renderingPages" class="pdf-rendering-state">Rendering PDF pages...</div>
      <section v-for="page in pages" :key="page.pageNumber" class="pdf-page-card">
        <div class="pdf-page-label">Page {{ page.pageNumber }}</div>
        <div
          :ref="setStageRef(page.pageNumber)"
          class="pdf-stage"
          :style="{
            width: `${page.width}px`,
            height: `${page.height}px`,
            '--scale-factor': page.scale
          }"
        >
          <canvas :ref="setCanvasRef(page.pageNumber)"></canvas>
          <div class="selection-highlight-layer" aria-hidden="true">
            <template v-for="selection in getPageSelections(page.pageNumber)" :key="selection.id">
              <span
                v-for="(rect, rectIndex) in selection.meta?.rects || []"
                :key="`${selection.id}-${rectIndex}`"
                class="selection-highlight"
                :style="highlightRectStyle(rect)"
              ></span>
            </template>
          </div>
          <div
            :ref="setTextLayerRef(page.pageNumber)"
            class="textLayer"
            :class="{ 'selection-enabled': selectionEnabled }"
            :style="{
              width: `${page.width}px`,
              height: `${page.height}px`,
              '--scale-factor': page.scale
            }"
            @mouseup="handleMouseUp(page.pageNumber)"
          ></div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { markRaw, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist/build/pdf.mjs'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url'
import 'pdfjs-dist/web/pdf_viewer.css'
import { useAppStore } from '../stores/app'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

const props = defineProps({
  file: {
    type: Object,
    default: null
  },
  selectionEnabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['loaded'])
const store = useAppStore()

const pages = ref([])
const loadingDocument = ref(false)
const renderingPages = ref(false)
const error = ref('')
const stageRefs = new Map()
const canvasRefs = new Map()
const textLayerRefs = new Map()
const renderTasks = new Map()
const textLayers = new Map()

let loadRunId = 0
let activeLoadingTask = null
let activePdfDocument = null

watch(() => props.file, async file => {
  const runId = ++loadRunId
  stopActiveWork()
  pages.value = []
  error.value = ''
  loadingDocument.value = false
  renderingPages.value = false
  stageRefs.clear()
  canvasRefs.clear()
  textLayerRefs.clear()
  store.setPaperPdfPages([])
  store.paperAlignText = ''

  if (!file) return

  loadingDocument.value = true

  try {
    const data = new Uint8Array(await file.arrayBuffer())
    const loadingTask = pdfjsLib.getDocument({ data })
    activeLoadingTask = loadingTask
    const pdf = await loadingTask.promise
    activeLoadingTask = null

    if (runId !== loadRunId) {
      await destroyPdf(pdf)
      return
    }

    activePdfDocument = pdf
    const nextPages = []

    for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
      if (runId !== loadRunId) return

      const page = await pdf.getPage(pageNumber)
      const scale = 1.35
      const viewport = page.getViewport({ scale })

      nextPages.push({
        pageNumber,
        page: markRaw(page),
        viewport,
        scale,
        width: viewport.width,
        height: viewport.height
      })
    }

    if (runId !== loadRunId) return

    pages.value = nextPages
    store.setPaperPdfPages(nextPages.map(page => ({
      pageNumber: page.pageNumber,
      width: page.width,
      height: page.height
    })))
    loadingDocument.value = false
    renderingPages.value = true

    await nextTick()

    const pageTexts = []
    for (const page of nextPages) {
      if (runId !== loadRunId) return
      pageTexts[page.pageNumber - 1] = await renderPage(page, runId)
    }

    if (runId === loadRunId) {
      store.paperAlignText = pageTexts.filter(Boolean).join('\n\n')
    }

    emit('loaded', nextPages)
  } catch (loadError) {
    if (runId === loadRunId && !isCancellationError(loadError)) {
      error.value = loadError?.message || 'Failed to load PDF.'
    }
  } finally {
    if (runId === loadRunId) {
      loadingDocument.value = false
      renderingPages.value = false
    }
  }
}, { immediate: true })

onBeforeUnmount(() => {
  loadRunId += 1
  stopActiveWork()
})

function setStageRef(pageNumber) {
  return element => {
    if (!element) {
      stageRefs.delete(pageNumber)
      return
    }
    stageRefs.set(pageNumber, element)
  }
}

function setCanvasRef(pageNumber) {
  return element => {
    if (!element) {
      canvasRefs.delete(pageNumber)
      return
    }
    canvasRefs.set(pageNumber, element)
  }
}

function setTextLayerRef(pageNumber) {
  return element => {
    if (!element) {
      textLayerRefs.delete(pageNumber)
      return
    }
    textLayerRefs.set(pageNumber, element)
  }
}

function getPageSelections(pageNumber) {
  return store.paperSelections.filter(selection => selection.meta?.pageNumber === pageNumber)
}

function highlightRectStyle(rect) {
  return {
    left: `${rect.left}px`,
    top: `${rect.top}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`
  }
}

function handleMouseUp(pageNumber) {
  const selection = window.getSelection()
  if (!props.selectionEnabled) {
    selection?.removeAllRanges()
    return
  }
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return

  const text = selection.toString().trim()
  if (!text) return

  const textLayer = textLayerRefs.get(pageNumber)
  const stage = stageRefs.get(pageNumber)
  const range = selection.getRangeAt(0)

  if (!textLayer || !stage) {
    selection.removeAllRanges()
    return
  }
  if (!textLayer.contains(range.startContainer) || !textLayer.contains(range.endContainer)) {
    selection.removeAllRanges()
    return
  }

  const rects = getSelectionRects(range, stage)
  store.addPaperSelection(text, { pageNumber, rects })
  selection.removeAllRanges()
}

function getSelectionRects(range, stage) {
  const stageRect = stage.getBoundingClientRect()
  const stageWidth = stageRect.width
  const stageHeight = stageRect.height

  return Array.from(range.getClientRects())
    .map(rect => {
      const left = Math.max(rect.left - stageRect.left, 0)
      const top = Math.max(rect.top - stageRect.top, 0)
      const right = Math.min(rect.right - stageRect.left, stageWidth)
      const bottom = Math.min(rect.bottom - stageRect.top, stageHeight)

      return {
        left,
        top,
        width: Math.max(right - left, 0),
        height: Math.max(bottom - top, 0)
      }
    })
    .filter(rect => rect.width > 1 && rect.height > 1)
}

async function renderPage(pageInfo, runId) {
  const canvas = canvasRefs.get(pageInfo.pageNumber)
  const textLayerContainer = textLayerRefs.get(pageInfo.pageNumber)

  if (!canvas || !textLayerContainer) {
    throw new Error('PDF page container was not mounted.')
  }

  const context = canvas.getContext('2d')
  if (!context) {
    throw new Error('Failed to initialize PDF canvas.')
  }

  const outputScale = window.devicePixelRatio || 1

  canvas.width = Math.floor(pageInfo.width * outputScale)
  canvas.height = Math.floor(pageInfo.height * outputScale)
  canvas.style.width = `${pageInfo.width}px`
  canvas.style.height = `${pageInfo.height}px`
  context.clearRect(0, 0, canvas.width, canvas.height)
  textLayerContainer.style.setProperty('--scale-factor', pageInfo.scale)
  textLayerContainer.replaceChildren()

  const renderTask = pageInfo.page.render({
    canvasContext: context,
    viewport: pageInfo.viewport,
    transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0]
  })

  renderTasks.set(pageInfo.pageNumber, renderTask)
  try {
    await renderTask.promise
  } finally {
    renderTasks.delete(pageInfo.pageNumber)
  }

  if (runId !== loadRunId) return ''

  const textContent = await pageInfo.page.getTextContent()
  if (runId !== loadRunId) return ''

  const pageText = extractPageText(textContent)
  const textLayer = new pdfjsLib.TextLayer({
    textContentSource: textContent,
    container: textLayerContainer,
    viewport: pageInfo.viewport
  })

  textLayers.set(pageInfo.pageNumber, textLayer)
  try {
    await textLayer.render()
  } finally {
    textLayers.delete(pageInfo.pageNumber)
  }

  return pageText
}

function extractPageText(textContent) {
  return textContent.items
    .map(item => `${item.str}${item.hasEOL ? '\n' : ' '}`)
    .join('')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function stopActiveWork() {
  for (const renderTask of renderTasks.values()) {
    try {
      renderTask.cancel()
    } catch {
      // Ignore cancellation races while replacing PDFs.
    }
  }
  renderTasks.clear()

  for (const textLayer of textLayers.values()) {
    try {
      textLayer.cancel()
    } catch {
      // Ignore cancellation races while replacing PDFs.
    }
  }
  textLayers.clear()

  if (activeLoadingTask) {
    const loadingTask = activeLoadingTask
    activeLoadingTask = null
    loadingTask.destroy().catch(() => {})
  }

  if (activePdfDocument) {
    const pdfDocument = activePdfDocument
    activePdfDocument = null
    destroyPdf(pdfDocument)
  }
}

async function destroyPdf(pdfDocument) {
  try {
    await pdfDocument.destroy()
  } catch {
    // The document may already be shutting down after a cancelled load.
  }
}

function isCancellationError(loadError) {
  const name = loadError?.name || ''
  const message = loadError?.message || ''
  return name === 'RenderingCancelledException' || /cancel|destroy/i.test(message)
}
</script>

<style scoped>
.pdf-viewer {
  width: 100%;
  min-height: 220px;
  height: 100%;
}

.pdf-state {
  padding: 18px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: #fff;
  color: #57534e;
  font-size: 13px;
}

.pdf-state.error {
  color: #b91c1c;
}

.pdf-pages {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  min-width: max-content;
}

.pdf-rendering-state {
  align-self: stretch;
  border: 1px solid rgba(59, 130, 246, 0.22);
  border-radius: 8px;
  background: rgba(239, 246, 255, 0.96);
  color: #2563eb;
  font-size: 12px;
  padding: 8px 12px;
}

.pdf-page-card {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: #fff;
  padding: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.pdf-page-label {
  margin-bottom: 10px;
  font-size: 12px;
  color: #78716c;
}

.pdf-stage {
  position: relative;
  background: #fff;
}

.pdf-stage canvas {
  display: block;
  position: relative;
  z-index: 0;
}

.selection-highlight-layer {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  overflow: hidden;
}

.selection-highlight {
  position: absolute;
  border-radius: 2px;
  background: rgba(34, 197, 94, 0.24);
  box-shadow: inset 0 0 0 1px rgba(22, 163, 74, 0.18);
}

.pdf-stage :deep(.textLayer) {
  position: absolute;
  inset: 0;
  z-index: 2;
  pointer-events: none;
  user-select: none;
}

.pdf-stage :deep(.textLayer.selection-enabled) {
  pointer-events: auto;
  user-select: text;
  cursor: text;
}

.pdf-stage :deep(.textLayer ::selection) {
  background: rgba(34, 197, 94, 0.26);
}
</style>