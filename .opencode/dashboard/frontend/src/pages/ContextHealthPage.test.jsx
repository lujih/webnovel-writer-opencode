import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderPage } from '../test-utils.jsx'
import { HttpError } from '../api.js'

// 上下文健康页的**四种互斥状态**——这是前两轮连续修过的区域，也是最容易
// 回归的地方：
//
//   1. loading      → "加载中..."
//   2. missing(404) → "还没有上下文快照" + 引导文案（**正常状态**，不能报红）
//   3. error(5xx)   → 红色 "加载失败：..."
//   4. 有数据       → 渲染统计卡与图表
//
// 历史事故：404 与真故障共用一条红色"加载失败"，新项目首次打开就被吓到。
// 区分这两种状态正是 HttpError.status 存在的理由，这里钉死它。

vi.mock('../api.js', async importOriginal => {
  const actual = await importOriginal()
  return {
    ...actual,
    fetchContextHealth: vi.fn(),
    fetchContextHistory: vi.fn(),
  }
})

const { fetchContextHealth, fetchContextHistory } = await import('../api.js')
const { default: ContextHealthPage } = await import('./ContextHealthPage.jsx')

const PROJECT = { progress: { current_chapter: 120 } }

function healthPayload(overrides = {}) {
  return {
    chapter: 120,
    stage: 'drafting',
    template: 'default',
    included: ['core', 'scene', 'story_contract'],
    excluded: ['memory'],
    critical_excluded: [],
    section_tokens: { core: 1200, scene: 800 },
    total_tokens: 2000,
    health_score: 100,
    weights_used: { core: 0.4, scene: 0.3 },
    ranker_enabled: true,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  fetchContextHistory.mockResolvedValue({ items: [] })
})

afterEach(() => {
  vi.resetAllMocks()
})

describe('ContextHealthPage — 404 与真错误必须区分', () => {
  it('404 显示空态引导，而不是红色报错', async () => {
    fetchContextHealth.mockRejectedValue(new HttpError(404, 'Not Found', 'trace 文件不存在'))
    renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    await waitFor(() => {
      expect(screen.getByText(/第 120 章还没有上下文快照/)).toBeInTheDocument()
    })
    // 引导里要告诉用户"怎么办"
    expect(screen.getByText(/chapter-commit/)).toBeInTheDocument()
    // 关键：不能出现"加载失败"
    expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument()
  })

  it('5xx 显示红色加载失败', async () => {
    fetchContextHealth.mockRejectedValue(new HttpError(500, 'Internal Server Error', '内部错误'))
    renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    await waitFor(() => {
      expect(screen.getByText(/加载失败：内部错误/)).toBeInTheDocument()
    })
    // 真错误不能给"还没有快照"的引导，那是误导
    expect(screen.queryByText(/还没有上下文快照/)).not.toBeInTheDocument()
  })

  it('网络错误（无 status）走 error 分支', async () => {
    fetchContextHealth.mockRejectedValue(new Error('Failed to fetch'))
    renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeInTheDocument()
    })
  })

  it('有数据时渲染统计卡', async () => {
    fetchContextHealth.mockResolvedValue(healthPayload())
    renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    await waitFor(() => expect(screen.getByText('drafting')).toBeInTheDocument())
    expect(screen.getByText('default')).toBeInTheDocument()
    expect(screen.queryByText(/还没有上下文快照/)).not.toBeInTheDocument()
  })
})

describe('ContextHealthPage — 历史为空时的提示', () => {
  it('history.items 为空时说明"尚未生成任何上下文快照"', async () => {
    fetchContextHealth.mockRejectedValue(new HttpError(404, 'Not Found', ''))
    fetchContextHistory.mockResolvedValue({ items: [] })
    renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    await waitFor(() => {
      expect(screen.getByText('尚未生成任何上下文快照')).toBeInTheDocument()
    })
  })

  it('history 有数据时不显示该提示', async () => {
    fetchContextHealth.mockResolvedValue(healthPayload())
    fetchContextHistory.mockResolvedValue({
      items: [{ chapter: 119, stage: 'drafting', template: 'default', included_count: 5, excluded_count: 1, critical_excluded_count: 0 }],
    })
    renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    await waitFor(() => expect(screen.getByText('drafting')).toBeInTheDocument())
    expect(screen.queryByText('尚未生成任何上下文快照')).not.toBeInTheDocument()
  })
})

describe('ContextHealthPage — 竞态', () => {
  it('丢弃过期响应：慢的旧请求不能覆盖新状态', async () => {
    // 第一次请求挂起，第二次立即返回新章节数据。
    // 若没有 cancelled 标记，旧响应落地会把页面刷回旧值。
    let resolveFirst
    fetchContextHealth
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
      .mockResolvedValue(healthPayload({ chapter: 121, stage: 'reviewing' }))

    const { rerender } = renderPage(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 0 })

    // 触发第二轮（refreshToken 变化 → 新 effect，旧 effect 的 cancelled 置真）
    const { buildPageTree } = await import('../test-utils.jsx')
    rerender(buildPageTree(ContextHealthPage, { projectInfo: PROJECT, refreshToken: 1 }, '/', 'r2'))

    await waitFor(() => expect(screen.getByText('reviewing')).toBeInTheDocument())

    // 现在让第一轮的旧请求迟到落地——它不该改写界面
    resolveFirst(healthPayload({ chapter: 120, stage: 'stale-drafting' }))
    await new Promise(r => setTimeout(r, 50))

    expect(screen.queryByText('stale-drafting')).not.toBeInTheDocument()
    expect(screen.getByText('reviewing')).toBeInTheDocument()
  })
})
