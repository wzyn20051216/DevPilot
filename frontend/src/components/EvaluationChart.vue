<script setup lang="ts">
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { Clock3, Coins, Wrench } from '@lucide/vue'
import { computed, ref } from 'vue'
import VChart from 'vue-echarts'

import type { EvaluationSummaryResponse } from '../types/evaluation'

use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ summary: EvaluationSummaryResponse }>()
type Metric = 'avg_tool_calls' | 'avg_elapsed_seconds' | 'avg_total_tokens'
const metric = ref<Metric>('avg_tool_calls')
const metricMeta = {
  avg_tool_calls: { label: 'Tool calls', color: '#167d57' },
  avg_elapsed_seconds: { label: 'Elapsed seconds', color: '#a46014' },
  avg_total_tokens: { label: 'Tokens', color: '#2d65a8' },
} as const

const option = computed(() => {
  const variants = Object.keys(props.summary)
  const selected = metricMeta[metric.value]
  return {
    animationDuration: 450,
    grid: { top: 18, left: 42, right: 18, bottom: 62 },
    tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => Number(value).toLocaleString() },
    xAxis: {
      type: 'category',
      data: variants,
      axisLabel: { color: '#6f787c', fontFamily: 'JetBrains Mono', fontSize: 10, rotate: 18 },
      axisLine: { lineStyle: { color: '#d8dddb' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#6f787c', fontSize: 10 },
      splitLine: { lineStyle: { color: '#e4e8e6' } },
    },
    series: [{
      name: selected.label,
      type: 'bar',
      barMaxWidth: 52,
      itemStyle: { color: selected.color, borderRadius: [3, 3, 0, 0] },
      data: variants.map((variant) => props.summary[variant]?.[metric.value] ?? 0),
    }],
  }
})
</script>

<template>
  <section class="evaluation-chart-section">
    <header class="section-heading">
      <div><h2>Cost comparison</h2><p>Switch metrics without leaving the persisted experiment set</p></div>
      <div class="chart-metric-switch" role="group" aria-label="图表指标">
        <button type="button" :aria-pressed="metric === 'avg_tool_calls'" title="工具调用" @click="metric = 'avg_tool_calls'"><Wrench :size="15" /></button>
        <button type="button" :aria-pressed="metric === 'avg_elapsed_seconds'" title="耗时" @click="metric = 'avg_elapsed_seconds'"><Clock3 :size="15" /></button>
        <button type="button" :aria-pressed="metric === 'avg_total_tokens'" title="Token" @click="metric = 'avg_total_tokens'"><Coins :size="15" /></button>
      </div>
    </header>
    <VChart class="evaluation-chart" :option="option" autoresize />
  </section>
</template>
