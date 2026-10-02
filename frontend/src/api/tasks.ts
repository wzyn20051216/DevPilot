import type {
  GitHubIssueImportRequest,
  GitHubIssueImportResponse,
  PublishPreviewResponse,
  PublishResponse,
  TaskDetailResponse,
  TaskPlanResponse,
} from '../types/agent'
import { api } from './client'

export interface CreatePlanRequest {
  repo_path: string
  question: string
  execution_mode?: 'single_no_rag' | 'single_rag' | 'multi_no_rag' | 'multi_rag'
}

/** @brief 请求后端生成计划，但不执行任何代码修改。 */
export async function createPlan(request: CreatePlanRequest): Promise<TaskPlanResponse> {
  const response = await api.post<TaskPlanResponse>('/api/tasks/plan', request)
  return response.data
}

/** @brief 读取任务主体、持久化事件和工具调用记录。 */
export async function getTask(taskId: string): Promise<TaskDetailResponse> {
  const response = await api.get<TaskDetailResponse>(`/api/tasks/${taskId}`)
  return response.data
}

/** @brief 请求取消正在后台执行的任务。 */
export async function cancelTask(taskId: string): Promise<{ task_id: string; status: string }> {
  const response = await api.post<{ task_id: string; status: string }>(
    `/api/tasks/${taskId}/cancel`,
  )
  return response.data
}

/** @brief 读取任务级 Token、成本和耗时指标。 */
export async function getTaskMetrics(taskId: string): Promise<Record<string, unknown>> {
  const response = await api.get<Record<string, unknown>>(`/api/tasks/${taskId}/metrics`)
  return response.data
}

/** @brief 导入 GitHub Issue，生成与本地任务共用的待审批计划。 */
export async function importGitHubIssue(
  request: GitHubIssueImportRequest,
): Promise<GitHubIssueImportResponse> {
  const response = await api.post<GitHubIssueImportResponse>('/api/github/issues/import', request)
  return response.data
}

/** @brief 获取任务仓库当前未提交的差异，仅执行只读 Git 查询。 */
export async function getTaskDiff(taskId: string): Promise<string> {
  const response = await api.get<{ diff: string }>(`/api/tasks/${taskId}/diff`)
  return response.data.diff
}

/** @brief 生成发布审批单；该请求不会写 GitHub。 */
export async function createPublishPreview(
  taskId: string,
  baseBranch: string,
): Promise<PublishPreviewResponse> {
  const response = await api.post<PublishPreviewResponse>(
    `/api/tasks/${taskId}/publish-preview`,
    { base_branch: baseBranch },
  )
  return response.data
}

/** @brief 在用户确认预览后执行真正的分支推送和 Draft PR 创建。 */
export async function publishTask(taskId: string): Promise<PublishResponse> {
  const response = await api.post<PublishResponse>(`/api/tasks/${taskId}/publish`)
  return response.data
}
