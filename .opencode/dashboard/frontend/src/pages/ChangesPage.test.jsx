import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderPage } from '../test-utils.jsx'
import { HttpError } from '../api.js'

// 变更审计页的三种互斥状态。与 ContextHealthPage 同一条教训：
// **404 是正常状态（该章还没提交），不是故障**——两者共用一条红色
// 「加载失败」会让新项目一打开就被吓到。
//
// 另外钉住「默认停在上次写的章」：硬编码第 1 章会让长书用户每次手填。

vi.mock('../api.js', async importOriginal => {
    const actual = await importOriginal()
    return {
        ...actual,
        fetchProjectInfo: vi.fn(),
        fetchChapterChanges: vi.fn(),
        fetchChangesDiff: vi.fn(),
    }
})

const { fetchProjectInfo, fetchChapterChanges, fetchChangesDiff } = await import('../api.js')
const { default: ChangesPage } = await import('./ChangesPage.jsx')

function change(overrides = {}) {
    return {
        entity_id: 'xiaoyan',
        field: 'realm',
        old: '斗者',
        new: '斗师',
        source: 'state_deltas',
        risk: 'high',
        ...overrides,
    }
}

beforeEach(() => {
    fetchProjectInfo.mockResolvedValue({ progress: { current_chapter: 5 } })
    fetchChapterChanges.mockResolvedValue({ chapter: 5, changes: [], count: 0 })
    fetchChangesDiff.mockResolvedValue({
        from: 4, to: 5,
        changed: [], added: [], removed: [],
        counts: { changed: 0, added: 0, removed: 0 },
    })
})

afterEach(() => {
    vi.clearAllMocks()
})

describe('ChangesPage', () => {
    it('默认跳到上次写的章', async () => {
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => expect(fetchChapterChanges).toHaveBeenCalledWith(5))
    })

    it('404 显示「还没有提交记录」而不是红色故障', async () => {
        fetchChapterChanges.mockRejectedValue(new HttpError(404, '没有 commit 记录'))
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => {
            expect(screen.getByText(/还没有提交记录/)).toBeInTheDocument()
        })
        expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument()
    })

    it('5xx 显示红色加载失败', async () => {
        fetchChapterChanges.mockRejectedValue(new HttpError(500, 'boom'))
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => {
            expect(screen.getByText(/加载失败/)).toBeInTheDocument()
        })
    })

    it('渲染变更表格（实体/字段/旧值/新值/风险）', async () => {
        fetchChapterChanges.mockResolvedValue({
            chapter: 5,
            changes: [change()],
            count: 1,
        })
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => expect(screen.getByText('xiaoyan')).toBeInTheDocument())
        expect(screen.getByText('realm')).toBeInTheDocument()
        expect(screen.getByText('斗者')).toBeInTheDocument()
        expect(screen.getByText('斗师')).toBeInTheDocument()
        expect(screen.getByText('高危')).toBeInTheDocument()
    })

    it('违背锁定要显式标出来', async () => {
        fetchChapterChanges.mockResolvedValue({
            chapter: 5,
            changes: [change({ field: 'hair_color', old: '黑', new: '白', risk: 'low', locked_value: '黑' })],
            count: 1,
        })
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => expect(screen.getByText(/违背锁定/)).toBeInTheDocument())
    })

    it('勾选「只看高危/中危」会过滤掉低危行', async () => {
        fetchChapterChanges.mockResolvedValue({
            chapter: 5,
            changes: [
                change(),
                change({ field: 'mood', old: '平静', new: '愤怒', risk: 'low' }),
            ],
            count: 2,
        })
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => expect(screen.getByText('mood')).toBeInTheDocument())

        await userEvent.click(screen.getByLabelText('只看高危/中危'))
        await waitFor(() => {
            expect(screen.queryByText('mood')).not.toBeInTheDocument()
        })
        expect(screen.getByText('realm')).toBeInTheDocument()
    })

    it('空变更给出说明而不是空表格', async () => {
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => {
            expect(screen.getByText(/这一章没有状态变更/)).toBeInTheDocument()
        })
    })
})

describe('ChangesPage — 对比两章', () => {
    async function switchToDiff() {
        renderPage(ChangesPage, { refreshToken: 0 })
        await waitFor(() => expect(fetchChapterChanges).toHaveBeenCalled())
        await userEvent.click(screen.getByText('对比两章'))
    }

    it('切到对比模式会用默认区间请求 diff', async () => {
        await switchToDiff()
        // 默认「上一章 → 当前章」（current_chapter=5）
        await waitFor(() => expect(fetchChangesDiff).toHaveBeenCalledWith(4, 5))
    })

    it('渲染变化 / 新增 / 消失三组', async () => {
        fetchChangesDiff.mockResolvedValue({
            from: 4, to: 5,
            changed: [{ entity_id: 'a', field: 'realm', from: '斗者', to: '斗师' }],
            added: [{ entity_id: 'c', field: 'realm', new: '斗王' }],
            removed: [{ entity_id: 'b', field: 'realm', new: '斗者' }],
            counts: { changed: 1, added: 1, removed: 1 },
        })
        await switchToDiff()
        await waitFor(() => expect(screen.getByText(/值发生变化/)).toBeInTheDocument())
        expect(screen.getByText(/后者新增/)).toBeInTheDocument()
        expect(screen.getByText(/后者不再有/)).toBeInTheDocument()
        expect(screen.getByText(/斗师/)).toBeInTheDocument()
        expect(screen.getByText(/斗王/)).toBeInTheDocument()
    })

    it('两章完全一致时给提示而不是空卡片', async () => {
        await switchToDiff()
        await waitFor(() => {
            expect(screen.getByText(/完全一致/)).toBeInTheDocument()
        })
    })

    it('对比模式下 404 也不报红', async () => {
        fetchChangesDiff.mockRejectedValue(new HttpError(404, '没有 commit 记录'))
        await switchToDiff()
        await waitFor(() => expect(screen.getByText(/还没有提交记录/)).toBeInTheDocument())
        expect(screen.queryByText(/加载失败/)).not.toBeInTheDocument()
    })
})
