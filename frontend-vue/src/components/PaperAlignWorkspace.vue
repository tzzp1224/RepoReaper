<template>
  <div class="paper-workspace">
    <div class="paper-toolbar">
      <div class="toolbar-left">
        <button class="back-btn" type="button" @click="$emit('back')">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12.5 5 7.5 10l5 5" />
            <path d="M8 10h8" />
          </svg>
          <span>Back</span>
        </button>
        <div class="toolbar-divider"></div>
        <div class="toolbar-copy">
          <span class="toolbar-title">Paper-to-Code Alignment</span>
          <span class="repo-dot">路</span>
          <span class="toolbar-repo">{{ repoLabel }}</span>
        </div>
      </div>

      <label class="topk-shell">
        <span>Top K</span>
        <input v-model.number="store.paperAlignTopK" type="number" min="1" max="20" />
      </label>
    </div>

    <div ref="paperContentRef" class="paper-content" :style="gridStyle">
      <section class="paper-input-panel">
        <div class="input-header">
          <div class="input-title-group">
            <svg class="section-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 3.5h5.5L15 7v9.5H6z" />
              <path d="M11.5 3.5V7H15" />
              <path d="M8 10h5" />
              <path d="M8 13h5" />
            </svg>
            <h3>Paper Input</h3>
            <span v-if="store.paperUploadedFileName" class="file-pill" :title="store.paperUploadedFileName">
              {{ store.paperUploadedFileName }}
            </span>
          </div>

          <div class="input-actions">
            <button
              v-if="inputMode === 'text'"
              type="button"
              class="secondary-btn"
              :class="{ active: store.paperHighlightMode }"
              :disabled="!store.paperAlignText.trim()"
              @click="toggleHighlightMode"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M4 15.5 5.2 12l7.9-7.9a1.8 1.8 0 0 1 2.5 2.5L7.7 14.5z" />
                <path d="M11.8 5.4 14.6 8.2" />
              </svg>
              <span>Select Passages</span>
            </button>
            <input
              ref="fileInputRef"
              type="file"
              accept=".pdf,.txt"
              hidden
              @change="handleFileUpload"
            />
            <button type="button" class="secondary-btn" :disabled="uploadingFile" @click="fileInputRef?.click()">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M7.2 11.2 12.8 5.6a3 3 0 1 1 4.2 4.2l-7.1 7.1a4.2 4.2 0 0 1-5.9-5.9l7.1-7.1" />
              </svg>
              <span>{{ uploadingFile ? 'Uploading...' : 'Upload' }}</span>
            </button>
          </div>
        </div>

        <div class="paper-editor">
          <template v-if="inputMode === 'text'">
            <textarea
              v-if="!store.paperHighlightMode"
              :value="store.paperAlignText"
              placeholder="Paste paper text here...

Recommended: Abstract + Methods + Implementation Details"
              @input="handleTextInput"
            ></textarea>

            <div v-else class="selection-mode-shell">
              <div class="selection-tip">
                Drag to select a passage. Click an existing highlight to deselect it.
              </div>
              <div ref="textViewRef" class="selection-text-view" @mouseup="handleSelectionMouseUp">
                <template v-if="store.paperAlignText">
                  <template v-for="segment in highlightSegments" :key="segment.key">
                    <mark
                      v-if="segment.highlighted"
                      class="highlight-mark"
                      title="Click to deselect"
                      @click.stop="store.removePaperHighlight(segment.id)"
                    >
                      {{ segment.text }}
                    </mark>
                    <span v-else>{{ segment.text }}</span>
                  </template>
                </template>
                <span v-else class="empty-copy">No text yet.</span>
              </div>
            </div>
          </template>

          <template v-else>
            <div class="pdf-mode-shell">
              <div class="pdf-selection-tip">
                Drag to select text passages from the PDF. Selections will be sent to alignment.
              </div>
              <div class="pdf-scroll-area">
                <PdfDocumentViewer :file="store.paperPdfFile" />
              </div>
            </div>
          </template>
        </div>

        <div v-if="inputMode === 'pdf' && store.paperSelections.length" class="selections-list">
          <div v-for="sel in store.paperSelections" :key="sel.id" class="sel-chip">
            <span class="sel-text">{{ sel.text.length > 80 ? sel.text.slice(0, 80) + '...' : sel.text }}</span>
            <button type="button" class="sel-remove" @click="store.removePaperSelection(sel.id)">×</button>
          </div>
        </div>

        <div class="input-footer">
          <div class="footer-meta">
            <span v-if="inputMode === 'text' && charCount">{{ charCount }} chars</span>
            <span v-if="inputMode === 'text' && store.paperHighlights.length" class="selected-meta">
              {{ store.paperHighlights.length }} passage{{ store.paperHighlights.length === 1 ? '' : 's' }} selected
            </span>
            <span v-if="inputMode === 'pdf' && store.paperSelections.length" class="selected-meta">
              {{ store.paperSelections.length }} selection{{ store.paperSelections.length === 1 ? '' : 's' }}
            </span>
            <span v-if="fileError" class="error-meta">{{ fileError }}</span>
          </div>

          <div class="footer-actions">
            <button v-if="store.paperAlignText.length || store.paperAlignResult || store.paperSelections.length" type="button" class="clear-btn" @click="clearPaperInput">
              Clear
            </button>
            <button class="run-btn" :disabled="runDisabled" type="button" @click="runPaperAlign">
              <span class="btn-icon" aria-hidden="true">
                <svg
                  v-if="store.paperAlignLoading"
                  class="spin"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path d="M10 3a7 7 0 1 0 7 7" />
                </svg>
                <svg
                  v-else
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path d="M7 5.5v9l7-4.5-7-4.5Z" />
                </svg>
              </span>
              <span class="run-btn-label">{{ store.paperAlignLoading ? 'Aligning...' : runLabel }}</span>
            </button>
          </div>
        </div>
      </section>

      <PanelResizer class="paper-resizer" @resize="handlePaperResize" />

      <section class="paper-results-panel">
        <div class="results-summary">
          <div class="confidence-block">
            <span class="summary-label">Alignment Confidence</span>
            <strong>{{ confidenceLabel }}</strong>
          </div>

          <div class="summary-right">
            <div class="summary-bar">
              <span :style="{ width: `${confidencePercent || 0}%` }"></span>
            </div>
            <div class="summary-stats">
              <span class="stat-pill aligned">Aligned {{ alignedCount }}</span>
              <span class="stat-pill partial">Partial {{ partialCount }}</span>
              <span class="stat-pill missing">Missing {{ missingCount }}</span>
            </div>
          </div>
        </div>

        <div class="results-scroll">
          <div v-if="store.paperAlignLoading" class="results-state">Running alignment...</div>
          <div v-else-if="store.paperAlignError" class="results-state error">{{ store.paperAlignError }}</div>
          <div v-else-if="!store.paperAlignResult" class="results-state">
            Paste paper text or select PDF passages, then run alignment. Up to the first 6000 characters will be used.
          </div>
          <template v-else>
            <div class="results-section">
              <h4>Claim Results ({{ resultItems.length }} matched)</h4>
              <article
                v-for="(item, index) in resultItems"
                :key="`claim-${index}`"
                class="result-card"
              >
                <div class="result-top">
                  <p>{{ item.claim }}</p>
                  <span class="status-pill" :class="item.status">{{ formatStatus(item.status) }}</span>
                </div>

                <button
                  v-if="hasResultDetails(item)"
                  type="button"
                  class="details-toggle"
                  @click="toggleClaimDetails(index)"
                >
                  {{ isClaimExpanded(index) ? 'Hide details' : 'Show details' }}
                </button>

                <div v-if="isClaimExpanded(index)" class="result-details">
                  <div v-if="item.matched_files?.length" class="result-block">
                    <strong>Matched Files</strong>
                    <div class="chip-row">
                      <span v-for="file in item.matched_files" :key="file" class="chip">{{ file }}</span>
                    </div>
                  </div>

                  <div v-if="item.matched_symbols?.length" class="result-block">
                    <strong>Matched Symbols</strong>
                    <div class="chip-row">
                      <span v-for="symbol in item.matched_symbols" :key="symbol" class="chip">{{ symbol }}</span>
                    </div>
                  </div>

                  <div v-if="item.evidence_excerpt" class="result-block">
                    <strong>Evidence</strong>
                    <blockquote>{{ item.evidence_excerpt }}</blockquote>
                  </div>
                </div>
              </article>
            </div>

            <div v-if="missingClaims.length" class="results-section">
              <h4>Missing Claims ({{ missingClaims.length }})</h4>
              <article
                v-for="(item, index) in missingClaims"
                :key="`missing-${index}`"
                class="result-card missing-card"
              >
                <div class="result-top">
                  <p>{{ item.claim }}</p>
                  <span class="status-pill missing">Missing</span>
                </div>
                <div v-if="item.reason" class="missing-reason">{{ item.reason }}</div>
              </article>
            </div>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import PanelResizer from './PanelResizer.vue'
import PdfDocumentViewer from './PdfDocumentViewer.vue'
import { useAppStore } from '../stores/app'
import { usePaperAlign } from '../composables/usePaperAlign'
import { buildPaperHighlightSegments, getTextNodeOffset } from '../utils/paperHighlights'

defineEmits(['back'])

const store = useAppStore()
const { runPaperAlign } = usePaperAlign()
const fileInputRef = ref(null)
const textViewRef = ref(null)
const paperContentRef = ref(null)
const paperLeftPaneWidth = ref(0)
const expandedClaims = ref({})
const uploadingFile = ref(false)
const fileError = ref('')
const inputMode = ref('text')  // 'text' | 'pdf'

const RESIZER_WIDTH = 4
const LEFT_MIN = 360
const RIGHT_MIN = 420
const DEFAULT_LEFT_RATIO = 0.42
const LEFT_MAX_RATIO = 0.65

const gridStyle = computed(() => ({
  '--paper-left-width': paperLeftPaneWidth.value ? `${paperLeftPaneWidth.value}px` : `${DEFAULT_LEFT_RATIO * 100}%`,
  '--paper-resizer-width': `${RESIZER_WIDTH}px`,
  '--paper-right-min': `${RIGHT_MIN}px`
}))

const resultItems = computed(() => store.paperAlignResult?.alignment_items || [])
const missingClaims = computed(() => store.paperAlignResult?.missing_claims || [])
const alignedCount = computed(() => resultItems.value.filter(item => item.status === 'aligned').length)
const partialCount = computed(() => resultItems.value.filter(item => item.status === 'partial').length)
const missingCount = computed(() => {
  const inlineMissing = resultItems.value.filter(item => item.status === 'missing').length
  return inlineMissing + missingClaims.value.length
})
const confidencePercent = computed(() => {
  if (typeof store.paperAlignResult?.confidence === 'number') {
    return Math.round(store.paperAlignResult.confidence * 100)
  }
  return null
})
const confidenceLabel = computed(() => (confidencePercent.value === null ? '--' : `${confidencePercent.value}%`))
const highlightSegments = computed(() => buildPaperHighlightSegments(store.paperAlignText, store.paperHighlights))
const charCount = computed(() => store.paperAlignText.length)
const repoLabel = computed(() => {
  if (!store.repoUrl) return 'No repository selected'
  const match = store.repoUrl.match(/github\.com[/:]([^/]+)\/([^/.#?]+)(?:\.git)?/i)
  return match ? `${match[1]}/${match[2]}` : store.repoUrl
})
const runDisabled = computed(() => {
  return !store.sessionId || !store.compiledPaperText || store.paperAlignLoading || uploadingFile.value
})
const runLabel = computed(() => {
  if (inputMode.value === 'pdf') {
    const count = store.paperSelections.length
    return count > 0 ? `Run Alignment (${count} selection${count === 1 ? '' : 's'})` : 'Run Alignment'
  }
  if (store.paperHighlights.length > 0) {
    const count = store.paperHighlights.length
    return `Run Alignment (${count} passage${count === 1 ? '' : 's'})`
  }
  return 'Run Alignment (All)'
})

function clampPaperLeftWidth(rawWidth, containerWidth) {
  const availableWidth = Math.max(containerWidth - RESIZER_WIDTH, 0)
  const maxByRightPane = Math.max(availableWidth - RIGHT_MIN, LEFT_MIN)
  const minLeftWidth = Math.min(LEFT_MIN, maxByRightPane)
  const maxLeftWidth = Math.max(
    minLeftWidth,
    Math.min(containerWidth * LEFT_MAX_RATIO, maxByRightPane)
  )

  return Math.min(Math.max(rawWidth, minLeftWidth), maxLeftWidth)
}

function syncPaperPaneWidth() {
  if (!paperContentRef.value) return
  const containerWidth = paperContentRef.value.getBoundingClientRect().width
  const fallbackWidth = containerWidth * DEFAULT_LEFT_RATIO
  paperLeftPaneWidth.value = clampPaperLeftWidth(paperLeftPaneWidth.value || fallbackWidth, containerWidth)
}

function handlePaperResize(clientX) {
  if (!paperContentRef.value) return
  const bounds = paperContentRef.value.getBoundingClientRect()
  paperLeftPaneWidth.value = clampPaperLeftWidth(clientX - bounds.left, bounds.width)
}

function toggleHighlightMode() {
  if (!store.paperAlignText.trim()) return
  store.setPaperHighlightMode(!store.paperHighlightMode)
  window.getSelection()?.removeAllRanges()
}

function handleTextInput(event) {
  store.paperAlignText = event.target.value
  store.clearPaperHighlights()
  fileError.value = ''

  if (!store.paperAlignText.trim()) {
    store.setPaperHighlightMode(false)
  }
}

async function handleFileUpload(event) {
  const file = event.target.files?.[0]
  if (!file) return

  uploadingFile.value = true
  fileError.value = ''

  try {
    if (file.name.toLowerCase().endsWith('.pdf')) {
      // PDF 妯″紡锛氱洿鎺ユ覆鏌擄紝涓嶆彁鍙栨枃鏈?      store.setPaperPdfFile(file)
      store.paperSelectionMode = 'pdf'
      store.clearPaperSelections()
      store.paperAlignText = ''
      store.clearPaperHighlights()
      store.setPaperHighlightMode(false)
      store.setPaperUploadedFileName(file.name)
      inputMode.value = 'pdf'
    } else {
      // 鏂囨湰妯″紡锛氭彁鍙栨枃鏈?      const text = await readUploadedText(file)
      if (!text) {
        throw new Error('No readable text was extracted from the selected file.')
      }
      store.paperAlignText = text
      store.setPaperUploadedFileName(file.name)
      store.clearPaperHighlights()
      store.setPaperHighlightMode(false)
      store.setPaperPdfFile(null)
      store.paperSelectionMode = 'text'
      inputMode.value = 'text'
    }
  } catch (error) {
    fileError.value = error?.message || 'Failed to read the selected file.'
  } finally {
    uploadingFile.value = false
    event.target.value = ''
  }
}

async function readUploadedText(file) {
  const fileName = file.name.toLowerCase()

  if (fileName.endsWith('.txt')) {
    return (await file.text()).trim()
  }

  throw new Error('Only .txt files are supported in text mode.')
}

function handleSelectionMouseUp() {
  if (!store.paperHighlightMode || !textViewRef.value) return

  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return

  const range = selection.getRangeAt(0)
  const container = textViewRef.value

  if (!container.contains(range.startContainer) || !container.contains(range.endContainer)) {
    return
  }

  const start = getTextNodeOffset(container, range.startContainer, range.startOffset)
  const end = getTextNodeOffset(container, range.endContainer, range.endOffset)

  if (end <= start) {
    selection.removeAllRanges()
    return
  }

  store.addPaperHighlight(start, end)
  selection.removeAllRanges()
}

function clearPaperInput() {
  store.paperAlignText = ''
  store.setPaperUploadedFileName('')
  store.clearPaperHighlights()
  store.setPaperHighlightMode(false)
  store.paperAlignResult = null
  store.paperAlignError = ''
  store.clearPaperSelections()
  store.setPaperPdfFile(null)
  store.paperSelectionMode = 'text'
  inputMode.value = 'text'
  fileError.value = ''
}


function toggleClaimDetails(index) {
  expandedClaims.value[index] = !expandedClaims.value[index]
}

function isClaimExpanded(index) {
  return Boolean(expandedClaims.value[index])
}

function hasResultDetails(item) {
  return Boolean(item.matched_files?.length || item.matched_symbols?.length || item.evidence_excerpt)
}

function formatStatus(status) {
  if (!status) return 'Unknown'
  return status.charAt(0).toUpperCase() + status.slice(1)
}

onMounted(() => {
  if (store.paperPdfFile) {
    inputMode.value = 'pdf'
    store.paperSelectionMode = 'pdf'
  }
  syncPaperPaneWidth()
  window.addEventListener('resize', syncPaperPaneWidth)
})

onUnmounted(() => {
  window.removeEventListener('resize', syncPaperPaneWidth)
})
</script>

<style scoped>
.paper-workspace {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.paper-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border-color);
  background: #faf9f6;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.back-btn,
.secondary-btn,
.run-btn,
.clear-btn,
.details-toggle {
  border: 1px solid var(--border-color);
  background: #fff;
  color: #292524;
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #78716c;
  font-size: 14px;
  font-weight: 500;
}

.back-btn svg {
  width: 16px;
  height: 16px;
}

.toolbar-divider {
  width: 1px;
  height: 24px;
  background: var(--border-color);
}

.toolbar-copy {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.toolbar-title {
  font-size: 14px;
  font-weight: 600;
  color: #1c1917;
}

.repo-dot,
.toolbar-repo {
  font-size: 12px;
  color: #78716c;
}

.toolbar-repo {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.topk-shell {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #57534e;
  flex-shrink: 0;
}

.topk-shell input {
  width: 48px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  text-align: center;
  background: #fff;
}

.paper-content {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: var(--paper-left-width, 42%) var(--paper-resizer-width, 10px) minmax(var(--paper-right-min, 420px), 1fr);
  grid-template-rows: minmax(0, 1fr);
  overflow: hidden;
}

.paper-input-panel,
.paper-results-panel {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.paper-input-panel {
  background: #fff;
}

.paper-resizer {
  width: 4px;
  background: transparent;
  border: 0;
}

.paper-resizer:hover {
  background: #e7e5e4;
}

.paper-resizer:deep(.resizer-handle) {
  width: 100%;
  height: 100%;
  border-radius: 0;
  background: transparent;
}

.paper-resizer:hover:deep(.resizer-handle) {
  background: #d6d3d1;
}

.input-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #f5f5f4;
  flex-shrink: 0;
}

.input-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.input-title-group h3,
.results-section h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #1c1917;
}

.section-icon {
  width: 14px;
  height: 14px;
  color: #a8a29e;
  flex: 0 0 14px;
}

.file-pill {
  max-width: 90px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border: 0;
  border-radius: 0;
  padding: 0;
  font-size: 12px;
  color: #a8a29e;
  background: transparent;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.secondary-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 6px 12px;
  color: #57534e;
  background: #fff;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  box-shadow: none;
}

.secondary-btn svg {
  width: 14px;
  height: 14px;
  color: inherit;
}

.secondary-btn.active {
  background: #ecfdf3;
  border-color: #b7ebc6;
  color: #166534;
}

.secondary-btn:disabled,
.run-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.paper-editor {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  padding: 16px;
}

.paper-editor textarea,
.selection-text-view {
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 0;
  background: #fff;
  color: #44403c;
  font-size: 14px;
  line-height: 1.65;
  resize: none;
  outline: none;
  box-sizing: border-box;
}

.paper-editor textarea::placeholder {
  color: #b8b4ae;
}

.paper-editor textarea {
  padding: 0;
  overflow: auto;
}

.selection-mode-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.selection-tip {
  flex-shrink: 0;
  margin: 0 0 8px;
  border: 1px solid #b7ebc6;
  border-radius: 8px;
  background: #f0fdf4;
  color: #166534;
  padding: 6px 12px;
  font-size: 12px;
}

.selection-text-view {
  flex: 1 1 auto;
  overflow: auto;
  white-space: pre-wrap;
  user-select: text;
  cursor: crosshair;
}

.highlight-mark {
  background: rgba(251, 191, 36, 0.32);
  border-bottom: 2px solid rgba(217, 119, 6, 0.55);
  border-radius: 2px;
  cursor: pointer;
  padding: 1px 0;
}

.empty-copy {
  color: #a8a29e;
}

.input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border-top: 1px solid #f5f5f4;
  background: #fff;
  flex-shrink: 0;
  min-width: 0;
}

.footer-meta,
.footer-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.footer-meta {
  font-size: 12px;
  color: #78716c;
  min-width: 0;
}

.footer-actions {
  justify-content: flex-end;
  min-width: 0;
}

.selected-meta {
  color: #166534;
  font-weight: 600;
}

.error-meta,
.results-state.error {
  color: #b91c1c;
}

.clear-btn {
  background: transparent;
  border: 0;
  color: #a8a29e;
  font-size: 12px;
  padding: 0;
}

.run-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: #292524;
  color: #fff;
  min-width: 0;
  min-height: 0;
  padding: 8px 14px;
  font-size: 12px;
  font-weight: 500;
}

.run-btn:disabled {
  opacity: 1;
  background: #d6d3d1;
  border-color: #d6d3d1;
  color: #fff;
}

.btn-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
  color: inherit;
}

.btn-icon svg {
  width: 12px;
  height: 12px;
  display: block;
}

.run-btn-label {
  display: inline-flex;
  align-items: center;
}

.spin {
  animation: button-spin 0.9s linear infinite;
}

.paper-results-panel {
  padding: 0;
  background: #faf9f6;
}

.results-summary {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 16px;
  border-bottom: 1px solid var(--border-color);
  background: #fff;
}

.results-scroll {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.confidence-block {
  flex-shrink: 0;
}

.summary-label {
  display: block;
  margin-bottom: 2px;
  font-size: 12px;
  font-weight: 500;
  color: #78716c;
}

.confidence-block strong {
  display: block;
  font-size: 30px;
  line-height: 1;
  color: #1c1917;
}

.summary-right {
  flex: 1;
  min-width: 0;
}

.summary-bar {
  height: 6px;
  border-radius: 999px;
  background: #e7e5e4;
  overflow: hidden;
}

.summary-bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #22c55e;
}

.summary-stats,
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.summary-stats {
  margin-top: 12px;
}

.stat-pill,
.status-pill,
.chip {
  display: inline-flex;
  align-items: center;
  border: 1px solid #e7e5e4;
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 500;
}

.stat-pill {
  background: #f5f5f4;
  border-color: #e7e5e4;
  color: #57534e;
}

.results-state {
  margin: 16px;
  padding: 16px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: #fff;
  color: #57534e;
  font-size: 14px;
}

.results-section {
  margin: 16px;
}

.results-section h4 {
  margin-bottom: 12px;
  color: #78716c;
  text-transform: uppercase;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.result-card {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: #fff;
  padding: 16px;
  overflow: hidden;
}

.result-card + .result-card {
  margin-top: 12px;
}

.result-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.result-top p {
  margin: 0;
  font-size: 14px;
  line-height: 1.65;
  color: #44403c;
}

.details-toggle {
  margin-top: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #78716c;
  font-size: 12px;
}

.result-details {
  margin-top: 12px;
}

.result-block + .result-block {
  margin-top: 10px;
}

.result-block strong {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  color: #a8a29e;
}

.chip {
  background: #f5f5f4;
  color: #44403c;
  border-radius: 4px;
  padding: 2px 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

blockquote {
  margin: 0;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fafaf9;
  color: #44403c;
  border: 1px solid var(--border-color);
  font-size: 12px;
  line-height: 1.7;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.missing-card {
  background: #fff;
}

.missing-reason {
  margin-top: 10px;
  font-size: 13px;
  color: #78716c;
}

@media (max-width: 1180px) {
  .paper-content {
    grid-template-columns: 1fr;
    grid-template-rows: auto;
    overflow-y: auto;
  }

  .paper-input-panel {
    border-bottom: 1px solid var(--border-color);
  }

  .paper-resizer {
    display: none;
  }

  .results-summary {
    flex-direction: column;
    align-items: flex-start;
  }
}

.pdf-mode-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.pdf-selection-tip {
  flex-shrink: 0;
  padding: 6px 12px;
  font-size: 12px;
  color: #3b82f6;
  background: #eff6ff;
  border-bottom: 1px solid #bfdbfe;
}

.pdf-scroll-area {
  flex: 1 1 auto;
  min-height: 0;
  height: 100%;
  overflow-y: auto;
  overflow-x: auto;
  background: #525659;
  padding: 12px;
  box-sizing: border-box;
}

.selections-list {
  flex-shrink: 0;
  border-top: 1px solid #f5f5f4;
  padding: 8px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 140px;
  overflow-y: auto;
  background: #fafaf9;
}

.sel-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  border: 1px solid #e7e5e4;
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  min-width: 0;
}

.sel-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #44403c;
}

.sel-remove {
  flex-shrink: 0;
  background: transparent;
  border: 0;
  color: #a8a29e;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
}

.sel-remove:hover {
  color: #ef4444;
}

@keyframes button-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>


