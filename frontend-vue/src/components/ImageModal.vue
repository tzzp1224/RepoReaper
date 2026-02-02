<template>
  <Teleport to="body">
    <div v-if="visible" class="modal" @click="close">
      <span class="close-btn" @click="close">&times;</span>
      <div class="zoom-controls" @click.stop>
        <button class="zoom-btn" @click="zoomOut" title="缩小">−</button>
        <span class="zoom-level">{{ Math.round(scale * 100) }}%</span>
        <button class="zoom-btn" @click="zoomIn" title="放大">+</button>
        <button class="zoom-btn" @click="resetZoom" title="重置">↺</button>
        <button class="zoom-btn" @click="fitToScreen" title="适应屏幕">⤢</button>
      </div>
      <div 
        class="modal-content-wrapper" 
        @click.stop
        @wheel="handleWheel"
        ref="wrapperRef"
      >
        <div 
          class="modal-content" 
          ref="contentRef"
        ></div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  visible: Boolean,
  content: String
})

const emit = defineEmits(['close'])

const scale = ref(1)
const wrapperRef = ref(null)
const contentRef = ref(null)
let originalSvgWidth = 0
let originalSvgHeight = 0

function close() {
  emit('close')
}

function zoomIn() {
  scale.value = Math.min(scale.value + 0.25, 5)
  applyScale()
}

function zoomOut() {
  scale.value = Math.max(scale.value - 0.25, 0.25)
  applyScale()
}

function resetZoom() {
  scale.value = 1
  applyScale()
}

function applyScale() {
  if (!contentRef.value) return
  const svg = contentRef.value.querySelector('svg')
  if (!svg || !originalSvgWidth) return
  
  svg.style.width = `${originalSvgWidth * scale.value}px`
  svg.style.height = `${originalSvgHeight * scale.value}px`
}

function fitToScreen() {
  if (!contentRef.value || !originalSvgWidth) return
  
  // 获取容器可用空间 (留出边距)
  const containerWidth = window.innerWidth * 0.85
  const containerHeight = window.innerHeight * 0.75
  
  // 计算适合的缩放比例
  const scaleX = containerWidth / originalSvgWidth
  const scaleY = containerHeight / originalSvgHeight
  
  // 选择较小的比例以确保完全可见
  scale.value = Math.min(scaleX, scaleY, 4)
  applyScale()
}

function handleWheel(e) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -0.15 : 0.15
  scale.value = Math.max(0.25, Math.min(5, scale.value + delta))
  applyScale()
}

function handleKeydown(e) {
  if (!props.visible) return
  if (e.key === 'Escape') {
    close()
  } else if (e.key === '+' || e.key === '=') {
    zoomIn()
  } else if (e.key === '-') {
    zoomOut()
  } else if (e.key === '0') {
    resetZoom()
  }
}

// 初始化 SVG 内容
function initSvgContent() {
  if (!contentRef.value || !props.content) return
  
  // 插入内容
  contentRef.value.innerHTML = props.content
  
  const svg = contentRef.value.querySelector('svg')
  if (!svg) return
  
  // 获取原始尺寸
  // 优先从 viewBox 获取，否则从 width/height 属性获取
  const viewBox = svg.viewBox?.baseVal
  if (viewBox && viewBox.width > 0) {
    originalSvgWidth = viewBox.width
    originalSvgHeight = viewBox.height
  } else {
    // 从属性或 getBoundingClientRect 获取
    const widthAttr = svg.getAttribute('width')
    const heightAttr = svg.getAttribute('height')
    
    if (widthAttr && heightAttr) {
      originalSvgWidth = parseFloat(widthAttr) || 800
      originalSvgHeight = parseFloat(heightAttr) || 600
    } else {
      // 临时设置可见以获取尺寸
      const rect = svg.getBoundingClientRect()
      originalSvgWidth = rect.width || 800
      originalSvgHeight = rect.height || 600
    }
  }
  
  // 确保最小尺寸
  if (originalSvgWidth < 100) originalSvgWidth = 800
  if (originalSvgHeight < 100) originalSvgHeight = 600
  
  // 移除 SVG 上可能存在的 max-width 限制
  svg.style.maxWidth = 'none'
  svg.style.maxHeight = 'none'
  
  // 自动适应屏幕
  fitToScreen()
}

// 监听显示状态变化
watch(() => props.visible, (visible) => {
  if (visible) {
    scale.value = 1
    originalSvgWidth = 0
    originalSvgHeight = 0
    nextTick(() => {
      initSvgContent()
    })
  }
})

// 监听内容变化
watch(() => props.content, () => {
  if (props.visible) {
    nextTick(() => {
      initSvgContent()
    })
  }
})

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<style scoped>
.modal {
  display: flex;
  position: fixed;
  z-index: 9999;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  background-color: rgba(0, 0, 0, 0.9);
  justify-content: center;
  align-items: center;
  flex-direction: column;
}

.close-btn {
  position: fixed;
  top: 15px;
  right: 25px;
  color: #fff;
  font-size: 36px;
  font-weight: bold;
  cursor: pointer;
  transition: all 0.2s;
  z-index: 10001;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.2);
  transform: scale(1.1);
}

.zoom-controls {
  position: fixed;
  top: 15px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(10px);
  padding: 8px 16px;
  border-radius: 24px;
  z-index: 10001;
}

.zoom-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  font-size: 18px;
  font-weight: bold;
  border-radius: 50%;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.zoom-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: scale(1.1);
}

.zoom-level {
  color: #fff;
  font-size: 14px;
  min-width: 50px;
  text-align: center;
  font-weight: 500;
}

.modal-content-wrapper {
  max-width: 95vw;
  max-height: 85vh;
  overflow: auto;
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
  margin-top: 60px;
}

.modal-content {
  display: flex;
  justify-content: center;
  align-items: center;
  min-width: 200px;
  min-height: 200px;
}

.modal-content :deep(svg) {
  display: block;
  transition: width 0.15s ease, height 0.15s ease;
}
</style>
