import type { EvaluationSummaryResponse } from '../types/evaluation'
import { api } from './client'

/** @brief 读取数据库中全部实验结果的 variant 聚合指标。 */
export async function getEvaluationSummary(): Promise<EvaluationSummaryResponse> {
  const response = await api.get<EvaluationSummaryResponse>('/api/evals/summary')
  return response.data
}
