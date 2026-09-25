<script setup lang="ts">
import { Activity, Clock3, Coins, Gauge, RefreshCw, TestTube2, Wrench } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { getErrorMessage } from '../api/client'
import { getEvaluationSummary } from '../api/evaluations'
import EvaluationChart from '../components/EvaluationChart.vue'
import type { EvaluationSummaryResponse } from '../types/evaluation'

const summary = ref<EvaluationSummaryResponse>({})
const loading = ref(true)
const error = ref('')
const variantOrder = ['single_no_rag', 'single_rag', 'multi_no_rag', 'multi_rag']

const rows = computed(() => Object.entries(summary.value).sort(
  ([first], [second]) => variantOrder.indexOf(first) - variantOrder.indexOf(second),
))
const totalRuns = computed(() => rows.value.reduce((total, [, value]) => total + value.runs, 0))
const bestVariant = computed(() => (
  rows.value.toSorted(([, a], [, b]) => b.success_rate - a.success_rate)[0]?.[0] ?? 'n/a'
))

async function loadSummary() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await getEvaluationSummary()
  } catch (caught) {
    error.value = getErrorMessage(caught)
  } finally {
    loading.value = false
  }
}

onMounted(loadSummary)
</script>

<template>
  <main class="evaluation-view">
    <div class="workspace-heading evaluation-heading">
      <div><p class="eyebrow">EXPERIMENT OBSERVATORY</p><h1>Evaluation Dashboard</h1></div>
      <button class="icon-button" type="button" title="刷新评测结果" @click="loadSummary">
        <RefreshCw :size="17" :class="{ spin: loading }" />
      </button>
    </div>

    <section class="metric-strip" aria-label="评测概览">
      <div><Activity :size="18" /><span>Recorded runs</span><strong>{{ totalRuns }}</strong></div>
      <div><Gauge :size="18" /><span>Variants</span><strong>{{ rows.length }}</strong></div>
      <div><TestTube2 :size="18" /><span>Best success</span><strong class="mono-value">{{ bestVariant }}</strong></div>
    </section>

    <EvaluationChart v-if="rows.length" :summary="summary" />

    <section class="evaluation-table-section">
      <header class="section-heading">
        <div><h2>Architecture comparison</h2><p>Aggregate performance across persisted benchmark runs</p></div>
      </header>
      <div v-if="loading" class="loading-row"><RefreshCw :size="18" class="spin" /> Loading evaluation data</div>
      <div v-else-if="error" class="error-banner" role="alert">{{ error }}</div>
      <div v-else-if="!rows.length" class="empty-state evaluation-empty"><Activity :size="22" /> No evaluation records</div>
      <div v-else class="table-scroll">
        <table class="evaluation-table">
          <thead><tr><th>Variant</th><th>Runs</th><th>Success</th><th>Tests</th><th><Wrench :size="14" /> Tools</th><th>Iterations</th><th><Clock3 :size="14" /> Time</th><th><Coins :size="14" /> Tokens</th></tr></thead>
          <tbody>
            <tr v-for="[variant, value] in rows" :key="variant">
              <td><code>{{ variant }}</code></td><td>{{ value.runs }}</td>
              <td><span class="rate" :data-good="value.success_rate >= 0.8">{{ (value.success_rate * 100).toFixed(1) }}%</span></td>
              <td>{{ (value.test_pass_rate * 100).toFixed(1) }}%</td><td>{{ value.avg_tool_calls.toFixed(1) }}</td>
              <td>{{ value.avg_iterations.toFixed(1) }}</td><td>{{ value.avg_elapsed_seconds.toFixed(1) }}s</td>
              <td>{{ Math.round(value.avg_total_tokens).toLocaleString() }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>
