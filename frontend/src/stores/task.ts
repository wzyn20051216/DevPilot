import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { AgentEvent, PlanStep, TaskStatus } from '../types/agent'

export const useTaskStore = defineStore('task', () => {
  const taskId = ref('')
  const status = ref<TaskStatus>('idle')
  const plan = ref<PlanStep[]>([])
  const events = ref<AgentEvent[]>([])
  const source = ref<Record<string, unknown> | null>(null)
  const running = ref(false)
  const error = ref('')

  const currentAgent = computed(() => events.value.at(-1)?.agent ?? null)
  const hasExecutionError = computed(() => events.value.some((event) => event.type === 'error'))

  /** @brief 清空上一次任务，恢复工作台初始状态。 */
  function reset() {
    taskId.value = ''
    status.value = 'idle'
    plan.value = []
    events.value = []
    source.value = null
    running.value = false
    error.value = ''
  }

  /** @brief 追加一条实时事件，并在 error 事件到达时同步失败状态。 */
  function addEvent(event: AgentEvent) {
    events.value.push(event)
    if (event.type === 'error') {
      status.value = 'failed'
      error.value = event.message
    }
  }

  return {
    taskId,
    status,
    plan,
    events,
    source,
    running,
    error,
    currentAgent,
    hasExecutionError,
    reset,
    addEvent,
  }
})
