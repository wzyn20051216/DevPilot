<script setup lang="ts">
import {
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  FolderGit2,
  GitBranch,
  Import,
  ListChecks,
  LoaderCircle,
  Play,
  RotateCcw,
  Sparkles,
  Terminal,
} from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'

import AgentPipeline from '../components/AgentPipeline.vue'
import ApprovalDialog from '../components/ApprovalDialog.vue'
import TaskResults from '../components/TaskResults.vue'
import { getErrorMessage } from '../api/client'
import { streamTask } from '../api/stream'
import { createPlan, importGitHubIssue } from '../api/tasks'
import { useTaskStore } from '../stores/task'
import type { TaskPlanResponse } from '../types/agent'

const store = useTaskStore()
const inputMode = ref<'local' | 'github'>('local')
const repoPath = ref(
  import.meta.env.VITE_DEFAULT_REPO_PATH ?? 'E:\\desktop\\devpilot-test-repo',
)
const question = ref('修复 add 函数中的 bug 并运行测试。')
const githubOwner = ref('')
const githubRepo = ref('devpilot-test-repo')
const issueNumber = ref(1)
const issueUrl = ref('')
const planning = ref(false)
const approvalOpen = ref(false)
const traceElement = ref<HTMLElement | null>(null)
const canPlan = computed(() => {
  if (planning.value || !repoPath.value.trim()) return false
  if (inputMode.value === 'local') return Boolean(question.value.trim())
  return Boolean(githubOwner.value.trim() && githubRepo.value.trim() && issueNumber.value > 0)
})

/** @brief 调用 Planner 创建待人工审批的任务计划。 */
async function handlePlan() {
  if (!canPlan.value) return
  store.reset()
  issueUrl.value = ''
  approvalOpen.value = false
  planning.value = true
  store.status = 'planning'
  try {
    let result: TaskPlanResponse
    if (inputMode.value === 'local') {
      result = await createPlan({
        repo_path: repoPath.value.trim(),
        question: question.value.trim(),
      })
    } else {
      const imported = await importGitHubIssue({
          owner: githubOwner.value.trim(),
          repo: githubRepo.value.trim(),
          issue_number: issueNumber.value,
          local_repo_path: repoPath.value.trim(),
        })

      issueUrl.value = imported.issue_url
      store.source = {
        source_type: 'github_issue',
        source: {
          owner: githubOwner.value.trim(),
          repo: githubRepo.value.trim(),
          issue_number: issueNumber.value,
          title: imported.issue_title,
          url: imported.issue_url,
        },
      }
      result = imported
    }

    store.taskId = result.task_id
    store.plan = result.plan
    store.status = result.status
    approvalOpen.value = true
  } catch (error) {
    store.error = getErrorMessage(error)
    store.status = 'failed'
  } finally {
    planning.value = false
  }
}

/** @brief 批准计划并持续消费 Coder、Tester、Reviewer 的 SSE 事件。 */
async function handleApprove() {
  if (!store.taskId || store.running) return
  approvalOpen.value = false
  store.running = true
  store.status = 'running'
  store.error = ''
  try {
    await streamTask(
      store.taskId,
      async (event) => {
        store.addEvent(event)
        await nextTick()
        traceElement.value?.scrollTo({ top: traceElement.value.scrollHeight, behavior: 'smooth' })
      },
    )
    if (!store.hasExecutionError) {
      store.status = 'completed'
    }
  } catch (error) {
    store.error = getErrorMessage(error)
    store.status = 'failed'
  } finally {
    store.running = false
  }
}

function eventLabel(type: string) {
  return type.replaceAll('_', ' ')
}
</script>

<template>
  <main class="workspace-view">
    <div class="workspace-heading">
      <div>
        <p class="eyebrow">AGENT CONTROL PLANE</p>
        <h1>Development Workspace</h1>
      </div>
      <div class="task-status" :data-status="store.status">
        <span></span>{{ store.status.replaceAll('_', ' ') }}
      </div>
    </div>

    <AgentPipeline :current-agent="store.currentAgent" :events="store.events" :status="store.status" />

    <div class="workspace-grid">
      <section class="workspace-column task-column" aria-labelledby="task-title">
        <header class="column-header">
          <FolderGit2 :size="17" />
          <h2 id="task-title">Task Input</h2>
        </header>

        <div class="input-mode-switch" role="tablist" aria-label="任务来源">
          <button type="button" :aria-selected="inputMode === 'local'" @click="inputMode = 'local'">
            <Terminal :size="14" />Local task
          </button>
          <button type="button" :aria-selected="inputMode === 'github'" @click="inputMode = 'github'">
            <GitBranch :size="14" />GitHub issue
          </button>
        </div>

        <form class="task-form" @submit.prevent="handlePlan">
          <label for="repo-path">Repository</label>
          <div class="input-shell">
            <Terminal :size="15" />
            <input id="repo-path" v-model="repoPath" autocomplete="off" spellcheck="false" />
          </div>

          <template v-if="inputMode === 'local'">
            <label for="question">Development Task</label>
            <textarea id="question" v-model="question" rows="9" />
          </template>

          <template v-else>
            <div class="github-fields">
              <div><label for="github-owner">Owner</label><input id="github-owner" v-model="githubOwner" autocomplete="off" /></div>
              <div><label for="github-repo">Repository</label><input id="github-repo" v-model="githubRepo" autocomplete="off" /></div>
            </div>
            <label for="issue-number">Issue number</label>
            <input id="issue-number" v-model.number="issueNumber" min="1" type="number" />
          </template>

          <button class="primary-button" type="submit" :disabled="!canPlan">
            <LoaderCircle v-if="planning" :size="16" class="spin" />
            <Import v-else-if="inputMode === 'github'" :size="16" />
            <Sparkles v-else :size="16" />
            {{ planning ? 'Planning' : inputMode === 'github' ? 'Import Issue' : 'Generate Plan' }}
          </button>
        </form>

        <div v-if="store.taskId" class="task-reference">
          <span>Task ID</span>
          <code>{{ store.taskId }}</code>
          <RouterLink :to="`/tasks/${store.taskId}`" title="打开任务详情">
            <ArrowUpRight :size="16" />
          </RouterLink>
          <a v-if="issueUrl" class="issue-source-link" :href="issueUrl" target="_blank" rel="noreferrer" title="打开来源 Issue">
            <GitBranch :size="15" />
          </a>
        </div>
      </section>

      <section class="workspace-column plan-column" aria-labelledby="plan-title">
        <header class="column-header">
          <ListChecks :size="17" />
          <h2 id="plan-title">Development Plan</h2>
          <span class="column-count">{{ store.plan.length }}</span>
        </header>

        <div v-if="store.plan.length" class="plan-list">
          <article v-for="step in store.plan" :key="step.id" class="plan-step">
            <span class="step-index">{{ String(step.id).padStart(2, '0') }}</span>
            <div>
              <strong>{{ step.title }}</strong>
              <p>{{ step.description }}</p>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">
          <Clock3 :size="22" />
          <span>No plan generated</span>
        </div>

        <div v-if="store.status === 'awaiting_approval'" class="approval-bar">
          <div>
            <CheckCircle2 :size="17" />
            <span>Ready for approval</span>
          </div>
          <button class="execute-button" type="button" @click="approvalOpen = true">
            <Play :size="15" fill="currentColor" />
            Review & Execute
          </button>
        </div>

      </section>

      <section class="workspace-column trace-column" aria-labelledby="trace-title">
        <header class="column-header">
          <Terminal :size="17" />
          <h2 id="trace-title">Agent Trace</h2>
          <span class="live-indicator" :class="{ active: store.running }"></span>
        </header>

        <div ref="traceElement" class="trace-stream" aria-live="polite">
          <article v-for="(event, index) in store.events" :key="index" class="trace-event">
            <div class="trace-meta">
              <span class="agent-name">{{ event.agent }}</span>
              <span>{{ eventLabel(event.type) }}</span>
              <span v-if="event.iteration">#{{ event.iteration }}</span>
            </div>
            <p>{{ event.message || 'Event received' }}</p>
          </article>
          <div v-if="!store.events.length" class="empty-state trace-empty">
            <RotateCcw :size="21" />
            <span>Trace waiting</span>
          </div>
        </div>

        <div v-if="store.error" class="error-banner" role="alert">
          {{ store.error }}
        </div>
      </section>
    </div>

    <TaskResults
      v-if="store.taskId"
      :task-id="store.taskId"
      :status="store.status"
      :events="store.events"
      :source="store.source"
    />

    <ApprovalDialog
      :open="approvalOpen"
      :plan="store.plan"
      :running="store.running"
      @close="approvalOpen = false"
      @confirm="handleApprove"
    />
  </main>
</template>
