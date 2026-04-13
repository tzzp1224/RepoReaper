<template>
  <div class="tabs-row">
    <div class="tabs-list">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        type="button"
        :class="['tab-btn', { active: activeTab === tab.id }]"
        @click="$emit('change', tab.id)"
      >
        <span class="tab-icon" aria-hidden="true" v-html="tab.icon"></span>
        <span>{{ tab.label }}</span>
      </button>
    </div>

    <button class="paper-align-btn" type="button" @click="$emit('paper-align')">
      <span>Paper Align</span>
      <span class="paper-align-icon" aria-hidden="true" v-html="paperAlignIcon"></span>
    </button>
  </div>
</template>

<script setup>
defineProps({
  activeTab: {
    type: String,
    required: true
  }
})

defineEmits(['change', 'paper-align'])

const tabs = [
  {
    id: 'report',
    label: 'Report',
    icon:
      '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5h5.5L15 7v9.5H6z"/><path d="M11.5 3.5V7H15"/><path d="M8 10h5"/><path d="M8 13h5"/></svg>'
  },
  {
    id: 'issues',
    label: 'Issues Notebook',
    icon:
      '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 4.5h4.8a2 2 0 0 1 1.4.58l.3.3a2 2 0 0 0 1.4.58h3v9H10.6a2.5 2.5 0 0 0-1.77.73l-.23.23-.23-.23A2.5 2.5 0 0 0 6.6 15H4.5z"/><path d="M10 5v10.5"/></svg>'
  },
  {
    id: 'roadmap',
    label: 'Commit Roadmap',
    icon:
      '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="10" r="1.8"/><circle cx="15" cy="6" r="1.8"/><circle cx="15" cy="14" r="1.8"/><path d="M6.8 10h3.4a2 2 0 0 0 1.41-.59L13.2 7.8"/><path d="M10.2 10a2 2 0 0 1 1.41.59l1.59 1.61"/></svg>'
  },
  {
    id: 'score',
    label: 'Score',
    icon:
      '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15.5V9.5"/><path d="M10 15.5V5.5"/><path d="M16 15.5V11.5"/></svg>'
  }
]

const paperAlignIcon =
  '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 13 13 7"/><path d="M8 7h5v5"/></svg>'
</script>

<style scoped>
.tabs-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 8px 0 4px;
  background: #fff;
  border-bottom: 1px solid var(--border-color);
}

.tabs-list {
  display: flex;
  align-items: center;
  gap: 0;
  min-width: 0;
}

.tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: #a8a29e;
  font-size: 14px;
  font-weight: 400;
  padding: 12px 14px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color 0.2s, background 0.2s;
  position: relative;
}

.tab-btn:hover {
  color: #57534e;
  background: #fafaf9;
}

.tab-btn.active {
  color: #1c1917;
  border-bottom-color: #1b7f48;
  font-weight: 500;
  background: transparent;
}

.tab-btn.active .tab-icon {
  color: #1b7f48;
}

.tab-icon {
  display: inline-flex;
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
  color: inherit;
}

.tab-icon :deep(svg) {
  width: 14px;
  height: 14px;
  display: block;
}

.paper-align-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-color);
  background: #f5f5f4;
  color: #57534e;
  font-size: 12px;
  font-weight: 500;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.2s, border-color 0.2s;
}

.paper-align-btn:hover {
  background: #e7e5e4;
  border-color: #d6d3d1;
}

.paper-align-icon {
  display: inline-flex;
  width: 12px;
  height: 12px;
  color: #57534e;
}

.paper-align-icon :deep(svg) {
  width: 12px;
  height: 12px;
  display: block;
}
</style>
