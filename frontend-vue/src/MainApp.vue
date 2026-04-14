<template>
  <div class="app-shell">
    <WorkspaceHeader :view="view" />

    <template v-if="view === 'main'">
      <div ref="mainRef" class="main-workspace" :style="gridStyle">
        <section class="left-pane">
          <div class="left-pane-top">
            <RepoInputBar />
            <HintBanner />
            <ConsoleLogs />
          </div>

          <WorkspaceTabs
            :active-tab="store.activeInsightTab"
            :paper-align-enabled="store.canUseAnalyzedContext"
            @change="handleTabChange"
            @paper-align="handlePaperAlign"
          />

          <div class="left-pane-body">
            <ReportPanel v-show="store.activeInsightTab === 'report'" @open-modal="openModal" />
            <IssuePanel v-show="store.activeInsightTab === 'issues'" />
            <RoadmapPanel v-show="store.activeInsightTab === 'roadmap'" @open-modal="openModal" />
            <ScorePanel
              v-show="store.activeInsightTab === 'score'"
              :active="store.activeInsightTab === 'score'"
            />
          </div>
        </section>

        <PanelResizer @resize="handleResize" />

        <section class="right-pane">
          <WorkspaceChat />
        </section>
      </div>
    </template>

    <template v-else>
      <PaperAlignWorkspace @back="view = 'main'" />
    </template>

    <ImageModal :visible="modalVisible" :content="modalContent" @close="closeModal" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import WorkspaceHeader from './components/WorkspaceHeader.vue'
import RepoInputBar from './components/RepoInputBar.vue'
import HintBanner from './components/HintBanner.vue'
import ConsoleLogs from './components/ConsoleLogs.vue'
import WorkspaceTabs from './components/WorkspaceTabs.vue'
import ReportPanel from './components/ReportPanel.vue'
import IssuePanel from './components/IssuePanel.vue'
import RoadmapPanel from './components/RoadmapPanel.vue'
import ScorePanel from './components/ScorePanel.vue'
import WorkspaceChat from './components/WorkspaceChat.vue'
import PaperAlignWorkspace from './components/PaperAlignWorkspace.vue'
import PanelResizer from './components/PanelResizer.vue'
import ImageModal from './components/ImageModal.vue'
import { useAppStore } from './stores/app'

const store = useAppStore()

const view = ref('main')
const leftPaneWidth = ref(0)
const mainRef = ref(null)
const modalVisible = ref(false)
const modalContent = ref('')

const RESIZER_WIDTH = 10
const LEFT_MIN = 320
const RIGHT_MIN = 320
const DEFAULT_LEFT_RATIO = 0.58
const LEFT_MIN_RATIO = 0.3
const LEFT_MAX_RATIO = 0.75

const gridStyle = computed(() => ({
  gridTemplateColumns: `${leftPaneWidth.value}px ${RESIZER_WIDTH}px minmax(${RIGHT_MIN}px, 1fr)`
}))

function clampLeftWidth(rawWidth, containerWidth) {
  const availableWidth = Math.max(containerWidth - RESIZER_WIDTH, 0)
  const maxByRightPane = Math.max(availableWidth - RIGHT_MIN, LEFT_MIN)
  const minLeftWidth = Math.min(Math.max(containerWidth * LEFT_MIN_RATIO, LEFT_MIN), maxByRightPane)
  const maxLeftWidth = Math.max(
    minLeftWidth,
    Math.min(containerWidth * LEFT_MAX_RATIO, maxByRightPane)
  )

  return Math.min(Math.max(rawWidth, minLeftWidth), maxLeftWidth)
}

function syncPaneWidth() {
  if (!mainRef.value) return
  const containerWidth = mainRef.value.getBoundingClientRect().width
  const fallbackWidth = containerWidth * DEFAULT_LEFT_RATIO
  leftPaneWidth.value = clampLeftWidth(leftPaneWidth.value || fallbackWidth, containerWidth)
}

function handleResize(clientX) {
  if (!mainRef.value) return
  const bounds = mainRef.value.getBoundingClientRect()
  leftPaneWidth.value = clampLeftWidth(clientX - bounds.left, bounds.width)
}

function handleTabChange(tab) {
  store.activeInsightTab = tab
}

function handlePaperAlign() {
  if (!store.canUseAnalyzedContext) {
    store.addLog('ℹ️ Analyze or load repository context before opening Paper Align.', '#f59e0b')
    return
  }
  view.value = 'paper-align'
}

function openModal(content) {
  modalContent.value = content
  modalVisible.value = true
}

function closeModal() {
  modalVisible.value = false
}

onMounted(() => {
  syncPaneWidth()
  window.addEventListener('resize', syncPaneWidth)
})

onUnmounted(() => {
  window.removeEventListener('resize', syncPaneWidth)
})
</script>

<style scoped>
.app-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.main-workspace {
  flex: 1;
  display: grid;
  min-height: 0;
  overflow: hidden;
}

.left-pane {
  min-width: 0;
  background: #fff;
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.left-pane-top {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.left-pane-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--shell-bg);
  padding-top: 0;
}

.right-pane {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: stretch;
  min-height: 0;
  overflow: hidden;
}
</style>
