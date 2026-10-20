import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Badge from './Badge.jsx'
import Pager from './Pager.jsx'

// 这两个组件被 11 个页面共用。它们的 bug 不会让页面白屏，只会让信息读错
// ——比如 Badge 的 tone 落到 neutral、Pager 在首页就把"上一页"点亮。
// 这类问题以前只能靠人眼看截图发现。

describe('Badge', () => {
  it('渲染内容', () => {
    render(<Badge>超期</Badge>)
    expect(screen.getByText('超期')).toBeInTheDocument()
  })

  it.each([
    ['blue', 'badge-blue'],
    ['green', 'badge-green'],
    ['amber', 'badge-amber'],
    ['red', 'badge-red'],
    ['purple', 'badge-purple'],
    ['cyan', 'badge-cyan'],
    ['neutral', 'badge-neutral'],
  ])('tone=%s 映射到 %s', (tone, expected) => {
    render(<Badge tone={tone}>x</Badge>)
    expect(screen.getByText('x')).toHaveClass(expected)
  })

  it('未知 tone 退回 neutral，而不是丢失样式', () => {
    // 这是真实风险：后端返回了一个前端没枚举的 tone，
    // 若直接拼 className 会得到 undefined，徽章变成无边框裸文本。
    render(<Badge tone="不存在的色调">x</Badge>)
    expect(screen.getByText('x')).toHaveClass('badge-neutral')
  })

  it('默认 tone 是 neutral', () => {
    render(<Badge>x</Badge>)
    expect(screen.getByText('x')).toHaveClass('badge-neutral')
  })

  it('透传 title（悬停提示）', () => {
    render(<Badge title="第 120 章已超期">x</Badge>)
    expect(screen.getByText('x')).toHaveAttribute('title', '第 120 章已超期')
  })

  it('附加 className 与基础类共存', () => {
    render(<Badge className="extra">x</Badge>)
    const el = screen.getByText('x')
    expect(el).toHaveClass('badge')
    expect(el).toHaveClass('extra')
  })
})

describe('Pager', () => {
  const baseProps = {
    page: 2,
    totalPages: 3,
    currentStart: 51,
    currentEnd: 100,
    totalItems: 120,
    onPrevious: vi.fn(),
    onNext: vi.fn(),
    onLatest: vi.fn(),
  }

  it('totalItems 为 0 时完全不渲染', () => {
    const { container } = render(<Pager {...baseProps} totalItems={0} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('显示区间与页码', () => {
    render(<Pager {...baseProps} />)
    expect(screen.getByText('第 51-100 章 · 第 2/3 页')).toBeInTheDocument()
  })

  it('首页时"上一页"禁用', () => {
    render(<Pager {...baseProps} page={1} />)
    expect(screen.getByRole('button', { name: /前 50/ })).toBeDisabled()
  })

  it('末页时"下一页"与"跳到最新"禁用', () => {
    render(<Pager {...baseProps} page={3} totalPages={3} />)
    expect(screen.getByRole('button', { name: /下一页/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /跳到最新/ })).toBeDisabled()
  })

  it('中间页时三个按钮都可用', () => {
    render(<Pager {...baseProps} />)
    expect(screen.getByRole('button', { name: /前 50/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /下一页/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /跳到最新/ })).toBeEnabled()
  })

  it('点击回调被调用', async () => {
    const onPrevious = vi.fn()
    const onNext = vi.fn()
    const onLatest = vi.fn()
    const user = userEvent.setup()
    render(<Pager {...baseProps} onPrevious={onPrevious} onNext={onNext} onLatest={onLatest} />)

    await user.click(screen.getByRole('button', { name: /前 50/ }))
    await user.click(screen.getByRole('button', { name: /下一页/ }))
    await user.click(screen.getByRole('button', { name: /跳到最新/ }))

    expect(onPrevious).toHaveBeenCalledTimes(1)
    expect(onNext).toHaveBeenCalledTimes(1)
    expect(onLatest).toHaveBeenCalledTimes(1)
  })

  it('stepLabel 可定制（不同页面窗口大小不同）', () => {
    render(<Pager {...baseProps} stepLabel="20" />)
    expect(screen.getByRole('button', { name: /前 20/ })).toBeInTheDocument()
  })
})
