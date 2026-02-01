<template>
  <header class="header">
    <h1>
      <span>🧠</span> 
      GitHub RAG Agent 
      <span class="badge">v6.0 Vue</span>
    </h1>
    <div class="session-info">
      Session: <span>{{ store.shortSessionId }}</span>
    </div>
  </header>
</template>

<script setup>
import { useAppStore } from '../stores/app'

const store = useAppStore()
</script>

<style scoped>
.header {
  height: 60px;
  background: var(--panel-bg);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}

.header h1 {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-primary);
}

.badge {
  background: var(--primary-color);
  color: white;
  font-size: 0.8rem;
  padding: 4px 10px;
  border-radius: 20px;
  font-weight: 500;
}

.session-info {
  font-size: 14px;
  color: var(--text-secondary);
}
</style>
