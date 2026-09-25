import type { AgentEvent, ReviewReport, TestReport } from '../types/agent'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readTestReport(value: unknown): TestReport | null {
  if (!isRecord(value) || typeof value.passed !== 'boolean') return null
  return {
    passed: value.passed,
    summary: typeof value.summary === 'string' ? value.summary : '',
    stdout: typeof value.stdout === 'string' ? value.stdout : '',
    stderr: typeof value.stderr === 'string' ? value.stderr : '',
  }
}

function readReviewReport(value: unknown): ReviewReport | null {
  if (!isRecord(value) || typeof value.approved !== 'boolean') return null
  return {
    approved: value.approved,
    summary: typeof value.summary === 'string' ? value.summary : '',
    issues: Array.isArray(value.issues)
      ? value.issues.filter((issue): issue is string => typeof issue === 'string')
      : [],
  }
}

/** @brief 从最终事件或失败事件中恢复 Tester 和 Reviewer 的结构化报告。 */
export function extractExecutionResults(events: AgentEvent[]) {
  let testReport: TestReport | null = null
  let reviewReport: ReviewReport | null = null

  for (const event of events.toReversed()) {
    if (!testReport) testReport = readTestReport(event.data.test_report)
    if (!reviewReport) reviewReport = readReviewReport(event.data.review_report)
    if (testReport && reviewReport) break
  }

  return { testReport, reviewReport }
}
