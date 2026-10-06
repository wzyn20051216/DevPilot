<script setup lang="ts">
import { Activity, ArrowLeft, Braces, Clock3, FolderGit2, GitBranch, ListChecks, LoaderCircle, Play, Square, Terminal, Wrench } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { getErrorMessage } from '../api/client'
import { reconnectTaskStream, resumeTaskStream } from '../api/stream'
import { cancelTask, getTask, getTaskMetrics } from '../api/tasks'
import TaskResults from '../components/TaskResults.vue'
import type { AgentEvent, TaskDetailResponse } from '../types/agent'

const route = useRoute()
const detail = ref<TaskDetailResponse | null>(null)
const loading = ref(true)
const error = ref('')
const metrics = ref<Record<string, unknown> | null>(null)
let streamController: AbortController | null = null
const sourceLabel = computed(() => {
  const source = detail.value?.source?.source
  if (!source || typeof source !== 'object') return 'Local task'
  const github = source as Record<string, unknown>
  return `${String(github.owner ?? '')}/${String(github.repo ?? '')}#${String(github.issue_number ?? '')}`
})

async function loadTask() {
  streamController?.abort()
  streamController = null
  loading.value = true
  error.value = ''
  try {
    detail.value = await getTask(String(route.params.taskId))
    metrics.value = await getTaskMetrics(String(route.params.taskId))
    if (['running', 'cancelling'].includes(detail.value.task.status)) {
      void observeRunningTask(false)
    }
  } catch (caught) {
    error.value = getErrorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function observeRunningTask(resume: boolean) {
  if (!detail.value) return
  streamController?.abort()
  const controller = new AbortController()
  streamController = controller
  const taskId = detail.value.task.id
  const onEvent = (event: AgentEvent) => {
    if (!detail.value) return
    detail.value.events.push({
      ...event,
      sequence: event.sequence ?? (detail.value.events.at(-1)?.sequence ?? 0) + 1,
      created_at: event.created_at ?? new Date().toISOString(),
    })
    if (event.type === 'cancelled') detail.value.task.status = 'cancelled'
    if (event.type === 'error') detail.value.task.status = 'failed'
  }
  try {
    if (resume) {
      detail.value.task.status = 'running'
      await resumeTaskStream(taskId, onEvent, controller.signal)
    } else {
      const sequence = detail.value.events.at(-1)?.sequence ?? 0
      await reconnectTaskStream(taskId, sequence, onEvent, controller.signal)
    }
    detail.value = await getTask(taskId)
    metrics.value = await getTaskMetrics(taskId)
  } catch (caught) {
    if (!controller.signal.aborted) error.value = getErrorMessage(caught)
  }
}

async function handleCancel() {
  if (!detail.value) return
  try {
    await cancelTask(detail.value.task.id)
    detail.value.task.status = 'cancelling'
  } catch (caught) {
    error.value = getErrorMessage(caught)
  }
}

onMounted(loadTask)
watch(() => route.params.taskId, loadTask)
onBeforeUnmount(() => streamController?.abort())
</script>

<template>
  <main class="task-detail-view">
    <div class="workspace-heading">
      <div>
        <RouterLink class="back-link" to="/"><ArrowLeft :size="15" /> 返回工作台 · Workspace</RouterLink>
        <h1>任务详情 · Task Trace</h1>
      </div>
      <div v-if="detail" class="task-status" :data-status="detail.task.status">
        <span></span>{{ detail.task.status.replaceAll('_', ' ') }}
        <button v-if="detail.task.status === 'running'" class="danger-button" type="button" @click="handleCancel"><Square :size="12" fill="currentColor" /> 停止</button>
        <button v-if="detail.task.status === 'interrupted'" class="primary-button" type="button" @click="observeRunningTask(true)"><Play :size="13" fill="currentColor" /> 恢复执行</button>
      </div>
    </div>
    <div v-if="loading" class="loading-row"><LoaderCircle :size="18" class="spin" /> 加载任务中…</div>
    <div v-else-if="error" class="error-banner" role="alert">{{ error }}</div>
    <template v-else-if="detail">
      <section class="task-summary-band">
        <div><FolderGit2 :size="17" /><span>仓库 · Repository</span><code>{{ detail.task.repo_path }}</code></div>
        <div><Clock3 :size="17" /><span>任务 ID · Task ID</span><code>{{ detail.task.id }}</code></div>
        <div><GitBranch :size="17" /><span>来源 · Source</span><code>{{ sourceLabel }}</code></div>
      </section>
      <section class="detail-question"><p class="eyebrow">需求描述 · Development Request</p><h2>{{ detail.task.question }}</h2></section>
      <section v-if="metrics" class="task-summary-band">
        <div><Activity :size="17" /><span>Token 用量</span><code>{{ metrics.total_tokens ?? 0 }}</code></div>
        <div><Clock3 :size="17" /><span>模型耗时 · LLM</span><code>{{ metrics.llm_seconds ?? 0 }}s</code></div>
        <div><Wrench :size="17" /><span>工具耗时 · Tools</span><code>{{ metrics.tool_seconds ?? 0 }}s</code></div>
      </section>
      <section class="detail-plan">
        <header class="column-header"><ListChecks :size="17" /><h2>已批准计划 · Approved plan</h2><span class="column-count">{{ detail.task.plan.length }}</span></header>
        <div class="detail-plan-grid">
          <article v-for="step in detail.task.plan" :key="step.id"><span>{{ String(step.id).padStart(2, '0') }}</span><div><strong>{{ step.title }}</strong><p>{{ step.description }}</p></div></article>
        </div>
      </section>
      <div class="detail-grid">
        <section class="detail-section">
          <header class="column-header"><Terminal :size="17" /><h2>持久化事件 · Events</h2></header>
          <div class="detail-list">
            <article v-for="event in detail.events" :key="event.sequence" class="trace-event">
              <div class="trace-meta"><span class="agent-name">{{ event.agent }}</span><span>{{ event.type }}</span><span>#{{ event.sequence }}</span></div>
              <p>{{ event.message || 'Event received' }}</p>
            </article>
            <div v-if="!detail.events.length" class="empty-state">暂无持久化事件</div>
          </div>
        </section>
        <section class="detail-section">
          <header class="column-header"><Wrench :size="17" /><h2>工具调用 · Tool calls</h2></header>
          <div class="detail-list">
            <article v-for="(call, index) in detail.tool_calls" :key="index" class="tool-call-row">
              <div><Braces :size="15" /><strong>{{ call.tool }}</strong><span>{{ call.agent }}</span></div>
              <pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre><p>{{ call.result_preview }}</p><small>{{ call.duration_seconds.toFixed(3) }}s · {{ call.succeeded ? 'ok' : 'failed' }}</small>
            </article>
            <div v-if="!detail.tool_calls.length" class="empty-state">暂无工具调用记录</div>
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
