import assert from 'node:assert/strict'
import { inspect } from 'node:util'
import { after, before, test } from 'node:test'

import { message } from 'ant-design-vue'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

let server
let apiRequest
let useUserStore

before(async () => {
  globalThis.localStorage = {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {}
  }
  setActivePinia(createPinia())
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ apiRequest } = await server.ssrLoadModule('/src/apis/base.js'))
  ;({ useUserStore } = await server.ssrLoadModule('/src/stores/user.js'))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

test('422 错误日志保留请求方法和状态，但不输出伪造凭据与敏感输入', async () => {
  const token = 'fixture-token-r04'
  const cookie = 'fixture-cookie-r04'
  const bodyValue = 'fixture-body-r04'
  const queryValue = 'fixture-query-r04'
  const echoedValue = 'fixture-echo-r04'
  const responseHeader = 'fixture-header-r04'
  const logged = []
  const originalFetch = globalThis.fetch
  const originalLog = console.log
  const originalError = console.error
  const originalWarn = console.warn

  useUserStore().token = token
  globalThis.fetch = async (url, options) => {
    assert.equal(url.includes(queryValue), true)
    assert.equal(options.headers.Authorization === `Bearer ${token}`, true)
    assert.equal(options.headers.Cookie === cookie, true)
    assert.equal(options.body.includes(bodyValue), true)
    return new Response(
      JSON.stringify({ detail: { message: `无效输入 ${bodyValue}` }, echo: echoedValue }),
      { status: 422, headers: { 'Content-Type': 'application/json', 'X-Debug': responseHeader } }
    )
  }
  console.log = (...args) => logged.push(inspect(args, { depth: null }))
  console.error = (...args) => logged.push(inspect(args, { depth: null }))
  console.warn = (...args) => logged.push(inspect(args, { depth: null }))

  try {
    let requestError
    try {
      await apiRequest(`/api/test?token=${queryValue}`, {
        method: 'POST',
        headers: { Cookie: cookie },
        body: JSON.stringify({ password: bodyValue })
      })
    } catch (error) {
      requestError = error
    }

    assert.equal(requestError?.response?.status, 422)
    assert.equal(requestError?.message?.includes(bodyValue), true)
    assert.equal(requestError?.response?.data?.echo === echoedValue, true)

    const output = logged.join('\n')
    assert.equal(logged.length > 0, true)
    assert.equal(output.includes('POST'), true)
    assert.equal(output.includes('422'), true)
    for (const secret of [token, cookie, bodyValue, queryValue, echoedValue, responseHeader]) {
      assert.equal(output.includes(secret), false, '错误日志不应包含敏感测试值')
    }
  } finally {
    globalThis.fetch = originalFetch
    console.log = originalLog
    console.error = originalError
    console.warn = originalWarn
  }
})

test('认证与服务端错误沿用既有提示，解析失败和中止请求不泄露错误对象', async () => {
  const echoedValue = 'fixture-error-r04'
  const logged = []
  const prompts = []
  const originalFetch = globalThis.fetch
  const originalLog = console.log
  const originalError = console.error
  const originalWarn = console.warn
  const originalMessageError = message.error
  const originalSetTimeout = globalThis.setTimeout
  const store = useUserStore()
  const originalLogout = store.logout
  let logoutCalled = false

  store.token = 'fixture-auth-r04'
  store.logout = () => {
    logoutCalled = true
    store.token = ''
  }
  message.error = (prompt) => prompts.push(prompt)
  globalThis.setTimeout = () => 1
  console.log = (...args) => logged.push(inspect(args, { depth: null }))
  console.error = (...args) => logged.push(inspect(args, { depth: null }))
  console.warn = (...args) => logged.push(inspect(args, { depth: null }))

  try {
    for (const [status, detail, expectedMessage] of [
      [403, echoedValue, '没有权限执行此操作'],
      [500, echoedValue, '服务器内部错误，请使用 docker logs api-dev 查看详细日志'],
      [401, 'token expired', 'token expired']
    ]) {
      globalThis.fetch = async () =>
        new Response(JSON.stringify({ detail, echo: echoedValue }), { status })
      let requestError
      try {
        await apiRequest('/api/test', { method: 'GET' })
      } catch (error) {
        requestError = error
      }
      assert.equal(requestError?.response?.status, status)
      assert.equal(requestError?.message === expectedMessage, true)
    }
    assert.deepEqual(prompts, ['登录已过期，请重新登录'])
    assert.equal(logoutCalled, true)

    globalThis.fetch = async () => {
      throw Object.assign(new Error(echoedValue), { name: 'AbortError' })
    }
    const logsBeforeAbort = logged.length
    let abortError
    try {
      await apiRequest('/api/test', {}, false)
    } catch (error) {
      abortError = error
    }
    assert.equal(abortError?.name, 'AbortError')
    assert.equal(logged.length, logsBeforeAbort)

    globalThis.fetch = async () => ({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: new Headers(),
      json: async () => {
        throw new SyntaxError(echoedValue)
      }
    })
    let parseError
    try {
      await apiRequest('/api/test', { method: 'POST' }, false)
    } catch (error) {
      parseError = error
    }
    assert.equal(parseError?.response?.status, 422)
    assert.equal(parseError?.message?.includes('请求失败'), true)

    globalThis.fetch = async () => {
      throw new Error(echoedValue)
    }
    let networkError
    try {
      await apiRequest('/api/test', { method: 'GET' }, false)
    } catch (error) {
      networkError = error
    }
    assert.equal(networkError?.message?.includes(echoedValue), true)
    assert.equal(logged.join('\n').includes(echoedValue), false, '错误日志不应包含服务端回显')
  } finally {
    globalThis.fetch = originalFetch
    globalThis.setTimeout = originalSetTimeout
    console.log = originalLog
    console.error = originalError
    console.warn = originalWarn
    message.error = originalMessageError
    store.logout = originalLogout
  }
})
