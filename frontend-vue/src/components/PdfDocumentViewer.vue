<template>
  <div class="pdf-viewer">
    <div v-if="loadingDocument" class="pdf-state">Loading PDF...</div>
    <div v-else-if="error" class="pdf-state error">{{ error }}</div>
    <div v-else-if="!pages.length" class="pdf-state">Open a local PDF to start selecting passages.</div>
    <div v-else class="pdf-pages">
      <div v-if="renderingPages" class="pdf-rendering-state">Rendering PDF pages...</div>
      <section v-for="page in pages" :key="page.pageNumber" class="pdf-page-card">
        <div class="pdf-page-label">Page {{ page.pageNumber }}</div>
        <div class="pdf-stage" :style="{ width: `${page.width}px`, height: `${page.height}px` }">
          <canvas :ref="setCanvasRef(page.pageNumber)"></canvas>
          <div
            :ref="setTextLayerRef(page.pageNumber)"
            class="textLayer"
            :style="{ width: `${page.width}px`, height: `${page.height}px` }"
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
  }
})

const emit = defineEmits(['loaded'])
const store = useAppStore()

const pages = ref([])
const loadingDocument = ref(false)
const renderingPages = ref(false)
const error = ref('')
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
  canvasRefs.clear()
  textLayerRefs.clear()
  store.setPaperPdfPages([])

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
      const viewport = page.getViewport({ scale: 1.35 })

      nextPages.push({
        pageNumber,
        page: markRaw(page),
        viewport,
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

    for (const page of nextPages) {
      if (runId !== loadRunId) return
      await renderPage(page, runId)
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

function handleMouseUp(pageNumber) {
  const selection = window.getSelection()
  const text = selection?.toString().trim()
  if (!text) return

  store.addPaperSelection(text, { pageNumber })
  selection.removeAllRanges()
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

  if (runId !== loadRunId) return

  const textContent = await pageInfo.page.getTextContent()
  if (runId !== loadRunId) return

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

.pdf-stage :deep(.textLayer) {
  position: absolute;
  inset: 0;
  z-index: 1;
  user-select: text;
}

.pdf-stage :deep(.textLayer ::selection) {
  background: rgba(34, 197, 94, 0.26);
}
</style>
