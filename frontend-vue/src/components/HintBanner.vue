<template>
  <transition name="hint-fade">
    <div v-if="store.hint.key" :class="['hint-banner', `hint-${store.hint.type}`]">
      <span class="hint-dot"></span>
      <span class="hint-copy" v-html="hintMessage"></span>
    </div>
  </transition>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { getHint } from '../utils/hints'

const store = useAppStore()
const hintMessage = computed(() => getHint(store.hint.key, store.language))
</script>

<style scoped>
.hint-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid transparent;
  font-size: 13px;
  line-height: 1.45;
}

.hint-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  margin-top: 5px;
  background: currentColor;
  opacity: 0.75;
}

.hint-copy {
  flex: 1;
}

.hint-copy :deep(strong) {
  font-weight: 600;
}

.hint-info {
  background: #eff6ff;
  border-color: #bfdbfe;
  color: #1d4ed8;
}

.hint-success {
  background: #edf7ee;
  border-color: #b9dfc0;
  color: #166534;
}

.hint-warning {
  background: #fef3c7;
  border-color: #f5d88b;
  color: #92400e;
}

.hint-fade-enter-active,
.hint-fade-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.hint-fade-enter-from,
.hint-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
