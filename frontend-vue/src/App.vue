<template>
  <div class="app-container">
    <!-- 顶部导航 -->
    <AppHeader />
    
    <!-- 主内容区 -->
    <div class="main-container" ref="mainRef">
      <!-- 左侧面板：输入 + 日志 + 报告/Insights -->
      <div class="left-panel" :style="{ width: leftPanelWidth + '%' }">
        <div class="input-section">
          <UrlInput />
          <SmartHint />
        </div>
        
        <div class="panel-content">
          <LogsArea />

          <!-- Tab 栏 -->
          <div class="insight-tabs">
            <button
              class="tab-btn"
              :class="{ active: store.activeInsightTab === 'report' }"
              @click="store.activeInsightTab = 'report'"
            >
              📊 Report
            </button>
            <button
              class="tab-btn"
              :class="{ active: store.activeInsightTab === 'issues' }"
              @click="store.activeInsightTab = 'issues'"
            >
              📋 Issues Notebook
            </button>
            <button
              class="tab-btn"
              :class="{ active: store.activeInsightTab === 'roadmap' }"
              @click="store.activeInsightTab = 'roadmap'"
            >
              🗺️ Commit Roadmap
            </button>
          </div>

          <!-- Tab 内容 -->
          <ReportPanel v-show="store.activeInsightTab === 'report'" @open-modal="openModal" />
          <IssuePanel v-show="store.activeInsightTab === 'issues'" />
          <RoadmapPanel v-show="store.activeInsightTab === 'roadmap'" @open-modal="openModal" />
        </div>
      </div>
      
      <!-- 拖拽条 -->
      <PanelResizer @resize="handleResize" />
      
      <!-- 右侧面板：聊天 -->
      <div class="right-panel">
        <ChatPanel />
      </div>
    </div>
    
    <!-- 模态框 -->
    <ImageModal 
      :visible="modalVisible" 
      :content="modalContent"
      @close="closeModal"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import AppHeader from './components/AppHeader.vue'
import UrlInput from './components/UrlInput.vue'
import LogsArea from './components/LogsArea.vue'
import SmartHint from './components/SmartHint.vue'
import ReportPanel from './components/ReportPanel.vue'
import IssuePanel from './components/IssuePanel.vue'
import RoadmapPanel from './components/RoadmapPanel.vue'
import ChatPanel from './components/ChatPanel.vue'
import PanelResizer from './components/PanelResizer.vue'
import ImageModal from './components/ImageModal.vue'
import { useAppStore } from './stores/app'

const store = useAppStore()

// 面板宽度
const leftPanelWidth = ref(50)
const mainRef = ref(null)

function handleResize(clientX) {
  if (!mainRef.value) return
  const containerWidth = mainRef.value.offsetWidth
  const newWidth = (clientX / containerWidth) * 100
  if (newWidth > 20 && newWidth < 80) {
    leftPanelWidth.value = newWidth
  }
}

// 模态框
const modalVisible = ref(false)
const modalContent = ref('')

function openModal(content) {
  modalContent.value = content
  modalVisible.value = true
}

function closeModal() {
  modalVisible.value = false
}
</script>

<style scoped>
.app-container {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.main-container {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.left-panel {
  min-width: 320px;
  background: var(--panel-bg);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border-color);
}

.right-panel {
  flex: 1;
  min-width: 320px;
  background: var(--bg-color);
  display: flex;
  flex-direction: column;
}

.input-section {
  padding: 0 24px;
  flex-shrink: 0;
}

.panel-content {
  flex: 1;
  padding: 0 24px 24px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.insight-tabs {
  display: flex;
  gap: 4px;
  padding: 6px 0 10px;
  flex-shrink: 0;
}

.tab-btn {
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.tab-btn:hover {
  color: var(--text-primary);
  background: #f1f5f9;
}

.tab-btn.active {
  color: var(--primary-color);
  background: #eff6ff;
  border-color: #bfdbfe;
  font-weight: 600;
}
</style>
