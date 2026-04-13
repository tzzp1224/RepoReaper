<template>
  <div class="repo-input-row">
    <div class="repo-input-shell">
      <span class="repo-prefix">Repo</span>
      <input
        v-model="store.repoUrl"
        type="text"
        placeholder="GitHub repository URL"
        @keypress.enter="handleAnalyzeClick"
        @input="handleUrlChange"
      />
    </div>

    <div class="lang-toggle">
      <button
        v-for="option in languageOptions"
        :key="option.value"
        type="button"
        :class="{ active: selectedLang === option.value }"
        @click="handleLangChange(option.value)"
      >
        {{ option.label }}
      </button>
    </div>

    <button
      class="analyze-btn"
      :class="store.buttonClass"
      :disabled="store.buttonDisabled"
      @click="handleAnalyzeClick"
    >
      {{ buttonLabel }}
    </button>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useAppStore, BTN_STATE } from '../stores/app'
import { useAnalysis } from '../composables/useAnalysis'

const store = useAppStore()
const { handleAnalyzeClick, handleLanguageChange } = useAnalysis()

const selectedLang = ref(store.language)
let urlCheckTimeout = null

const languageOptions = [
  { value: 'en', label: 'ENG' },
  { value: 'zh', label: '中文' }
]

const buttonLabel = computed(() => {
  switch (store.buttonState) {
    case BTN_STATE.GENERATE:
      return store.language === 'zh' ? 'Generate 中文' : 'Generate EN'
    case BTN_STATE.REANALYZE:
      return 'Reanalyze'
    case BTN_STATE.CHECKING:
      return 'Checking...'
    case BTN_STATE.ANALYZING:
      return 'Analyzing...'
    default:
      return 'Analyze'
  }
})

watch(() => store.language, value => {
  selectedLang.value = value
})

function handleUrlChange() {
  clearTimeout(urlCheckTimeout)
  store.hideHint()
  urlCheckTimeout = setTimeout(() => {
    store.checkUrl()
  }, 500)
}

function handleLangChange(nextLanguage) {
  if (nextLanguage === store.language) return
  selectedLang.value = nextLanguage
  handleLanguageChange(nextLanguage)
}
</script>

<style scoped>
.repo-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.repo-input-shell {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 1px 0 rgba(12, 10, 9, 0.02);
}

.repo-prefix {
  font-size: 12px;
  color: #78716c;
  padding: 2px 6px;
  border-radius: 999px;
  background: #f5f5f4;
  border: 1px solid #e7e5e4;
  flex-shrink: 0;
}

.repo-input-shell input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: #1f2937;
  font-size: 14px;
}

.lang-toggle {
  display: flex;
  padding: 3px;
  border-radius: 8px;
  background: #f5f5f4;
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}

.lang-toggle button {
  border: 0;
  background: transparent;
  color: #78716c;
  font-size: 12px;
  line-height: 1;
  padding: 9px 12px;
  border-radius: 6px;
  cursor: pointer;
}

.lang-toggle button.active {
  background: #fff;
  color: #1c1917;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.analyze-btn {
  border: 0;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
  padding: 12px 16px;
  border-radius: 8px;
  cursor: pointer;
  white-space: nowrap;
  transition: transform 0.15s ease, opacity 0.15s ease, background 0.15s ease;
}

.analyze-btn:hover:not(:disabled) {
  transform: translateY(-1px);
}

.analyze-btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

.btn-analyze {
  background: #2563eb;
}

.btn-generate {
  background: #16a34a;
}

.btn-reanalyze {
  background: #44403c;
}

.btn-checking {
  background: #a8a29e;
}
</style>
