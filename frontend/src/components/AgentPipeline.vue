<script setup lang="ts">
import { Check, Circle, LoaderCircle, X } from '@lucide/vue'
import { computed } from 'vue'

import type { AgentEvent, AgentRole, TaskStatus } from '../types/agent'

const props = defineProps<{
  currentAgent: AgentRole | null
  events: AgentEvent[]
  status: TaskStatus
}>()

const stages: { id: AgentRole; label: string }[] = [
  { id: 'planner', label: 'Planner' },
  { id: 'coder', label: 'Coder' },
  { id: 'tester', label: 'Tester' },
  { id: 'reviewer', label: 'Reviewer' },
]

const furthestStage = computed(() => {
  return props.events.reduce((furthest, event) => {
    const index = stages.findIndex((stage) => stage.id === event.agent)
    return Math.max(furthest, index)
  }, -1)
})

function stageState(stage: AgentRole, index: number) {
  if (props.status === 'failed' && props.currentAgent === stage) return 'failed'
  if (props.status === 'completed' || index < furthestStage.value) return 'completed'
  if (props.status === 'running' && props.currentAgent === stage) return 'running'
  if (stage === 'planner') {
    if (props.status === 'planning') return 'running'
    if (['awaiting_approval', 'running'].includes(props.status)) return 'completed'
  }
  return 'pending'
}
</script>

<template>
  <ol class="agent-pipeline" aria-label="Agent 执行流水线">
    <li v-for="(stage, index) in stages" :key="stage.id" :data-state="stageState(stage.id, index)">
      <span class="stage-icon">
        <LoaderCircle v-if="stageState(stage.id, index) === 'running'" :size="15" class="spin" />
        <Check v-else-if="stageState(stage.id, index) === 'completed'" :size="15" />
        <X v-else-if="stageState(stage.id, index) === 'failed'" :size="15" />
        <Circle v-else :size="11" />
      </span>
      <span>{{ stage.label }}</span>
    </li>
  </ol>
</template>
