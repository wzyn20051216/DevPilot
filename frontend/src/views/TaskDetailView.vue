<script setup lang="ts">
import { ArrowLeft, Braces, Clock3, FolderGit2, GitBranch, ListChecks, LoaderCircle, Terminal, Wrench } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getErrorMessage } from '../api/client'
import { getTask } from '../api/tasks'
import TaskResults from '../components/TaskResults.vue'
import type { TaskDetailResponse } from '../types/agent'

const route = useRoute()
const detail = ref<TaskDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')
const sourceLabel = computed(() => {
  const source = detail.value?.source?.source
  if (!source || typeof source !== 'object') return 'Local task'
  const github = source as Record<string, unknown>
  return `${String(github.owner ?? '')}/${String(github.repo ?? '')}#${String(github.issue_number ?? '')}`
})

async function loadTask() {
  loading.value = true
  error.value = ''
  try {
    detail.value = await getTask(String(route.params.taskId))
  } catch (caught) {
    error.value = getErrorMessage(caught)
  } finally {
    loading.value = false
  }
}

onMounted(loadTask)
watch(() => route.params.taskId, loadTask)
</script>

<template>
  <main class="task-detail-view">
    <div class="workspace-heading">
      <div><RouterLink class="back-link" to="/"><ArrowLeft :size="15" /> Workspace</RouterLink><h1>Task Trace</h1></div>
      <div v-if="detail" class="task-status" :data-status="detail.task.status"><span></span>{{ detail.task.status.replaceAll('_', ' ') }}</div>
    </div>
    <div v-if="loading" class="loading-row"><LoaderCircle :size="18" class="spin" /> Loading task</div>
    <div v-else-if="error" class="error-banner" role="alert">{{ error }}</div>
    <template v-else-if="detail">
      <section class="task-summary-band">
        <div><FolderGit2 :size="17" /><span>Repository</span><code>{{ detail.task.repo_path }}</code></div>
        <div><Clock3 :size="17" /><span>Task ID</span><code>{{ detail.task.id }}</code></div>
        <div><GitBranch :size="17" /><span>Source</span><code>{{ sourceLabel }}</code></div>
      </section>
      <section class="detail-question"><p class="eyebrow">DEVELOPMENT REQUEST</p><h2>{{ detail.task.question }}</h2></section>
      <section class="detail-plan">
        <header class="column-header"><ListChecks :size="17" /><h2>Approved plan</h2><span class="column-count">{{ detail.task.plan.length }}</span></header>
        <div class="detail-plan-grid">
          <article v-for="step in detail.task.plan" :key="step.id"><span>{{ String(step.id).padStart(2, '0') }}</span><div><strong>{{ step.title }}</strong><p>{{ step.description }}</p></div></article>
        </div>
      </section>
      <div class="detail-grid">
        <section class="detail-section">
          <header class="column-header"><Terminal :size="17" /><h2>Persisted events</h2></header>
          <div class="detail-list">
            <article v-for="event in detail.events" :key="event.sequence" class="trace-event">
              <div class="trace-meta"><span class="agent-name">{{ event.agent }}</span><span>{{ event.type }}</span><span>#{{ event.sequence }}</span></div>
              <p>{{ event.message || 'Event received' }}</p>
            </article>
            <div v-if="!detail.events.length" class="empty-state">No persisted events</div>
          </div>
        </section>
        <section class="detail-section">
          <header class="column-header"><Wrench :size="17" /><h2>Tool calls</h2></header>
          <div class="detail-list">
            <article v-for="(call, index) in detail.tool_calls" :key="index" class="tool-call-row">
              <div><Braces :size="15" /><strong>{{ call.tool }}</strong><span>{{ call.agent }}</span></div>
              <pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre><p>{{ call.result_preview }}</p>
            </article>
            <div v-if="!detail.tool_calls.length" class="empty-state">No tool calls recorded</div>
          </div>
        </section>
      </div>
      <TaskResults
        :task-id="detail.task.id"
        :status="detail.task.status"
        :events="detail.events"
        :source="detail.source"
      />
    </template>
  </main>
</template>
