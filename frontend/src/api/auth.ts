import { ref } from 'vue'

const STORAGE_KEY = 'devpilot.api-key'
export const authRequired = ref(false)

/** @brief 仅在当前标签页保存凭据；受限浏览器回退到内存。 */
let memoryKey = ''
export function getApiKey(): string {
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? memoryKey
  } catch {
    return memoryKey
  }
}

/** @brief 更新或清除凭据，不把密钥写入 URL 或日志。 */
export function setApiKey(value: string): void {
  memoryKey = value.trim()
  try {
    if (memoryKey) sessionStorage.setItem(STORAGE_KEY, memoryKey)
    else sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // 禁用浏览器存储时，本次页面生命周期内仍可使用凭据。
  }
}

/** @brief axios 与 fetch SSE 共享同一份认证头。 */
export function authHeaders(): Record<string, string> {
  const key = getApiKey()
  return key ? { Authorization: `Bearer ${key}` } : {}
}

/** @brief 401 后显示输入框；由用户确认后重新发起业务操作。 */
export function requestAuthentication(): void {
  authRequired.value = true
}
