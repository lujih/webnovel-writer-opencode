import { describe, expect, it } from 'vitest'
import { buildBoxplotData, FORESHADOWING_COLORS, STRAND_COLORS } from './charts.js'

// buildBoxplotData 把每卷字数转成 ECharts 箱线图要求的五数概括
// [min, Q1, median, Q3, max]。分位数算错不会报错，只会让箱子画歪——
// 作者看到的是"节奏分布异常"的假象，因此必须钉住。

describe('buildBoxplotData', () => {
    it('返回五个数', () => {
        const result = buildBoxplotData([{ values: [1, 2, 3, 4, 5] }])
        expect(result).toHaveLength(1)
        expect(result[0]).toHaveLength(5)
    })

    it('单值组五个数全部相同', () => {
        const [five] = buildBoxplotData([{ values: [42] }])
        expect(five).toEqual([42, 42, 42, 42, 42])
    })

    it('中位数取中间值', () => {
        const [five] = buildBoxplotData([{ values: [1, 2, 3, 4, 5] }])
        expect(five[2]).toBe(3)
    })

    it('min/max 是端点', () => {
        const [five] = buildBoxplotData([{ values: [10, 20, 30, 40, 50] }])
        expect(five[0]).toBe(10)
        expect(five[4]).toBe(50)
    })

    it('乱序输入先排序', () => {
        const [five] = buildBoxplotData([{ values: [5, 1, 3, 2, 4] }])
        expect(five[0]).toBe(1)
        expect(five[4]).toBe(5)
        expect(five[2]).toBe(3)
    })

    it('过滤非数字后仍能计算', () => {
        const [five] = buildBoxplotData([{ values: [1, 'x', 3, null, 5] }])
        // null → 0 会被当有效数字吗？这里断言不会崩溃即可，
        // 关键是 min <= max 且都是有限数
        expect(five.every(v => Number.isFinite(v))).toBe(true)
        expect(five[0]).toBeLessThanOrEqual(five[4])
    })

    it('空组退化为全 0 而不是 NaN', () => {
        const [five] = buildBoxplotData([{ values: [] }])
        expect(five).toEqual([0, 0, 0, 0, 0])
    })

    it('全是非法值时退化为全 0', () => {
        const [five] = buildBoxplotData([{ values: ['a', 'b'] }])
        expect(five).toEqual([0, 0, 0, 0, 0])
    })

    it('结果取整', () => {
        const [five] = buildBoxplotData([{ values: [1.4, 2.6, 3.5, 4.4, 5.9] }])
        expect(five.every(v => Number.isInteger(v))).toBe(true)
    })

    it('多组按顺序返回', () => {
        const result = buildBoxplotData([
            { values: [1, 2] },
            { values: [10, 20] },
        ])
        expect(result).toHaveLength(2)
        expect(result[0][0]).toBe(1)
        expect(result[1][0]).toBe(10)
    })

    it('不修改入参', () => {
        const values = [3, 1, 2]
        buildBoxplotData([{ values }])
        expect(values).toEqual([3, 1, 2])
    })
})

describe('配色常量', () => {
    it('三个 strand 颜色齐全', () => {
        expect(Object.keys(STRAND_COLORS).sort()).toEqual(['constellation', 'fire', 'quest'])
    })

    it('四种伏笔状态颜色齐全', () => {
        expect(Object.keys(FORESHADOWING_COLORS).sort())
            .toEqual(['active', 'overdue', 'resolved', 'urgent'])
    })

    it('颜色均为合法十六进制', () => {
        const all = [...Object.values(STRAND_COLORS), ...Object.values(FORESHADOWING_COLORS)]
        for (const color of all) {
            expect(color).toMatch(/^#[0-9a-fA-F]{6}$/)
        }
    })
})
