<script setup lang="ts">
import { Code2, GitBranch, KeyRound, LayoutDashboard } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { authRequired, getApiKey, setApiKey } from './api/auth'
import { api, getErrorMessage } from './api/client'
import { useTaskStore } from './stores/task'

const store = useTaskStore()

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
    // 上一次请求因未认证失败留下的错误条会误导用户，登录成功后清掉。
    if (store.status === 'failed') {
      store.error = ''
      store.status = 'idle'
    }
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
  if (route.name === 'task-detail') return '任务详情 · Task Trace'
  return '研发工作台 · Workspace'
})
</script>

<template>
  <div class="app-shell">
    <aside class="app-rail">
      <RouterLink class="brand-mark" to="/" aria-label="DevPilot 工作台" title="DevPilot">
        <Code2 :size="22" stroke-width="2" />
      </RouterLink>

      <nav class="rail-nav" aria-label="主导航">
        <RouterLink to="/" aria-label="研发工作台" title="研发工作台">
          <LayoutDashboard :size="20" />
        </RouterLink>
      </nav>

      <a
        class="rail-link"
        href="https://github.com/wzyn20051216/DevPilot"
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
          <strong>DevPilot</strong>
          <span>/</span>
          <span>{{ pageTitle }}</span>
        </div>
        <div class="connection-state">
          <button class="icon-button" type="button" aria-label="配置 API Key" title="配置 API Key" @click="authRequired = true">
            <KeyRound :size="17" />
          </button>
          <i aria-hidden="true"></i>
          本地运行中 · Local runtime
        </div>
      </header>

      <RouterView />
    </section>
  </div>

  <dialog ref="keyDialog" class="auth-dialog" aria-labelledby="auth-title" @cancel="authRequired = false">
    <form @submit.prevent="saveKey">
      <p class="eyebrow">Access · 访问授权</p>
      <h2 id="auth-title">连接 DevPilot</h2>
      <p class="auth-lead">输入服务管理员提供的 API Key。验证成功后，请重新发起刚才的操作。</p>
      <label for="api-key">API Key</label>
      <input
        id="api-key"
        ref="keyField"
        v-model="keyInput"
        type="password"
        autocomplete="off"
        placeholder="粘贴你的 API Key"
        required
        :disabled="checkingKey"
      />
      <p v-if="keyError" role="alert" class="auth-error">{{ keyError }}</p>
      <footer>
        <button class="secondary-button" type="button" :disabled="checkingKey" @click="setApiKey(''); authRequired = false">清除并关闭</button>
        <button class="primary-button" type="submit" :disabled="checkingKey">{{ checkingKey ? '验证中…' : '验证并连接' }}</button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.auth-dialog {
  width: min(460px, calc(100vw - 32px));
  padding: 28px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-xl);
  background: var(--surface-strong);
  box-shadow: var(--shadow-lg);
  color: var(--ink);
}
.auth-dialog::backdrop { background: rgba(13, 16, 18, .6); backdrop-filter: blur(6px); }
.auth-dialog h2 { margin: 10px 0 0; font-size: 21px; letter-spacing: -.02em; }
.auth-lead { margin: 12px 0 0; font-size: 13px; line-height: 1.6; color: var(--muted); }
.auth-dialog label { display: block; margin: 20px 0 8px; font-size: 11.5px; font-weight: 600; color: var(--ink-soft); }
.auth-dialog input {
  width: 100%; height: 44px; padding: 0 14px;
  border: 1px solid var(--line-strong); border-radius: var(--r-md);
  background: var(--surface); color: var(--ink); font-family: var(--mono); font-size: 12px;
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.auth-dialog input:focus { border-color: var(--green); box-shadow: 0 0 0 3px var(--green-glow); outline: 0; }
.auth-dialog footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px; }
.auth-dialog .auth-error { margin: 12px 0 0; padding: 10px 12px; border-left: 3px solid var(--red); border-radius: 0 var(--r-sm) var(--r-sm) 0; background: var(--red-soft); color: #8c2d2d; font-size: 11.5px; }
</style>
