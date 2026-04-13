<template>
  <div class="logs-shell">
    <button class="logs-header" type="button" @click="collapsed = !collapsed">
      <div class="logs-title">
        <span class="logs-indicator"></span>
        <span class="logs-name">analysis.log</span>
      </div>
      <span class="logs-count">{{ store.logs.length }} entries</span>
    </button>

    <div v-if="!collapsed" ref="logsRef" class="logs-body">
      <div v-for="(log, index) in store.logs" :key="index" class="log-row">
        <span class="log-text" :style="{ color: log.color }">{{ log.text }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const logsRef = ref(null)
const collapsed = ref(false)

watch(() => store.logs.length, async () => {
  await nextTick()
  if (logsRef.value) {
    logsRef.value.scrollTop = logsRef.value.scrollHeight
  }
})
</script>

<style scoped>
.logs-shell {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.logs-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid var(--border-color);
  background: #f5f5f4;
  cursor: pointer;
}

.logs-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logs-indicator {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #22c55e;
}

.logs-name,
.logs-count {
  font-size: 12px;
  color: #78716c;
}

.logs-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.logs-body {
  height: 108px;
  overflow-y: auto;
  background: #fafaf9;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.45;
}

.log-row {
  color: #57534e;
  white-space: pre-wrap;
  word-break: break-word;
}

.log-text {
  color: inherit;
}
</style>
