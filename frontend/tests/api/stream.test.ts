import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseEventBlock, streamTask } from '../../src/api/stream'
import { authHeaders, authRequired, setApiKey } from '../../src/api/auth'

afterEach(() => {
  setApiKey('')
  authRequired.value = false
  vi.unstubAllGlobals()
})

describe('SSE 认证', () => {
  it('POST SSE 携带当前 Key；清除后不残留请求凭据', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('data: {"type":"final"}\n\n'))
    vi.stubGlobal('fetch', fetchMock)
    setApiKey('test-key')
    await streamTask('task', vi.fn())
    expect(fetchMock.mock.calls[0]?.[1].headers.Authorization).toBe('Bearer test-key')
    setApiKey('')
    expect(authHeaders()).toEqual({})
  })

  it('401 触发输入框且不重放执行请求', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(streamTask('task', vi.fn())).rejects.toThrow('API Key')
    expect(authRequired.value).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

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
