export type AgentRole = 'orchestrator' | 'planner' | 'coder' | 'tester' | 'reviewer'

export type AgentEventType =
  | 'start'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'hand_off'
  | 'plan'
  | 'test_result'
  | 'review'
  | 'final'
  | 'error'

export type TaskStatus =
  | 'idle'
  | 'planning'
  | 'awaiting_approval'
  | 'approved'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AgentEvent {
  type: AgentEventType
  agent: AgentRole
  iteration: number
  message: string
  data: Record<string, unknown>
}

export interface PersistedAgentEvent extends AgentEvent {
  sequence: number
  created_at: string
}

export interface PlanStep {
  id: number
  title: string
  description: string
  status: 'pending' | 'running' | 'completed' | 'failed'
}

export interface TaskPlanResponse {
  task_id: string
  status: TaskStatus
  plan: PlanStep[]
}

export interface DevelopmentTask {
  id: string
  repo_path: string
  question: string
  status: Exclude<TaskStatus, 'idle' | 'planning'>
  plan: PlanStep[]
}

export interface ToolCallRecord {
  agent: AgentRole
  iteration: number
  tool: string
  arguments: Record<string, unknown>
  result_preview: string
  created_at: string
}

export interface TaskDetailResponse {
  task: DevelopmentTask
  source: Record<string, unknown> | null
  events: PersistedAgentEvent[]
  tool_calls: ToolCallRecord[]
}

export interface TestReport {
  passed: boolean
  summary: string
  stdout: string
  stderr: string
}

export interface ReviewReport {
  approved: boolean
  summary: string
  issues: string[]
}

export interface GitHubIssueImportRequest {
  owner: string
  repo: string
  issue_number: number
  local_repo_path: string
}

export interface GitHubIssueImportResponse extends TaskPlanResponse {
  issue_title: string
  issue_url: string
}

export type PublishStatus = 'awaiting_approval' | 'publishing' | 'published' | 'failed'

export interface PublishPreview {
  task_id: string
  owner: string
  repo: string
  base_branch: string
  head_branch: string
  commit_message: string
  pr_title: string
  pr_body: string
  files: string[]
  snapshot_hash: string
  status: PublishStatus
  pr_url: string
}

export interface PublishPreviewResponse {
  preview: PublishPreview
}

export interface PublishResponse extends PublishPreviewResponse {
  result: Record<string, unknown>
}
