<template>
  <header class="workspace-header">
    <div class="brand">
      <div class="logo" aria-hidden="true">
        <span class="logo-core"></span>
      </div>
      <div class="brand-copy">
        <div class="brand-title">
          RepoReaper
          <span v-if="view === 'paper-align'" class="view-badge">Paper Align</span>
        </div>
        <div class="brand-subtitle">GitHub repository analysis workspace</div>
      </div>
    </div>

    <div class="header-right">
      <span class="course-badge">NUS CS5260 Project (Spring 2026)</span>
      <div class="session-pill">
        <span class="session-dot"></span>
        <span class="session-label">Session</span>
        <span class="session-value">{{ store.shortSessionId }}</span>
      </div>
    </div>
  </header>
</template>

<script setup>
import { useAppStore } from '../stores/app'

defineProps({
  view: {
    type: String,
    default: 'main'
  }
})

const store = useAppStore()
</script>

<style scoped>
.workspace-header {
  height: 56px;
  border-bottom: 1px solid var(--border-color);
  background: var(--shell-bg);
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-shrink: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.logo {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(180deg, #2f9f63, #1b7f48);
  display: grid;
  place-items: center;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.25);
}

.logo-core {
  width: 11px;
  height: 11px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
}

.brand-copy {
  min-width: 0;
}

.brand-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
}

.brand-subtitle {
  font-size: 12px;
  color: #78716c;
}

.view-badge {
  padding: 2px 8px;
  border: 1px solid #b8e3c8;
  background: #edf9f0;
  color: #166534;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.course-badge {
  padding: 5px 12px;
  border: 1px solid #c7d2fe;
  background: #eef2ff;
  color: #4338ca;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.session-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  background: #f5f5f4;
  border-radius: 8px;
  flex-shrink: 0;
  font-size: 12px;
}

.session-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #10b981;
}

.session-label {
  color: #78716c;
}

.session-value {
  color: #292524;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-weight: 600;
}
</style>
