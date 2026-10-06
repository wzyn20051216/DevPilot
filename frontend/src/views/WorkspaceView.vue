<script setup lang="ts">
import {
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  FolderGit2,
  GitBranch,
  Import,
  Info,
  ListChecks,
  LoaderCircle,
  Play,
  RotateCcw,
  Sparkles,
  Square,
  Terminal,
} from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'

import AgentPipeline from '../components/AgentPipeline.vue'
import ApprovalDialog from '../components/ApprovalDialog.vue'
import TaskResults from '../components/TaskResults.vue'
import { getErrorMessage } from '../api/client'
import { reconnectTaskStream, streamTask } from '../api/stream'
import { cancelTask, createPlan, getTask, importGitHubIssue } from '../api/tasks'
import { useTaskStore } from '../stores/task'
import type { TaskPlanResponse } from '../types/agent'

const store = useTaskStore()
const inputMode = ref<'local' | 'github'>('local')
const repoPath = ref(import.meta.env.VITE_DEFAULT_REPO_PATH ?? '')
const question = ref('')
const executionMode = ref<'multi_no_rag' | 'multi_rag'>('multi_rag')
const githubOwner = ref('')
const githubRepo = ref('')
const issueNumber = ref(1)
const issueUrl = ref('')
const planning = ref(false)
const approvalOpen = ref(false)
const traceElement = ref<HTMLElement | null>(null)

/** @brief 一个既能点也能读的路径示例，帮新用户快速上手。 */
const repoExample = '/workspace/example-project'
const taskExamples = [
  '为订单接口补充分页参数校验，并补上边界用例的单元测试',
  '修复登录在并发请求下偶发 401 的问题，说明根因与修复',
]
const canPlan = computed(() => {
  if (planning.value || !repoPath.value.trim()) return false
  if (inputMode.value === 'local') return Boolean(question.value.trim())
  return Boolean(githubOwner.value.trim() && githubRepo.value.trim() && issueNumber.value > 0)
})

/** @brief 点击示例芯片，把示例值填进对应输入框。 */
function applyRepoExample() {
  repoPath.value = repoExample
}
function applyTaskExample(text: string) {
  question.value = text
}

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
        execution_mode: executionMode.value,
      })
    } else {
      const imported = await importGitHubIssue({
        owner: githubOwner.value.trim(),
        repo: githubRepo.value.trim(),
        issue_number: issueNumber.value,
        local_repo_path: repoPath.value.trim(),
        execution_mode: executionMode.value,
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
    let firstConnection = true
    let reconnects = 0
    const onEvent = async (event: Parameters<typeof store.addEvent>[0]) => {
      store.addEvent(event)
      await nextTick()
      traceElement.value?.scrollTo({ top: traceElement.value.scrollHeight, behavior: 'smooth' })
    }
    while (true) {
      try {
        const lastSequence = store.events.at(-1)?.sequence ?? 0
        if (firstConnection) {
          firstConnection = false
          await streamTask(store.taskId, onEvent)
        } else {
          await reconnectTaskStream(store.taskId, lastSequence, onEvent)
        }
        break
      } catch (error) {
        const detail = await getTask(store.taskId)
        store.status = detail.task.status
        if (!['running', 'cancelling'].includes(detail.task.status) || reconnects >= 3) {
          throw error
        }
        reconnects += 1
        store.events = detail.events
        await new Promise((resolve) => window.setTimeout(resolve, 500))
      }
    }
    const detail = await getTask(store.taskId)
    store.status = detail.task.status
  } catch (error) {
    store.error = getErrorMessage(error)
    store.status = 'failed'
  } finally {
    store.running = false
  }
}

/** @brief 请求后台任务在下一个安全点停止。 */
async function handleCancel() {
  if (!store.taskId || store.status !== 'running') return
  try {
    await cancelTask(store.taskId)
    store.status = 'cancelling'
  } catch (error) {
    store.error = getErrorMessage(error)
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
        <p class="eyebrow">Development Workspace · 研发工作台</p>
        <h1>让 Agent 替你改代码</h1>
        <p class="heading-sub">
          填写仓库路径与需求，先生成计划，人工确认后再执行；全过程实时可追踪。
        </p>
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
          <span class="column-step">1</span>
        </header>

        <div class="input-mode-switch" role="tablist" aria-label="任务来源">
          <button type="button" :aria-selected="inputMode === 'local'" @click="inputMode = 'local'">
            <Terminal :size="14" />本地需求
          </button>
          <button type="button" :aria-selected="inputMode === 'github'" @click="inputMode = 'github'">
            <GitBranch :size="14" />GitHub Issue
          </button>
        </div>

        <form class="task-form" @submit.prevent="handlePlan">
          <label for="repo-path">仓库路径 · Repository path</label>
          <div class="input-shell">
            <Terminal :size="15" />
            <input
              id="repo-path"
              v-model="repoPath"
              placeholder="例如 /workspace/example-project"
              autocomplete="off"
              spellcheck="false"
            />
          </div>
          <div class="example-row">
            <span class="example-chip" @click="applyRepoExample">
              试用示例：{{ repoExample }}
            </span>
          </div>

          <template v-if="inputMode === 'local'">
            <label for="question">需求描述 · What should we build or fix?</label>
            <textarea
              id="question"
              v-model="question"
              rows="7"
              placeholder="描述目标行为、当前现象与验收条件。越具体，计划越准确。"
            />
            <div class="example-row">
              <span
                v-for="(example, index) in taskExamples"
                :key="index"
                class="example-chip"
                @click="applyTaskExample(example)"
              >
                {{ example }}
              </span>
            </div>
          </template>

          <template v-else>
            <div class="github-fields">
              <div><label for="github-owner">Owner</label><input id="github-owner" v-model="githubOwner" autocomplete="off" placeholder="例如 octocat" /></div>
              <div><label for="github-repo">Repository</label><input id="github-repo" v-model="githubRepo" autocomplete="off" placeholder="例如 hello-world" /></div>
            </div>
            <label for="issue-number">Issue number</label>
            <input id="issue-number" v-model.number="issueNumber" min="1" type="number" placeholder="例如 42" />
          </template>

          <label for="execution-mode">执行策略 · Execution strategy</label>
          <select id="execution-mode" v-model="executionMode">
            <option value="multi_rag">多 Agent · 混合检索（推荐）</option>
            <option value="multi_no_rag">多 Agent · 不使用检索</option>
          </select>
          <p class="field-hint">
            <Info :size="13" />
            <span>默认使用多 Agent 协作并提供混合检索工具，也可选择不使用检索。</span>
          </p>

          <button class="primary-button" type="submit" :disabled="!canPlan">
            <LoaderCircle v-if="planning" :size="16" class="spin" />
            <Import v-else-if="inputMode === 'github'" :size="16" />
            <Sparkles v-else :size="16" />
            {{ planning ? '生成中…' : inputMode === 'github' ? '导入 Issue 并生成计划' : '生成执行计划' }}
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
          <span class="column-step">2</span>
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
          <span>生成计划后，这里会显示待确认的步骤</span>
        </div>

        <div v-if="store.status === 'awaiting_approval'" class="approval-bar">
          <div>
            <CheckCircle2 :size="17" />
            <span>计划已就绪，等待你确认</span>
          </div>
          <button class="execute-button" type="button" @click="approvalOpen = true">
            <Play :size="15" fill="currentColor" />
            查看并执行
          </button>
        </div>
      </section>

      <section class="workspace-column trace-column" aria-labelledby="trace-title">
        <header class="column-header">
          <Terminal :size="17" />
          <h2 id="trace-title">Agent Trace</h2>
          <span class="column-step">3</span>
          <span class="live-indicator" :class="{ active: store.running }"></span>
          <button
            v-if="store.status === 'running'"
            class="danger-button"
            type="button"
            @click="handleCancel"
          >
            <Square :size="13" fill="currentColor" /> 停止
          </button>
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
            <span>执行后，Agent 的每一步都会实时出现在这里</span>
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

<style scoped>
.heading-sub {
  margin: 12px 0 0;
  max-width: 620px;
  color: var(--muted);
  font-size: 13.5px;
  line-height: 1.6;
}
.column-step {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--surface-sunken);
  color: var(--muted);
  font-family: var(--mono);
  font-size: 10px;
}
</style>
