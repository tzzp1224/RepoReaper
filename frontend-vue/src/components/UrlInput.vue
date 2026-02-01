<template>
  <div class="input-bar">
    <input 
      type="text" 
      v-model="store.repoUrl"
      placeholder="Enter GitHub Repo URL (e.g., https://github.com/owner/repo)"
      @keypress.enter="handleEnter"
      @input="handleUrlChange"
    />
    
    <div class="lang-toggle">
      <input 
        type="radio" 
        id="lang-en" 
        value="en" 
        v-model="selectedLang"
        @change="handleLangChange"
      />
      <label for="lang-en" title="Generate report in English">ENG</label>
      
      <input 
        type="radio" 
        id="lang-zh" 
        value="zh" 
        v-model="selectedLang"
        @change="handleLangChange"
      />
      <label for="lang-zh" title="使用中文生成报告">中文</label>
    </div>
    
    <button 
      :class="['analyze-btn', store.buttonClass]"
      :disabled="store.buttonDisabled"
      @click="handleAnalyzeClick"
    >
      {{ store.buttonText }}
    </button>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { useAnalysis } from '../composables/useAnalysis'

const store = useAppStore()
const { handleAnalyzeClick, handleLanguageChange } = useAnalysis()

const selectedLang = ref(store.language)
let urlCheckTimeout = null

// 同步语言状态
watch(() => store.language, (val) => {
  selectedLang.value = val
})

function handleEnter() {
  handleAnalyzeClick()
}

function handleUrlChange() {
  clearTimeout(urlCheckTimeout)
  store.hideHint()
  urlCheckTimeout = setTimeout(() => {
    store.checkUrl()
  }, 500)
}

function handleLangChange() {
  handleLanguageChange(selectedLang.value)
}
</script>

<style scoped>
.input-bar {
  padding: 20px;
  border-bottom: 1px solid var(--border-color);
  background: #ffffff;
  display: flex;
  gap: 12px;
  align-items: center;
}

input[type="text"] {
  flex: 1;
  padding: 12px 16px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  outline: none;
  font-size: 16px;
  transition: all 0.2s;
  background: #f8fafc;
}

input[type="text"]:focus {
  border-color: var(--primary-color);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

/* 语言切换 */
.lang-toggle {
  display: flex;
  background: #f1f5f9;
  padding: 4px;
  border-radius: 10px;
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}

.lang-toggle input[type="radio"] {
  display: none;
}

.lang-toggle label {
  padding: 8px 16px;
  font-size: 14px;
  cursor: pointer;
  border-radius: 8px;
  color: var(--text-secondary);
  font-weight: 500;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  user-select: none;
}

.lang-toggle label:hover {
  color: var(--primary-color);
}

.lang-toggle input[type="radio"]:checked + label {
  background: #ffffff;
  color: var(--primary-color);
  box-shadow: 0 2px 4px rgba(0,0,0,0.06);
  transform: scale(1.02);
}

/* 按钮样式 */
.analyze-btn {
  color: white;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 500;
  font-size: 16px;
  transition: all 0.2s;
  white-space: nowrap;
}

.analyze-btn:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.btn-analyze {
  background: linear-gradient(135deg, #2563eb, #1d4ed8);
}
.btn-analyze:hover:not(:disabled) {
  background: linear-gradient(135deg, #1d4ed8, #1e40af);
}

.btn-generate {
  background: linear-gradient(135deg, #10b981, #059669);
}
.btn-generate:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
}

.btn-reanalyze {
  background: linear-gradient(135deg, #f59e0b, #d97706);
}
.btn-reanalyze:hover:not(:disabled) {
  background: linear-gradient(135deg, #d97706, #b45309);
}

.btn-checking {
  background: #94a3b8;
}
</style>
