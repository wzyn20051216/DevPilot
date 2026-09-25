import type { AgentEvent } from '../types/agent'
import { API_BASE_URL } from './client'

/** @brief 将一个完整 SSE block 转成事件，忽略注释和空 data。 */
export function parseEventBlock(block: string): AgentEvent | null {
  const payload = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')

  if (!payload) {
    return null
  }
  try {
    return JSON.parse(payload) as AgentEvent
  } catch {
    return null
  }
}

/** @brief 通过 fetch 消费 POST SSE；EventSource 只支持 GET，不能用于该接口。 */
export async function streamTask(
  taskId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/tasks/${taskId}/execute`, {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    signal,
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`)
  }
  if (!response.body) {
    throw new Error('服务器没有返回事件流')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const consumeBlocks = (flush = false) => {
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = flush ? '' : (blocks.pop() ?? '')
    for (const block of blocks) {
      const event = parseEventBlock(block)
      if (event) {
        onEvent(event)
      }
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) {
      buffer += decoder.decode()
      if (buffer.trim()) {
        const event = parseEventBlock(buffer)
        if (event) {
          onEvent(event)
        }
      }
      break
    }
    buffer += decoder.decode(value, { stream: true })
    consumeBlocks()
  }
}
