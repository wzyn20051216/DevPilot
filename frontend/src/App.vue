<script setup lang="ts">
import { Activity, BarChart3, Code2, GitBranch, KeyRound, PanelLeft } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { authRequired, getApiKey, setApiKey } from './api/auth'
import { api, getErrorMessage } from './api/client'

const keyInput = ref('')
const keyError = ref('')
const checkingKey = ref(false)
const keyDialog = ref<HTMLDialogElement>()
const keyField = ref<HTMLInputElement>()

watch(authRequired, (open) => {
  if (open) {
    keyInput.value = ''
    keyError.value = ''
    keyDialog.value?.showModal()
    keyField.value?.focus()
  } else keyDialog.value?.close()
}, { flush: 'post' })

/** @brief 用轻量接口验证 Key，避免重放修改仓库或发布请求。 */
async function saveKey(): Promise<void> {
  const previousKey = getApiKey()
  checkingKey.value = true
  keyError.value = ''
  setApiKey(keyInput.value)
  try {
    await api.get('/api/auth/check')
    authRequired.value = false
    keyInput.value = ''
  } catch (error) {
    setApiKey(previousKey)
    keyError.value = getErrorMessage(error)
  } finally {
    checkingKey.value = false
  }
}

onMounted(() => {
  void api.get('/api/auth/check').catch(() => { /* 401 由拦截器触发输入框。 */ })
})

const route = useRoute()
const pageTitle = computed(() => {
  if (route.name === 'evaluations') return 'Evaluation Dashboard'
  if (route.name === 'task-detail') return 'Task Trace'
  return 'Development Workspace'
})
</script>

<template>
  <div class="app-shell">
    <aside class="app-rail">
      <RouterLink class="brand-mark" to="/" aria-label="DevPilot 工作台" title="DevPilot">
        <Code2 :size="22" stroke-width="2" />
      </RouterLink>

      <nav class="rail-nav" aria-label="主导航">
        <RouterLink to="/" aria-label="开发工作台" title="开发工作台">
          <Activity :size="20" />
        </RouterLink>
        <RouterLink to="/evaluations" aria-label="评测面板" title="评测面板">
          <BarChart3 :size="20" />
        </RouterLink>
      </nav>

      <a
        class="rail-link"
        href="https://github.com"
        target="_blank"
        rel="noreferrer"
        aria-label="GitHub"
        title="GitHub"
      >
        <GitBranch :size="19" />
      </a>
    </aside>

    <section class="app-frame">
      <header class="topbar">
        <div class="product-name">
          <PanelLeft :size="17" />
          <strong>DevPilot</strong>
          <span>/</span>
          <span>{{ pageTitle }}</span>
        </div>
        <div class="connection-state">
          <button class="icon-button" type="button" aria-label="配置 API Key" title="配置 API Key" @click="authRequired = true">
            <KeyRound :size="17" />
          </button>
          <i aria-hidden="true"></i>
          Local runtime
        </div>
      </header>

      <RouterView />
    </section>
  </div>
  <dialog ref="keyDialog" class="auth-dialog" aria-labelledby="auth-title" @cancel="authRequired = false">
    <form @submit.prevent="saveKey">
      <h2 id="auth-title">连接 DevPilot</h2>
      <p>输入服务管理员提供的 API Key。验证后请重新发起刚才的操作。</p>
      <label for="api-key">API Key</label>
      <input id="api-key" ref="keyField" v-model="keyInput" type="password" autocomplete="off" required :disabled="checkingKey" />
      <p v-if="keyError" role="alert" class="auth-error">{{ keyError }}</p>
      <footer>
        <button class="secondary-button" type="button" :disabled="checkingKey" @click="setApiKey(''); authRequired = false">清除并关闭</button>
        <button class="primary-button" type="submit" :disabled="checkingKey">{{ checkingKey ? '验证中…' : '验证并连接' }}</button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.auth-dialog { width: min(440px, calc(100vw - 32px)); padding: 24px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); color: var(--ink); }
.auth-dialog::backdrop { background: rgba(23, 26, 29, .5); }
.auth-dialog h2 { margin: 0 0 12px; font-size: 20px; }
.auth-dialog p { font-size: 13px; line-height: 1.6; color: var(--muted); }
.auth-dialog label { display: block; margin: 16px 0 8px; font-size: 12px; }
.auth-dialog input { width: 100%; height: 38px; padding: 0 10px; border: 1px solid var(--line-strong); border-radius: 5px; background: var(--surface-strong); }
.auth-dialog footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
.auth-dialog .auth-error { color: var(--red); }
</style>
