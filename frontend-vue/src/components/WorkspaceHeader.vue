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
  </header>
</template>

<script setup>
defineProps({
  view: {
    type: String,
    default: 'main'
  }
})
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

</style>
