<template>
  <div class="resizer" @mousedown="startResize">
    <span class="resizer-handle" aria-hidden="true"></span>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'

const emit = defineEmits(['resize'])

let isResizing = false

function startResize(event) {
  event.preventDefault()
  isResizing = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onMouseMove(event) {
  if (!isResizing) return
  emit('resize', event.clientX)
}

function onMouseUp() {
  if (!isResizing) return
  isResizing = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
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
  background: #f5f5f4;
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
  background: #ede7e1;
}

.resizer-handle {
  width: 2px;
  height: 52px;
  border-radius: 999px;
  background: #d6d3d1;
  pointer-events: none;
}
</style>
