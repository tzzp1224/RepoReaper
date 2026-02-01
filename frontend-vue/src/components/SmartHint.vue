<template>
  <Transition name="slide">
    <div 
      v-if="store.hint.key" 
      :class="['smart-hint', `hint-${store.hint.type}`]"
    >
      <span class="hint-icon">💡</span>
      <span class="hint-text" v-html="hintMessage"></span>
    </div>
  </Transition>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { getHint } from '../utils/i18n'

const store = useAppStore()

const hintMessage = computed(() => {
  return getHint(store.hint.key, store.language)
})
</script>

<style scoped>
.smart-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin: 8px 0 0;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.4;
}

.hint-info {
  background: linear-gradient(135deg, #e0f2fe, #f0f9ff);
  border: 1px solid #7dd3fc;
  color: #0369a1;
}

.hint-success {
  background: linear-gradient(135deg, #d1fae5, #ecfdf5);
  border: 1px solid #6ee7b7;
  color: #047857;
}

.hint-warning {
  background: linear-gradient(135deg, #fef3c7, #fffbeb);
  border: 1px solid #fcd34d;
  color: #92400e;
}

.hint-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.hint-text {
  flex: 1;
}

.hint-text :deep(strong) {
  font-weight: 600;
}

/* 动画 */
.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s ease-out;
}

.slide-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}

.slide-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>
