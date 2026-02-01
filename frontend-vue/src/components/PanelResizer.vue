<template>
  <div 
    class="resizer" 
    @mousedown="startResize"
  >
    <span class="resizer-handle">⋮</span>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'

const emit = defineEmits(['resize'])

let isResizing = false

function startResize() {
  isResizing = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onMouseMove(e) {
  if (!isResizing) return
  emit('resize', e.clientX)
}

function onMouseUp() {
  if (isResizing) {
    isResizing = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
}

onMounted(() => {
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
})

onUnmounted(() => {
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
})
</script>

<style scoped>
.resizer {
  width: 10px;
  background: #f1f5f9;
  cursor: col-resize;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-left: 1px solid var(--border-color);
  border-right: 1px solid var(--border-color);
  transition: background 0.2s;
}

.resizer:hover {
  background: #e2e8f0;
}

.resizer-handle {
  color: #94a3b8;
  font-size: 14px;
  pointer-events: none;
}
</style>
