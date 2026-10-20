import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { buildPageTree, renderPage } from '../test-utils.jsx'

// 追读债页的状态矩阵。
//
// 这页有 4 条互斥的渲染分支（loading / error / 空态 / 有数据），
// 而且数据里有几个**容易读错**的字段：
//   * totals 是全表口径，items 受 limit 限制 → 有 truncated 时必须说明；
//   * is_overdue 按当前章推算，与库里的 status 可能不一致 → 徽章要用前者；
//   * chapters_left 是**有符号**的，负数意味着已超期。
// 这些以前只靠人工看截图，回归了没人知道。

vi.mock('../api.js', () => ({
  fetchDebtsSummary: vi.fn(),
  fetchDebtDetail: vi.fn(),
}))

const { fetchDebtsSummary, fetchDebtDetail } = await import('../api.js')
const { default: DebtPage } = await import('./DebtPage.jsx')

function summary(overrides = {}) {
  return {
    current_chapter: 120,
    items: [
      {
        id: 1, debt_type: 'foreshadowing', original_amount: 1, current_amount: 2.46,
        growth: 2.46, interest_rate: 0.1, source_chapter: 15, due_chapter: 45,
        chapters_left: -75, status: 'active', is_overdue: true,
      },
      {
        id: 2, debt_type: 'pacing_debt', original_amount: 1.5, current_amount: 1.62,
        growth: 1.08, interest_rate: 0.08, source_chapter: 110, due_chapter: 175,
        chapters_left: 55, status: 'active', is_overdue: false,
      },
    ],
    totals: { count: 2, balance: 4.08, overdue: 1 },
    by_status: { active: 2 },
    by_type: { foreshadowing: { count: 1, total: 2.46 }, pacing_debt: { count: 1, total: 1.62 } },
    truncated: false,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.resetAllMocks()
})

describe('DebtPage — 加载与错误', () => {
  it('首屏显示加载中', () => {
    fetchDebtsSummary.mockReturnValue(new Promise(() => {})) // 永不 resolve
    renderPage(DebtPage, { refreshToken: 0 })
    expect(screen.getByText('正在加载…')).toBeInTheDocument()
  })

  it('请求失败显示错误文案', async () => {
    fetchDebtsSummary.mockRejectedValue(new Error('500 Internal Server Error'))
    renderPage(DebtPage, { refreshToken: 0 })
    await waitFor(() => {
      expect(screen.getByText(/加载失败：500/)).toBeInTheDocument()
    })
  })
})

describe('DebtPage — 有数据', () => {
  it('渲染统计卡与表格', async () => {
    fetchDebtsSummary.mockResolvedValue(summary())
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getByText('追读债')).toBeInTheDocument())
    // 总余额 4.08 → 格式化后 4.1。
    // 注意要限定在统计卡里找：这个数字也可能出现在表格的"当前金额"列。
    const table = screen.getByRole('table')
    expect(within(table).getByText('伏笔未收')).toBeInTheDocument()
    expect(within(table).getByText('节奏欠账')).toBeInTheDocument()
    expect(screen.getByText('4.1', { selector: '.stat-value' })).toBeInTheDocument()
  })

  it('超期徽章用 is_overdue 而不是 status', async () => {
    // 库里 status=active，但 is_overdue=true —— 表格行必须显示"已超期"。
    // 统计卡里也有"已超期"label，所以要限定在表格内断言。
    fetchDebtsSummary.mockResolvedValue(summary())
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument())
    const table = screen.getByRole('table')
    const badges = within(table).getAllByText(/已超期|进行中/)
    const texts = badges.map(el => el.textContent)
    // 第一行（is_overdue=true）→ 已超期；第二行（false）→ 进行中
    expect(texts).toEqual(['已超期', '进行中'])
  })

  it('chapters_left 为负显示"超 N 章"', async () => {
    fetchDebtsSummary.mockResolvedValue(summary())
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getByText('超 75 章')).toBeInTheDocument())
    expect(screen.getByText('55 章')).toBeInTheDocument()
  })

  it('滚存倍数按水位配色', async () => {
    fetchDebtsSummary.mockResolvedValue(summary())
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getByRole('table')).toBeInTheDocument())
    const table = screen.getByRole('table')
    // 阈值来自 growthTone：>=2.5 红 / >=1.8 琥珀 / 其余绿。
    // 2.46 落在琥珀档（差 0.04 没到红），1.08 是绿。
    expect(within(table).getByText('×2.5')).toHaveClass('badge-amber')
    expect(within(table).getByText('×1.1')).toHaveClass('badge-green')
  })
})

describe('DebtPage — 截断提示', () => {
  it('truncated=true 时统计卡说明只显示前 N 笔', async () => {
    fetchDebtsSummary.mockResolvedValue(summary({ truncated: true, totals: { count: 60, balance: 120, overdue: 50 } }))
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => {
      expect(screen.getByText(/列表仅显示前 2 笔/)).toBeInTheDocument()
    })
    // totals 仍是全表口径
    expect(screen.getByText('60')).toBeInTheDocument()
  })

  it('truncated=false 时不出现截断说明', async () => {
    fetchDebtsSummary.mockResolvedValue(summary())
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getByText('追读债')).toBeInTheDocument())
    expect(screen.queryByText(/列表仅显示前/)).not.toBeInTheDocument()
  })
})

describe('DebtPage — 空态', () => {
  it('无债务时给出"债从哪来"的说明，而不是干巴巴的暂无数据', async () => {
    fetchDebtsSummary.mockResolvedValue(summary({ items: [], totals: { count: 0, balance: 0, overdue: 0 }, by_type: {}, truncated: false }))
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => {
      expect(screen.getByText(/该项目还没有追读债/)).toBeInTheDocument()
    })
    expect(screen.getByText(/chapter-commit/)).toBeInTheDocument()
  })
})

describe('DebtPage — 明细展开', () => {
  it('点击"明细"加载并显示事件时间线', async () => {
    const user = userEvent.setup()
    fetchDebtsSummary.mockResolvedValue(summary())
    fetchDebtDetail.mockResolvedValue({
      debt: { id: 1, debt_type: 'foreshadowing', current_amount: 2.46, due_chapter: 45 },
      events: [
        { id: 10, chapter: 15, event_type: 'create', amount: 1, note: '伏笔埋设生成债务', created_at: '2026-10-19 17:30' },
      ],
      current_chapter: 120,
      chapters_left: -75,
      is_overdue: true,
    })
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getAllByRole('button', { name: '明细' })).not.toHaveLength(0))
    await user.click(screen.getAllByRole('button', { name: '明细' })[0])

    await waitFor(() => {
      expect(screen.getByText(/债务 #1 事件时间线/)).toBeInTheDocument()
    })
    // 「建债」也是 EVENT_TYPE_LABELS 的通用词，限定在内找
    const detailCard = screen.getByText(/债务 #1 事件时间线/).closest('.card')
    expect(within(detailCard).getByText('建债')).toBeInTheDocument()
    expect(fetchDebtDetail).toHaveBeenCalledWith(1)
  })

  it('关闭按钮收起明细', async () => {
    const user = userEvent.setup()
    fetchDebtsSummary.mockResolvedValue(summary())
    fetchDebtDetail.mockResolvedValue({ debt: {}, events: [], current_chapter: 120 })
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getAllByRole('button', { name: '明细' })).not.toHaveLength(0))
    await user.click(screen.getAllByRole('button', { name: '明细' })[0])
    await waitFor(() => expect(screen.getByText(/事件时间线/)).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByText(/事件时间线/)).not.toBeInTheDocument())
  })

  it('明细请求失败时不崩，停留在加载态提示', async () => {
    const user = userEvent.setup()
    fetchDebtsSummary.mockResolvedValue(summary())
    fetchDebtDetail.mockRejectedValue(new Error('404'))
    renderPage(DebtPage, { refreshToken: 0 })

    await waitFor(() => expect(screen.getAllByRole('button', { name: '明细' })).not.toHaveLength(0))
    await user.click(screen.getAllByRole('button', { name: '明细' })[0])

    // detail 请求失败 → setDetail(null) → 停留在"正在加载明细…"（不崩、不白屏）
    await waitFor(() => {
      expect(screen.getByText('正在加载明细…')).toBeInTheDocument()
    })
    // 关键：页面其余部分仍然在（失败只影响明细区）
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})

describe('DebtPage — 刷新', () => {
  it('refreshToken 变化会重新拉取（SSE 驱动）', async () => {
    fetchDebtsSummary.mockResolvedValue(summary())
    // 必须真正重挂一次 Router 才能改变 Outlet context；
    // renderPage 内部每次新建 MemoryRouter，用 key 强制重挂。
    const { rerender } = renderPage(DebtPage, { refreshToken: 0 })
    await waitFor(() => expect(fetchDebtsSummary).toHaveBeenCalledTimes(1))

    rerender(
      buildPageTree(DebtPage, { refreshToken: 1 }, '/', 'r2'),
    )
    await waitFor(() => expect(fetchDebtsSummary).toHaveBeenCalledTimes(2))
  })
})
