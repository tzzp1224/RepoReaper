<template>
  <Teleport to="body">
    <div v-if="visible" class="modal" @click="close">
      <span class="close-btn" @click="close">&times;</span>
      <div class="modal-content-wrapper" @click.stop>
        <div class="modal-content" v-html="content"></div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'

const props = defineProps({
  visible: Boolean,
  content: String
})

const emit = defineEmits(['close'])

function close() {
  emit('close')
}

function handleKeydown(e) {
  if (e.key === 'Escape') {
    close()
  }
}

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
  background-color: rgba(0, 0, 0, 0.85);
  justify-content: center;
  align-items: center;
}

.close-btn {
  position: fixed;
  top: 20px;
  right: 30px;
  color: #fff;
  font-size: 40px;
  font-weight: bold;
  cursor: pointer;
  transition: color 0.2s;
  z-index: 10000;
}

.close-btn:hover {
  color: #f8f8f8;
}

.modal-content-wrapper {
  max-width: 95vw;
  max-height: 95vh;
  overflow: auto;
  background: white;
  border-radius: 8px;
  padding: 20px;
}

.modal-content :deep(svg) {
  max-width: none;
  height: auto;
}
</style>
