<script setup lang="ts">
import { AlertTriangle, Check, X } from '@lucide/vue'

import type { PlanStep } from '../types/agent'

defineProps<{
  open: boolean
  plan: PlanStep[]
  running?: boolean
}>()

defineEmits<{
  close: []
  confirm: []
}>()
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" role="presentation" @click.self="$emit('close')">
      <section class="approval-dialog" role="dialog" aria-modal="true" aria-labelledby="approval-title">
        <header>
          <div>
            <p class="eyebrow">HUMAN APPROVAL GATE</p>
            <h2 id="approval-title">Review execution plan</h2>
          </div>
          <button class="icon-button" type="button" title="关闭审批" @click="$emit('close')">
            <X :size="17" />
          </button>
        </header>

        <p class="approval-warning">
          <AlertTriangle :size="17" />
          批准后 Agent 将获得修改本地仓库和运行测试的权限。
        </p>

        <ol class="approval-plan">
          <li v-for="step in plan" :key="step.id">
            <span>{{ String(step.id).padStart(2, '0') }}</span>
            <div><strong>{{ step.title }}</strong><p>{{ step.description }}</p></div>
          </li>
        </ol>

        <footer>
          <button class="secondary-button" type="button" @click="$emit('close')">暂不执行</button>
          <button class="primary-button" type="button" :disabled="running" @click="$emit('confirm')">
            <Check :size="16" />
            {{ running ? 'Starting' : 'Approve & Execute' }}
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
