<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ diff: string }>()

const lines = computed(() => props.diff.split('\n').map((content, index) => ({
  content,
  index,
  kind: content.startsWith('+++') || content.startsWith('---')
    ? 'file'
    : content.startsWith('+')
      ? 'addition'
      : content.startsWith('-')
        ? 'deletion'
        : content.startsWith('@@')
          ? 'hunk'
          : 'context',
})))
</script>

<template>
  <div v-if="diff" class="diff-viewer" aria-label="Git 差异">
    <div v-for="line in lines" :key="line.index" class="diff-line" :data-kind="line.kind">
      <span>{{ line.index + 1 }}</span><code>{{ line.content || ' ' }}</code>
    </div>
  </div>
  <div v-else class="empty-state compact-empty">No uncommitted changes</div>
</template>
