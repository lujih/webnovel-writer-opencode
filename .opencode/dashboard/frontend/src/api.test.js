import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  apiDelete,
  apiPost,
  apiPut,
  fetchJSON,
  HttpError,
} from './api.js'

// api.js 是所有页面与后端之间的唯一通道。它的错误语义一旦不一致，
// 页面就只能在字符串里比对 —— 2026-10 的「上下文页把 404 显示成加载失败」
// 正是这么来的：fetchJSON 抛裸 Error，调用方拿不到 status。
//
// 这里钉住三件事：
//   1. 四种方法（fetchJSON / apiPost / apiPut / apiDelete）抛同一种错误；
//   2. 错误携带 status 与 isNotFound，且 message 优先用后端 detail；
//   3. 后端返回非 JSON（HTML 错误页）时降级成状态文本，而不是崩掉。

const ORIGIN = 'http://127.0.0.1:8765'

beforeEach(() => {
  vi.stubGlobal('window', { location: { origin: ORIGIN } })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function mockResponse({ ok, status = 200, statusText = 'OK', detail, jsonThrows = false }) {
  return {
    ok,
    status,
    statusText,
    json: async () => {
      if (jsonThrows) throw new SyntaxError('Unexpected token <')
      return detail === undefined ? {} : { detail }
    },
  }
}

const METHODS = [
  ['fetchJSON', () => fetchJSON('/api/probe')],
  ['apiPost', () => apiPost('/api/probe', {})],
  ['apiPut', () => apiPut('/api/probe', {})],
  ['apiDelete', () => apiDelete('/api/probe')],
]

describe.each(METHODS)('%s 的错误语义', (_name, call) => {
  it('404 携带 status 与 isNotFound', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse({
      ok: false, status: 404, statusText: 'Not Found', detail: 'trace 文件不存在',
    })))

    await expect(call()).rejects.toMatchObject({
      name: 'HttpError',
      status: 404,
    })
    try {
      await call()
    } catch (error) {
      expect(error).toBeInstanceOf(HttpError)
      expect(error.isNotFound).toBe(true)
      // message 用后端 detail，比 "404 Not Found" 更能告诉用户发生了什么
      expect(error.message).toBe('trace 文件不存在')
    }
  })

  it('500 不是 notFound', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse({
      ok: false, status: 500, statusText: 'Internal Server Error',
    })))

    try {
      await call()
      throw new Error('应当抛错')
    } catch (error) {
      expect(error.status).toBe(500)
      expect(error.isNotFound).toBe(false)
    }
  })

  it('非 JSON 错误响应降级为状态文本而不崩', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse({
      ok: false, status: 502, statusText: 'Bad Gateway', jsonThrows: true,
    })))

    try {
      await call()
      throw new Error('应当抛错')
    } catch (error) {
      expect(error).toBeInstanceOf(HttpError)
      expect(error.status).toBe(502)
      expect(error.message).toBe('502 Bad Gateway')
    }
  })

  it('2xx 正常返回 JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({ ok: true }),
    })))
    await expect(call()).resolves.toEqual({ ok: true })
  })
})

describe('fetchJSON 参数拼接', () => {
  it('跳过 undefined / null / 空串，保留 0 与 false', async () => {
    const spy = vi.fn(async () => ({
      ok: true, status: 200, statusText: 'OK', json: async () => ({}),
    }))
    vi.stubGlobal('fetch', spy)

    await fetchJSON('/api/x', { a: 1, b: undefined, c: null, d: '', e: 0, f: false })

    const url = new URL(spy.mock.calls[0][0])
    expect(url.searchParams.get('a')).toBe('1')
    expect(url.searchParams.has('b')).toBe(false)
    expect(url.searchParams.has('c')).toBe(false)
    expect(url.searchParams.has('d')).toBe(false)
    // 0 与 false 是**有效**过滤值（如 threshold=0），不能被当成空值丢掉
    expect(url.searchParams.get('e')).toBe('0')
    expect(url.searchParams.get('f')).toBe('false')
  })
})

describe('HttpError', () => {
  it('是无参 statusText 时仍能构造', () => {
    const error = new HttpError(500, 'Internal Server Error', '')
    expect(error.message).toBe('500 Internal Server Error')
    expect(error.isNotFound).toBe(false)
  })

  it('isNotFound 只认 404', () => {
    expect(new HttpError(404, 'Not Found', '').isNotFound).toBe(true)
    expect(new HttpError(403, 'Forbidden', '').isNotFound).toBe(false)
    expect(new HttpError(200, 'OK', '').isNotFound).toBe(false)
  })

  it('是 Error 的子类，能被 catch 与 instanceof 识别', () => {
    expect(new HttpError(500, 'x', '')).toBeInstanceOf(Error)
  })
})
