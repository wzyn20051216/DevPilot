import { describe, expect, it } from 'vitest'

import { parseEventBlock } from './stream'

describe('parseEventBlock', () => {
  it('解析 FastAPI 发出的单行 AgentEvent', () => {
    const event = parseEventBlock(
      'data: {"type":"thinking","agent":"coder","iteration":2,"message":"分析代码","data":{}}',
    )

    expect(event).toMatchObject({ type: 'thinking', agent: 'coder', iteration: 2 })
  })

  it('兼容 CRLF、多行 data 和 SSE 注释', () => {
    const event = parseEventBlock(
      ': keep-alive\r\ndata: {"type":"final","agent":"reviewer",\r\ndata: "iteration":1,"message":"ok","data":{}}',
    )

    expect(event?.message).toBe('ok')
  })

  it('忽略空块和畸形 JSON，不中断后续事件流', () => {
    expect(parseEventBlock(': ping')).toBeNull()
    expect(parseEventBlock('data: {broken')).toBeNull()
  })
})
