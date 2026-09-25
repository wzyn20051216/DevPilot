export interface EvaluationSummary {
  runs: number
  success_rate: number
  test_pass_rate: number
  avg_tool_calls: number
  avg_iterations: number
  avg_repair_rounds: number
  avg_elapsed_seconds: number
  avg_total_tokens: number
}

export type EvaluationSummaryResponse = Record<string, EvaluationSummary>
