import { describe, expect, it } from 'vitest'
import {
    average,
    escapeHtml,
    formatChapterLabel,
    formatJSONText,
    formatNumber,
    formatPercent,
    formatShortNumber,
    formatTableValue,
} from './format.js'

// 这些函数被 9 个页面共用，任何一处行为变化都会同时影响多处展示。
// 重点是**异常输入**：数据库里取出来的值可能是 null、空串、字符串数字、
// 甚至 'null' 字面量，格式化函数崩掉会让整页白屏（React 渲染期抛错）。

describe('escapeHtml', () => {
    it('转义五个 HTML 危险字符', () => {
        expect(escapeHtml('<script>"&\'')).toBe('&lt;script&gt;&quot;&amp;&#39;')
    })

    it('对 null/undefined 返回空串而不是 "null"', () => {
        expect(escapeHtml(null)).toBe('')
        expect(escapeHtml(undefined)).toBe('')
    })

    it('非字符串先转字符串', () => {
        expect(escapeHtml(42)).toBe('42')
    })
})

describe('formatNumber', () => {
    it('一万以上折成"万"', () => {
        expect(formatNumber(12345)).toBe('1.2 万')
    })

    it('一万以下按千分位', () => {
        expect(formatNumber(1234)).toBe('1,234')
    })

    it('非数字退化成占位符而不是 NaN', () => {
        expect(formatNumber('abc')).toBe('—')
        expect(formatNumber(null)).toBe('—')
        expect(formatNumber(undefined)).toBe('—')
    })

    it('0 显示为 0，不能变成占位符', () => {
        expect(formatNumber(0)).toBe('0')
    })
})

describe('formatShortNumber', () => {
    it('保留一位小数且不折万', () => {
        expect(formatShortNumber(12345)).toBe('12,345')
    })

    it('小数正确四舍五入到一位', () => {
        expect(formatShortNumber(2.456)).toBe('2.5')
    })

    it('非法输入退化成占位符', () => {
        expect(formatShortNumber('')).toBe('—')
    })
})

describe('formatPercent', () => {
    it('默认一位小数', () => {
        expect(formatPercent(65.4321)).toBe('65.4%')
    })

    it('可指定小数位', () => {
        expect(formatPercent(65.4321, 2)).toBe('65.43%')
    })

    it('非法输入退化成占位符', () => {
        expect(formatPercent('x')).toBe('—')
    })
})

describe('formatChapterLabel', () => {
    it('正常章号带"第 N 章"', () => {
        expect(formatChapterLabel(12)).toBe('第 12 章')
    })

    it('0 / 负数 / 空值都退化成占位符', () => {
        // 0 章不存在，显示"第 0 章"会误导
        expect(formatChapterLabel(0)).toBe('—')
        expect(formatChapterLabel(-3)).toBe('—')
        expect(formatChapterLabel(null)).toBe('—')
    })

    it('字符串数字可解析', () => {
        expect(formatChapterLabel('88')).toBe('第 88 章')
    })
})

describe('average', () => {
    it('忽略非数字项', () => {
        expect(average([10, '20', null, undefined, 'x', 30])).toBe(20)
    })

    it('空数组返回 null 而不是 NaN', () => {
        expect(average([])).toBeNull()
    })

    it('全是非法值时返回 null', () => {
        expect(average(['a', null])).toBeNull()
    })

    it('单个值等于其自身', () => {
        expect(average([42])).toBe(42)
    })
})

describe('formatTableValue', () => {
    it('空值显示破折号', () => {
        expect(formatTableValue(null)).toBe('—')
        expect(formatTableValue('')).toBe('—')
    })

    it('数组用顿号连接', () => {
        expect(formatTableValue(['甲', '乙'])).toBe('甲、乙')
    })

    it('空数组也显示破折号', () => {
        expect(formatTableValue([])).toBe('—')
    })

    it('布尔值转中文', () => {
        expect(formatTableValue(true)).toBe('是')
        expect(formatTableValue(false)).toBe('否')
    })

    it('对象序列化', () => {
        expect(formatTableValue({ a: 1 })).toContain('"a"')
    })
})

describe('formatJSONText', () => {
    it('JSON 字符串被美化', () => {
        expect(formatJSONText('{"a":1}')).toContain('\n')
    })

    it('非法 JSON 原样返回', () => {
        expect(formatJSONText('不是 json')).toBe('不是 json')
    })

    it('空值返回空串', () => {
        expect(formatJSONText(null)).toBe('')
    })

    it('对象直接序列化', () => {
        expect(formatJSONText({ a: 1 })).toContain('"a": 1')
    })
})
