import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DataTable from './DataTable.jsx'

// DataTable 是 11 个页面里 8 个在用的表格组件。它的两个坑在真实事故里都出现过：
//
// 1. **空态**：rows 为空时返回的是 `.empty-state`，不是空表格——如果这里渲染
//    错了，用户看到的是"有表头没内容"，会以为数据丢了。
// 2. **分页越界**：pageSize 变化时若没重置页码，用户会停在一个不存在的页上，
//    看到空表格（历史上有过 `useEffect` 依赖写错导致此问题）。
//
// 另外 rowKey 的解析有三种形态（函数 / 字段名 / 下标），任一形态抛错都会白屏。

const COLUMNS = [
  { key: 'chapter', label: '章节' },
  { key: 'title', label: '标题' },
]

function makeRows(count) {
  return Array.from({ length: count }, (_, i) => ({
    id: `row-${i}`,
    chapter: i + 1,
    title: `第 ${i + 1} 章标题`,
  }))
}

describe('DataTable — 空态', () => {
  it('rows 为空数组时显示空态而不是空表格', () => {
    render(<DataTable columns={COLUMNS} rows={[]} emptyText="暂无伏笔数据" />)
    expect(screen.getByText('暂无伏笔数据')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('rows 为 undefined 时不崩（接口返回异常时的兜底）', () => {
    render(<DataTable columns={COLUMNS} rows={undefined} />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
  })

  it('空态用 compact 样式（不是整页级空态）', () => {
    const { container } = render(<DataTable columns={COLUMNS} rows={[]} />)
    expect(container.querySelector('.empty-state.compact')).toBeTruthy()
  })
})

describe('DataTable — 渲染', () => {
  it('渲染表头与行', () => {
    render(<DataTable columns={COLUMNS} rows={makeRows(3)} />)
    expect(screen.getByRole('columnheader', { name: '章节' })).toBeInTheDocument()
    expect(screen.getByText('第 1 章标题')).toBeInTheDocument()
    expect(screen.getByText('第 3 章标题')).toBeInTheDocument()
  })

  it('自定义 render 覆盖默认取值', () => {
    const columns = [
      { key: 'chapter', label: '章节', render: row => `第 ${row.chapter} 章` },
    ]
    render(<DataTable columns={columns} rows={makeRows(1)} />)
    expect(screen.getByText('第 1 章')).toBeInTheDocument()
  })

  it('空值走 formatTableValue 的占位符', () => {
    render(<DataTable columns={COLUMNS} rows={[{ id: 'x', chapter: 1, title: null }]} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('minWidth 生效于 table 元素', () => {
    const { container } = render(
      <DataTable columns={COLUMNS} rows={makeRows(1)} minWidth={480} />,
    )
    expect(container.querySelector('table')).toHaveStyle({ minWidth: '480px' })
  })
})

describe('DataTable — 分页', () => {
  it('行数不超过 pageSize 时不显示翻页条', () => {
    render(<DataTable columns={COLUMNS} rows={makeRows(5)} pageSize={8} />)
    expect(screen.queryByText(/第 1\/1 页/)).not.toBeInTheDocument()
  })

  it('超出 pageSize 时显示翻页条与总量', () => {
    render(<DataTable columns={COLUMNS} rows={makeRows(20)} pageSize={8} />)
    expect(screen.getByText('第 1/3 页 · 共 20 条')).toBeInTheDocument()
  })

  it('下一页翻到第二页', async () => {
    const user = userEvent.setup()
    render(<DataTable columns={COLUMNS} rows={makeRows(20)} pageSize={8} />)

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('第 2/3 页 · 共 20 条')).toBeInTheDocument()
    expect(screen.getByText('第 9 章标题')).toBeInTheDocument()
  })

  it('首页"上一页"禁用、末页"下一页"禁用', async () => {
    const user = userEvent.setup()
    render(<DataTable columns={COLUMNS} rows={makeRows(10)} pageSize={8} />)

    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '上一页' })).toBeEnabled()
  })

  it('最后一页行数不足 pageSize 时只渲染剩余行', async () => {
    const user = userEvent.setup()
    render(<DataTable columns={COLUMNS} rows={makeRows(10)} pageSize={8} />)
    await user.click(screen.getByRole('button', { name: '下一页' }))

    const tbody = document.querySelector('tbody')
    expect(within(tbody).getAllByRole('row')).toHaveLength(2)
  })

  it('页码不会超过总页数（防越界）', async () => {
    const user = userEvent.setup()
    render(<DataTable columns={COLUMNS} rows={makeRows(10)} pageSize={8} />)
    const next = screen.getByRole('button', { name: '下一页' })
    await user.click(next)
    // 末页后按钮已禁用，再点无效
    await user.click(next)
    expect(screen.getByText('第 2/2 页 · 共 10 条')).toBeInTheDocument()
  })
})

describe('DataTable — rowKey 三种形态', () => {
  it('字符串字段名', () => {
    const { container } = render(
      <DataTable columns={COLUMNS} rows={makeRows(2)} rowKey="id" />,
    )
    // 不应抛错，且渲染出 2 行
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  })

  it('函数', () => {
    const { container } = render(
      <DataTable columns={COLUMNS} rows={makeRows(2)} rowKey={row => row.chapter} />,
    )
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  })

  it('字段缺失时退回下标，不抛错', () => {
    const rows = [{ chapter: 1, title: 'A' }, { chapter: 2, title: 'B' }]
    const { container } = render(
      <DataTable columns={COLUMNS} rows={rows} rowKey="id" />,
    )
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  })
})
