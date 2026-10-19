import { describe, expect, it } from 'vitest'
import { buildForeshadowingRecords, summarizeForeshadowing } from './foreshadowing.js'

// 伏笔的紧急度是**派生值**：由 state.json 里的原始字段算出来，而不是读现成的
// 数值。这里的计算错不会报错，只会让甘特图配色和"紧急伏笔 Top5"排序出错——
// 作者据此决定下一章该收哪个伏笔，错判的代价是剧情节奏失衡。

const makeProject = (items, currentChapter = 100) => ({
    progress: { current_chapter: currentChapter },
    plot_threads: { foreshadowing: items },
})

describe('buildForeshadowingRecords — 基础映射', () => {
    it('空输入返回空数组', () => {
        expect(buildForeshadowingRecords(null)).toEqual([])
        expect(buildForeshadowingRecords({})).toEqual([])
    })

    it('非数组的 foreshadowing 字段被忽略', () => {
        expect(buildForeshadowingRecords({ plot_threads: { foreshadowing: 'x' } })).toEqual([])
    })

    it('缺 content 时用占位文本', () => {
        const [record] = buildForeshadowingRecords(makeProject([{}]))
        expect(record.content).toBe('未命名伏笔')
    })

    it('content 优先于 description', () => {
        const [record] = buildForeshadowingRecords(
            makeProject([{ content: '正文', description: '描述' }]),
        )
        expect(record.content).toBe('正文')
    })

    it('id 带下标保证唯一', () => {
        const records = buildForeshadowingRecords(makeProject([{ content: '同' }, { content: '同' }]))
        expect(records[0].id).not.toBe(records[1].id)
    })
})

describe('buildForeshadowingRecords — 章节字段别名', () => {
    it('planted_chapter 优先', () => {
        const [record] = buildForeshadowingRecords(
            makeProject([{ planted_chapter: 10, source_chapter: 20, chapter: 30 }]),
        )
        expect(record.plantedChapter).toBe(10)
    })

    it('退回 source_chapter', () => {
        const [record] = buildForeshadowingRecords(makeProject([{ source_chapter: 20 }]))
        expect(record.plantedChapter).toBe(20)
    })

    it('target_chapter 优先于 due_chapter', () => {
        const [record] = buildForeshadowingRecords(
            makeProject([{ target_chapter: 50, due_chapter: 60 }]),
        )
        expect(record.targetChapter).toBe(50)
    })

    it('0 / 负数 / 空值不当作有效章号', () => {
        const [record] = buildForeshadowingRecords(
            makeProject([{ planted_chapter: 0, target_chapter: -5 }]),
        )
        expect(record.plantedChapter).toBeNull()
        expect(record.targetChapter).toBeNull()
    })
})

describe('buildForeshadowingRecords — 层级', () => {
    it('核心/装饰/其它归一成三种中文', () => {
        const records = buildForeshadowingRecords(makeProject([
            { tier: 'core' }, { tier: '装饰' }, { tier: '支线' },
        ]))
        expect(records.map(r => r.tier)).toEqual(['核心', '装饰', '支线'])
    })

    it('未知层级归为支线', () => {
        const [record] = buildForeshadowingRecords(makeProject([{ tier: '???' }]))
        expect(record.tier).toBe('支线')
    })
})

describe('buildForeshadowingRecords — 紧急度', () => {
    it('已回收优先于其它判定', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { status: '已回收', planted_chapter: 1, target_chapter: 10 },
        ]))
        expect(record.level).toBe('resolved')
    })

    it('越过目标章 → overdue', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { planted_chapter: 10, target_chapter: 50 },
        ], 100))
        expect(record.level).toBe('overdue')
        expect(record.remaining).toBe(-50)
    })

    it('剩余 5 章以内 → urgent', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { planted_chapter: 80, target_chapter: 104 },
        ], 100))
        expect(record.level).toBe('urgent')
        expect(record.remaining).toBe(4)
    })

    it('目标章未到且不急 → active', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { planted_chapter: 90, target_chapter: 200 },
        ], 100))
        expect(record.level).toBe('active')
    })

    it('expected 剩余章数正确', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { planted_chapter: 10, target_chapter: 150 },
        ], 100))
        expect(record.remaining).toBe(50)
        expect(record.elapsed).toBe(90)
    })

    it('目标章早于埋设章时给高分', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { planted_chapter: 50, target_chapter: 40 },
        ], 100))
        // target <= planted → urgencyScore = weight * 2（支线权重 2 → 4）
        expect(record.urgencyScore).toBe(4)
    })
})

describe('buildForeshadowingRecords — 状态文案', () => {
    it('欠 resolved 状态归一：多种写法都识别', () => {
        for (const status of ['已回收', '已完成', 'resolved', 'done', 'DOING'.slice(0, 4)]) {
            const [record] = buildForeshadowingRecords(makeProject([{ status }]))
            if (['已回收', '已完成', 'resolved', 'done'].includes(status)) {
                expect(record.level).toBe('resolved')
            }
        }
    })

    it('状态文本与徽章等级同步', () => {
        const [record] = buildForeshadowingRecords(makeProject([
            { planted_chapter: 10, target_chapter: 50 },
        ], 100))
        expect(record.statusText).toBe('超期')
        expect(record.urgencyText).toBe('critical')
    })
})

describe('summarizeForeshadowing', () => {
    it('空数组全为 0', () => {
        expect(summarizeForeshadowing([])).toEqual({
            total: 0, active: 0, resolved: 0, attention: 0,
        })
    })

    it('总数 = 活跃 + 已回收', () => {
        const records = buildForeshadowingRecords(makeProject([
            { status: '已回收' },
            { planted_chapter: 10, target_chapter: 50 },   // overdue
            { planted_chapter: 90, target_chapter: 300 },  // active
        ], 100))
        const summary = summarizeForeshadowing(records)
        expect(summary.total).toBe(3)
        expect(summary.resolved).toBe(1)
        expect(summary.active).toBe(2)
        expect(summary.active + summary.resolved).toBe(summary.total)
    })

    it('attention 统计 urgent + overdue', () => {
        const records = buildForeshadowingRecords(makeProject([
            { planted_chapter: 10, target_chapter: 50 },    // overdue
            { planted_chapter: 80, target_chapter: 104 },   // urgent
            { planted_chapter: 90, target_chapter: 300 },   // active
            { status: '已回收' },                            // resolved
        ], 100))
        expect(summarizeForeshadowing(records).attention).toBe(2)
    })
})
