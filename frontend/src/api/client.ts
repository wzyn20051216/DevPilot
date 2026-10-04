import axios from 'axios'
import { getApiKey, requestAuthentication } from './auth'

// 空字符串表示同源请求：开发环境由 Vite 代理，生产环境由 Nginx 代理。
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use((config) => {
  const key = getApiKey()
  if (key) config.headers.set('Authorization', `Bearer ${key}`)
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) requestAuthentication()
    return Promise.reject(error)
  },
)

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') {
      return detail
    }
    const structuredMessage = error.response?.data?.error?.message
    if (typeof structuredMessage === 'string') {
      return structuredMessage
    }
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}
