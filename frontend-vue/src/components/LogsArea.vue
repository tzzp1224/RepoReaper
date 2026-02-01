<template>
  <div class="logs-area" ref="logsRef">
    <div 
      v-for="(log, index) in store.logs" 
      :key="index"
      :style="{ color: log.color }"
    >
      {{ log.text }}
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const logsRef = ref(null)

// 自动滚动到底部
watch(() => store.logs.length, async () => {
  await nextTick()
  if (logsRef.value) {
    logsRef.value.scrollTop = logsRef.value.scrollHeight
  }
})
</script>

<style scoped>
.logs-area {
  background: #f1f5f9;
  color: #475569;
  padding: 12px 16px;
  border-radius: 8px;
  height: 100px;
  max-height: 100px;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 14px;
  margin-bottom: 12px;
  line-height: 1.5;
  flex-shrink: 0;
  border: 1px solid var(--border-color);
}
</style>
