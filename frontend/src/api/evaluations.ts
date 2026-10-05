import type { EvaluationSummaryResponse } from '../types/evaluation'
import { api } from './client'

/** @brief 读取当前发布的最终实验结果。 */
export async function getEvaluationSummary(): Promise<EvaluationSummaryResponse> {
  const response = await api.get<EvaluationSummaryResponse>('/api/evals/summary')
  return response.data
}
