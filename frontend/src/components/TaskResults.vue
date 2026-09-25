<script setup lang="ts">
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileCode2,
  GitPullRequest,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TestTube2,
  X,
  XCircle,
} from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { getErrorMessage } from '../api/client'
import { createPublishPreview, getTaskDiff, publishTask } from '../api/tasks'
import type { AgentEvent, PublishPreview, TaskStatus } from '../types/agent'
import { extractExecutionResults } from '../utils/taskResults'
import DiffViewer from './DiffViewer.vue'

const props = defineProps<{
  taskId: string
  status: TaskStatus | string
  events: AgentEvent[]
  source?: Record<string, unknown> | null
}>()

type ResultTab = 'diff' | 'tests' | 'review' | 'publish'

const activeTab = ref<ResultTab>('diff')
const diff = ref('')
const diffLoading = ref(false)
const error = ref('')
const baseBranch = ref('main')
const preview = ref<PublishPreview | null>(null)
const previewLoading = ref(false)
const publishLoading = ref(false)
const publishConfirmOpen = ref(false)

const results = computed(() => extractExecutionResults(props.events))
const isGitHubTask = computed(() => props.source?.source_type === 'github_issue')
const canPreparePublish = computed(() => props.status === 'completed' && isGitHubTask.value)

/** @brief 重新读取工作区差异，确保显示的是当前磁盘状态。 */
async function loadDiff() {
  if (!props.taskId || diffLoading.value) return
  diffLoading.value = true
  error.value = ''
  try {
    diff.value = await getTaskDiff(props.taskId)
  } catch (caught) {
    error.value = getErrorMessage(caught)
  } finally {
    diffLoading.value = false
  }
}

/** @brief 生成只读发布预览，供用户核对分支、文件和 PR 文案。 */
async function preparePublish() {
  if (!canPreparePublish.value || previewLoading.value) return
  previewLoading.value = true
  error.value = ''
  try {
    preview.value = (await createPublishPreview(props.taskId, baseBranch.value.trim())).preview
  } catch (caught) {
    error.value = getErrorMessage(caught)
  } finally {
    previewLoading.value = false
  }
}

/** @brief 用户二次确认后才执行真正的 GitHub 写操作。 */
async function confirmPublish() {
  if (!preview.value || publishLoading.value) return
  publishLoading.value = true
  error.value = ''
  try {
    preview.value = (await publishTask(props.taskId)).preview
    publishConfirmOpen.value = false
  } catch (caught) {
    error.value = getErrorMessage(caught)
  } finally {
    publishLoading.value = false
  }
}

watch(
  () => [props.taskId, props.status],
  ([taskId, status]) => {
    if (taskId && (status === 'completed' || status === 'failed')) void loadDiff()
  },
  { immediate: true },
)
</script>

<template>
  <section class="results-console" aria-labelledby="results-title">
    <header class="results-header">
      <div><p class="eyebrow">VERIFICATION OUTPUT</p><h2 id="results-title">Execution Results</h2></div>
      <div class="result-tabs" role="tablist" aria-label="任务结果">
        <button type="button" :aria-selected="activeTab === 'diff'" @click="activeTab = 'diff'"><FileCode2 :size="15" />Diff</button>
        <button type="button" :aria-selected="activeTab === 'tests'" @click="activeTab = 'tests'"><TestTube2 :size="15" />Tests</button>
        <button type="button" :aria-selected="activeTab === 'review'" @click="activeTab = 'review'"><ShieldCheck :size="15" />Review</button>
        <button type="button" :aria-selected="activeTab === 'publish'" @click="activeTab = 'publish'"><GitPullRequest :size="15" />Publish</button>
      </div>
    </header>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <div v-if="activeTab === 'diff'" class="result-body">
      <div class="result-toolbar">
        <span>Current working tree</span>
        <button class="icon-button" type="button" title="刷新 Git Diff" @click="loadDiff">
          <RefreshCw :size="15" :class="{ spin: diffLoading }" />
        </button>
      </div>
      <div v-if="diffLoading && !diff" class="loading-row"><LoaderCircle :size="17" class="spin" />Loading diff</div>
      <DiffViewer v-else :diff="diff" />
    </div>

    <div v-else-if="activeTab === 'tests'" class="result-body report-body">
      <article v-if="results.testReport" class="report-summary" :data-success="results.testReport.passed">
        <CheckCircle2 v-if="results.testReport.passed" :size="22" />
        <XCircle v-else :size="22" />
        <div><span>{{ results.testReport.passed ? 'Tests passed' : 'Tests failed' }}</span><p>{{ results.testReport.summary }}</p></div>
      </article>
      <div v-if="results.testReport" class="terminal-output">
        <div><span>stdout</span><pre>{{ results.testReport.stdout || 'No stdout captured.' }}</pre></div>
        <div v-if="results.testReport.stderr"><span>stderr</span><pre>{{ results.testReport.stderr }}</pre></div>
      </div>
      <div v-else class="empty-state compact-empty"><Clock3 :size="20" />No structured test report yet</div>
    </div>

    <div v-else-if="activeTab === 'review'" class="result-body report-body">
      <article v-if="results.reviewReport" class="report-summary" :data-success="results.reviewReport.approved">
        <ShieldCheck :size="22" />
        <div><span>{{ results.reviewReport.approved ? 'Review approved' : 'Changes requested' }}</span><p>{{ results.reviewReport.summary }}</p></div>
      </article>
      <ul v-if="results.reviewReport?.issues.length" class="review-issues">
        <li v-for="issue in results.reviewReport.issues" :key="issue">{{ issue }}</li>
      </ul>
      <div v-else-if="!results.reviewReport" class="empty-state compact-empty"><Clock3 :size="20" />No structured review report yet</div>
    </div>

    <div v-else class="result-body publish-body">
      <template v-if="preview">
        <div class="publish-metadata">
          <div><span>Target</span><code>{{ preview.owner }}/{{ preview.repo }} : {{ preview.base_branch }}</code></div>
          <div><span>Head branch</span><code>{{ preview.head_branch }}</code></div>
          <div><span>Commit</span><code>{{ preview.commit_message }}</code></div>
          <div><span>Files</span><code>{{ preview.files.length }} changed</code></div>
        </div>
        <div class="pr-copy"><span>PR title</span><strong>{{ preview.pr_title }}</strong><span>PR body</span><pre>{{ preview.pr_body }}</pre></div>
        <a v-if="preview.pr_url" class="pr-link" :href="preview.pr_url" target="_blank" rel="noreferrer">Open Pull Request <ExternalLink :size="15" /></a>
        <button v-else class="danger-button" type="button" :disabled="preview.status !== 'awaiting_approval'" @click="publishConfirmOpen = true">
          <GitPullRequest :size="16" />Approve GitHub Publish
        </button>
      </template>
      <template v-else>
        <p class="publish-notice"><AlertTriangle :size="18" />发布只对 GitHub Issue 导入且已经完成的任务开放；生成预览不会写入 GitHub。</p>
        <label for="base-branch">Base branch</label>
        <input id="base-branch" v-model="baseBranch" :disabled="!canPreparePublish" />
        <button class="primary-button prepare-publish" type="button" :disabled="!canPreparePublish || previewLoading" @click="preparePublish">
          <LoaderCircle v-if="previewLoading" :size="16" class="spin" /><GitPullRequest v-else :size="16" />Prepare Pull Request
        </button>
      </template>
    </div>
  </section>

  <Teleport to="body">
    <div v-if="publishConfirmOpen" class="modal-backdrop" role="presentation" @click.self="publishConfirmOpen = false">
      <section class="publish-confirm" role="alertdialog" aria-modal="true" aria-labelledby="publish-confirm-title">
        <header><AlertTriangle :size="22" /><div><p class="eyebrow">IRREVERSIBLE REMOTE ACTION</p><h2 id="publish-confirm-title">Publish to GitHub?</h2></div><button class="icon-button" type="button" title="关闭" @click="publishConfirmOpen = false"><X :size="17" /></button></header>
        <p>将创建远程分支、推送 {{ preview?.files.length }} 个文件并创建 Draft PR。系统会在执行前再次校验本地文件快照。</p>
        <footer><button class="secondary-button" type="button" @click="publishConfirmOpen = false">Cancel</button><button class="danger-button" type="button" :disabled="publishLoading" @click="confirmPublish"><LoaderCircle v-if="publishLoading" :size="16" class="spin" /><GitPullRequest v-else :size="16" />Confirm Publish</button></footer>
      </section>
    </div>
  </Teleport>
</template>
