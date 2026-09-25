<script setup lang="ts">
import { Activity, BarChart3, Code2, GitBranch, PanelLeft } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
const pageTitle = computed(() => {
  if (route.name === 'evaluations') return 'Evaluation Dashboard'
  if (route.name === 'task-detail') return 'Task Trace'
  return 'Development Workspace'
})
</script>

<template>
  <div class="app-shell">
    <aside class="app-rail">
      <RouterLink class="brand-mark" to="/" aria-label="DevPilot 工作台" title="DevPilot">
        <Code2 :size="22" stroke-width="2" />
      </RouterLink>

      <nav class="rail-nav" aria-label="主导航">
        <RouterLink to="/" aria-label="开发工作台" title="开发工作台">
          <Activity :size="20" />
        </RouterLink>
        <RouterLink to="/evaluations" aria-label="评测面板" title="评测面板">
          <BarChart3 :size="20" />
        </RouterLink>
      </nav>

      <a
        class="rail-link"
        href="https://github.com"
        target="_blank"
        rel="noreferrer"
        aria-label="GitHub"
        title="GitHub"
      >
        <GitBranch :size="19" />
      </a>
    </aside>

    <section class="app-frame">
      <header class="topbar">
        <div class="product-name">
          <PanelLeft :size="17" />
          <strong>DevPilot</strong>
          <span>/</span>
          <span>{{ pageTitle }}</span>
        </div>
        <div class="connection-state">
          <i aria-hidden="true"></i>
          Local runtime
        </div>
      </header>

      <RouterView />
    </section>
  </div>
</template>
