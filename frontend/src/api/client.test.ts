import { afterEach, expect, it } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'

import { api } from './client'
import { authRequired, setApiKey } from './auth'

afterEach(() => {
  setApiKey('')
  authRequired.value = false
})

it('axios 读写请求动态附带认证；401 交由用户重新确认', async () => {
  setApiKey('test-key')
  await api.get('/api/auth/check', {
    adapter: async (config) => {
      expect(config.headers.get('Authorization')).toBe('Bearer test-key')
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    },
  })
  setApiKey('')
  await expect(api.post('/api/tasks/id/execute', {}, {
    adapter: async (config) => {
      expect(config.headers.get('Authorization')).toBeUndefined()
      throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, {
        data: {}, status: 401, statusText: 'Unauthorized', headers: new AxiosHeaders(), config,
      })
    },
  })).rejects.toThrow('Unauthorized')
  expect(authRequired.value).toBe(true)
})
