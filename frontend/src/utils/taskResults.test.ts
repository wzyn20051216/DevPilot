import { describe, expect, it } from 'vitest'

import type { AgentEvent } from '../types/agent'
import { extractExecutionResults } from './taskResults'

describe('extractExecutionResults', () => {
  it('从最终事件提取测试和评审报告', () => {
    const events: AgentEvent[] = [{
      type: 'final',
      agent: 'orchestrator',
      iteration: 0,
      message: '完成',
      data: {
        test_report: { passed: true, summary: '1 passed', stdout: 'ok', stderr: '' },
        review_report: { approved: true, summary: 'approved', issues: [] },
      },
    }]

    expect(extractExecutionResults(events)).toEqual({
      testReport: { passed: true, summary: '1 passed', stdout: 'ok', stderr: '' },
      reviewReport: { approved: true, summary: 'approved', issues: [] },
    })
  })

  it('忽略不完整的非结构化事件数据', () => {
    const events: AgentEvent[] = [{
      type: 'error',
      agent: 'orchestrator',
      iteration: 0,
      message: '失败',
      data: { test_report: 'invalid' },
    }]

    expect(extractExecutionResults(events)).toEqual({ testReport: null, reviewReport: null })
  })
})
