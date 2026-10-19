// ECharts 的函数式 tooltip formatter 返回值直接进 innerHTML，不做转义
// （只有字符串模板形式才走 encodeHTML）。小说正文经抽取后会进入实体名、
// 关系描述与伏笔文本，因此拼进 tooltip 的动态值必须先转义。
const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

export function escapeHtml(value) {
    if (value === null || value === undefined) return ''
    return String(value).replace(/[&<>"']/g, ch => HTML_ESCAPES[ch])
}

// `value || 0` 会把 null / undefined / '' / false 一律变成 0，于是
// 「这一章还没有审查分」会被显示成「0 分」——读者/作者会以为是真实测量值。
// 缺值必须显示占位符，只有**真正的数字 0** 才显示 0。
function toFiniteNumber(value) {
    if (value === null || value === undefined || value === '') return null
    if (typeof value === 'boolean') return null
    const number = Number(value)
    return Number.isFinite(number) ? number : null
}

export function formatNumber(value) {
    const number = toFiniteNumber(value)
    if (number === null) return '—'
    if (Math.abs(number) >= 10000) {
        return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number / 10000)} 万`
    }
    return new Intl.NumberFormat('zh-CN').format(number)
}

export function formatShortNumber(value) {
    const number = toFiniteNumber(value)
    if (number === null) return '—'
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number)
}

export function formatPercent(value, fractionDigits = 1) {
    const number = Number(value)
    if (!Number.isFinite(number)) return '—'
    return `${number.toFixed(fractionDigits)}%`
}

export function formatChapterLabel(chapter) {
    const number = Number(chapter || 0)
    if (!Number.isFinite(number) || number <= 0) return '—'
    return `第 ${number} 章`
}

export function formatDateTime(value) {
    if (!value) return '—'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return String(value)
    return date.toLocaleString('zh-CN', {
        hour12: false,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    })
}

export function formatJSONText(value) {
    if (value === null || value === undefined || value === '') return ''
    if (typeof value === 'object') {
        return JSON.stringify(value, null, 2)
    }
    if (typeof value !== 'string') {
        return String(value)
    }
    try {
        return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
        return value
    }
}

export function average(values) {
    // 必须走 toFiniteNumber 而不是裸 Number()：Number(null) === 0 且
    // Number.isFinite(0) 为真，于是「没测量的章节」会被当成 0 分拖低均值。
    // 均值会显示在总览/节奏页的统计卡上，是作者判断趋势的依据。
    const valid = (values || [])
        .map(toFiniteNumber)
        .filter(item => item !== null)
    if (!valid.length) return null
    return valid.reduce((sum, item) => sum + item, 0) / valid.length
}

export function formatTableValue(value) {
    if (value === null || value === undefined || value === '') return '—'
    if (Array.isArray(value)) {
        return value.length ? value.join('、') : '—'
    }
    if (typeof value === 'object') {
        return JSON.stringify(value, null, 2)
    }
    if (typeof value === 'boolean') {
        return value ? '是' : '否'
    }
    return String(value)
}
