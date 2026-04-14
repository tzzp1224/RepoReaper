<template>
  <div class="score-panel">
    <div class="score-content">
      <div v-if="store.scoreLoading" class="panel-state">Reproducibility score is being prepared.</div>
      <div v-else-if="!store.canUseAnalyzedContext" class="placeholder">
        <div class="placeholder-icon" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 15.5V9.5" />
            <path d="M10 15.5V5.5" />
            <path d="M16 15.5V11.5" />
          </svg>
        </div>
        <div class="placeholder-title">Reproducibility Score</div>
        <div class="placeholder-text">The reproducibility score will be generated here.</div>
      </div>
      <div v-else-if="store.scoreError" class="panel-state error">{{ store.scoreError }}</div>
      <div v-else-if="!store.scoreResult" class="placeholder">
        <div class="placeholder-icon" aria-hidden="true">
          <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 15.5V9.5" />
            <path d="M10 15.5V5.5" />
            <path d="M16 15.5V11.5" />
          </svg>
        </div>
        <div class="placeholder-title">Reproducibility Score</div>
        <div class="placeholder-text">The reproducibility score will be generated here.</div>
      </div>
      <template v-else>
        <section class="score-summary-card">
          <div class="score-ring-wrap">
            <div class="score-ring" :style="{ '--score': `${store.scoreResult.overall_score}%` }">
              <div class="score-ring-inner">
                <div class="score-number">{{ store.scoreResult.overall_score }}</div>
                <div class="score-unit">/ 100</div>
              </div>
            </div>
          </div>

          <div class="score-summary-copy">
            <div class="badge-row">
              <span class="summary-badge">{{ store.scoreResult.level }}</span>
              <span class="summary-badge">{{ store.scoreResult.quality_tier }}</span>
            </div>
            <p class="summary-text">{{ store.scoreResult.summary || 'No summary available.' }}</p>
          </div>
        </section>

        <section class="score-card">
          <h3>Dimension Scores</h3>
          <div v-for="item in dimensions" :key="item.key" class="dimension-row">
            <div class="dimension-head">
              <span>{{ item.label }}</span>
              <span>{{ item.value }}</span>
            </div>
            <div class="dimension-bar">
              <div class="dimension-fill" :style="{ width: `${item.value}%` }"></div>
            </div>
          </div>
        </section>

        <section class="score-card">
          <h3>Reproducibility Risks</h3>
          <div v-if="!store.scoreResult.risks?.length" class="empty-copy">No explicit risks returned by the backend.</div>
          <div v-for="(risk, index) in store.scoreResult.risks || []" :key="index" class="risk-card">
            <div class="risk-title">{{ risk.title }}</div>
            <div class="risk-reason">{{ risk.reason }}</div>
            <div v-if="risk.evidence_refs?.length" class="chip-row">
              <span v-for="reference in risk.evidence_refs" :key="reference" class="chip">{{ reference }}</span>
            </div>
          </div>
        </section>


      </template>
    </div>

    <div v-if="store.scoreLoading" class="streaming-indicator">
      <span class="dot-pulse"></span> Generating reproducibility score...
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { useScore } from '../composables/useScore'

const props = defineProps({
  active: {
    type: Boolean,
    default: false
  }
})

const store = useAppStore()
const { loadScore } = useScore()
const lastLoadKey = ref('')

const dimensions = computed(() => {
  const source = store.scoreResult?.dimension_scores || {}
  return [
    { key: 'code_structure', label: 'Code Structure', value: source.code_structure || 0 },
    { key: 'docs_quality', label: 'Docs Quality', value: source.docs_quality || 0 },
    { key: 'env_readiness', label: 'Environment Readiness', value: source.env_readiness || 0 },
    { key: 'community_stability', label: 'Community Stability', value: source.community_stability || 0 }
  ]
})

watch(
  () => [props.active, store.language, store.sessionId, store.repoUrl, store.canUseAnalyzedContext],
  ([active, language, sessionId, repoUrl, canUse]) => {
    if (!active || !canUse || !sessionId || !repoUrl || store.scoreLoading) return
    const loadKey = `${sessionId}:${repoUrl}:${language}`
    if (lastLoadKey.value === loadKey && store.scoreResult?.language === language) return
    lastLoadKey.value = loadKey
    loadScore()
  },
  { immediate: true }
)
</script>

<style scoped>
.score-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.score-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-state {
  padding: 24px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: #fff;
  color: #57534e;
  font-size: 14px;
}

.panel-state.error {
  color: #b91c1c;
}

.placeholder {
  min-height: 280px;
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 8px;
  padding: 32px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: #fff;
}

.placeholder-icon {
  width: 48px;
  height: 48px;
  border-radius: 16px;
  background: #f5f5f4;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #78716c;
}

.placeholder-icon svg {
  width: 20px;
  height: 20px;
}

.placeholder-title {
  font-size: 14px;
  font-weight: 500;
  color: #57534e;
}

.placeholder-text {
  font-size: 12px;
  color: #a8a29e;
}

.score-summary-card,
.score-card {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}

.score-summary-card {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 18px;
}

.score-ring-wrap {
  flex-shrink: 0;
}

.score-ring {
  --score: 0%;
  width: 124px;
  height: 124px;
  border-radius: 50%;
  background:
    radial-gradient(circle closest-side, #fff 72%, transparent 73% 100%),
    conic-gradient(#1b7f48 var(--score), #e7e5e4 0);
  display: grid;
  place-items: center;
}

.score-ring-inner {
  text-align: center;
}

.score-number {
  font-size: 30px;
  line-height: 1;
  color: #166534;
  font-weight: 700;
}

.score-unit {
  margin-top: 6px;
  font-size: 12px;
  color: #78716c;
}

.score-summary-copy {
  min-width: 0;
}

.badge-row,
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-badge,
.chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid #e7e5e4;
  background: #f5f5f4;
  color: #44403c;
  font-size: 12px;
}

.summary-text {
  margin: 12px 0 0;
  font-size: 14px;
  line-height: 1.6;
  color: #44403c;
}

.score-card {
  padding: 16px;
}

.score-card h3 {
  margin: 0 0 12px;
  font-size: 14px;
  color: #292524;
}

.dimension-row + .dimension-row {
  margin-top: 12px;
}

.dimension-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #44403c;
  margin-bottom: 6px;
}

.dimension-bar {
  height: 8px;
  border-radius: 999px;
  background: #f0f0ef;
  overflow: hidden;
}

.dimension-fill {
  height: 100%;
  border-radius: inherit;
  background: #1b7f48;
}

.risk-card + .risk-card {
  margin-top: 10px;
}

.risk-card {
  padding: 12px;
  border: 1px solid #ece7e0;
  border-radius: 8px;
  background: #fafaf9;
}

.risk-title {
  font-size: 13px;
  font-weight: 600;
  color: #1c1917;
}

.risk-reason,
.empty-copy {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.55;
  color: #57534e;
}
.streaming-indicator {
  padding: 8px 16px;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary);
  border-top: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 0 -16px -16px;
}

.dot-pulse {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1b7f48;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.8); }
}
</style>
