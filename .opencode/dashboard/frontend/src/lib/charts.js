// ⚠️ echarts 是**按需注册**的：echarts.use() 就是一份白名单，
// 没在这里注册的图表类型，页面里写了 `type: 'xxx'` 也不会画出来——
// 而且**不报错**，只留一个空白画布。新增图表类型时必须同时加进这里，
// 否则会重演 2026-10 的雷达图空白事故（用了 type:'radar' 但缺 RadarChart）。
import { BarChart, BoxplotChart, CustomChart, GraphChart, LineChart, PieChart, RadarChart } from 'echarts/charts'
import {
    GridComponent,
    LegendComponent,
    MarkLineComponent,
    RadarComponent,
    TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import * as echarts from 'echarts/core'

echarts.use([
    LineChart,
    BarChart,
    PieChart,
    BoxplotChart,
    GraphChart,
    CustomChart,
    RadarChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    MarkLineComponent,
    RadarComponent,
    CanvasRenderer,
])

export { echarts }

const PIXEL_THEME_NAME = 'pixel'
const PIXEL_DARK_THEME_NAME = 'pixel-dark'

const PIXEL_THEME = {
    color: ['#26a8ff', '#f5a524', '#7f5af0', '#2ec27e', '#d7263d', '#00b8d4', '#ff5c8a'],
    backgroundColor: 'transparent',
    textStyle: {
        fontFamily: "'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        color: '#2a220f',
    },
    title: {
        textStyle: {
            fontFamily: "'Press Start 2P', monospace",
            fontSize: 11,
            color: '#2a220f',
        },
    },
    legend: {
        textStyle: {
            color: '#5d5035',
            fontSize: 13,
            fontWeight: 600,
        },
    },
    tooltip: {
        backgroundColor: '#fffaf0',
        borderColor: '#2a220f',
        borderWidth: 2,
        textStyle: {
            color: '#2a220f',
            fontSize: 13,
        },
        extraCssText: 'border-radius:0;box-shadow:3px 3px 0 #2a220f;',
    },
    categoryAxis: {
        axisLine: { lineStyle: { color: '#8f7f5c', width: 2 } },
        axisTick: { lineStyle: { color: '#8f7f5c' } },
        axisLabel: { color: '#8f7f5c', fontSize: 12 },
        splitLine: { lineStyle: { color: '#e8dcc4', type: 'dashed' } },
    },
    valueAxis: {
        axisLine: { lineStyle: { color: '#8f7f5c', width: 2 } },
        axisTick: { lineStyle: { color: '#8f7f5c' } },
        axisLabel: { color: '#8f7f5c', fontSize: 12 },
        splitLine: { lineStyle: { color: '#e8dcc4', type: 'dashed' } },
    },
    grid: { left: 50, right: 24, top: 30, bottom: 48 },
}

const PIXEL_DARK_THEME = {
    ...PIXEL_THEME,
    color: ['#6ccbf0', '#f0c040', '#b994f0', '#5cdb8b', '#f05050', '#60d8c0', '#ff6b90'],
    textStyle: { ...PIXEL_THEME.textStyle, color: '#eee5d8' },
    title: { textStyle: { ...PIXEL_THEME.title.textStyle, color: '#eee5d8' } },
    legend: { textStyle: { ...PIXEL_THEME.legend.textStyle, color: '#c4b9a8' } },
    tooltip: {
        backgroundColor: '#2d2822',
        borderColor: '#5c5245',
        borderWidth: 2,
        textStyle: { color: '#eee5d8', fontSize: 13 },
        extraCssText: 'border-radius:0;box-shadow:3px 3px 0 #0d0b09;',
    },
    categoryAxis: {
        axisLine: { lineStyle: { color: '#5c5245', width: 2 } },
        axisTick: { lineStyle: { color: '#5c5245' } },
        axisLabel: { color: '#8c8275', fontSize: 12 },
        splitLine: { lineStyle: { color: '#353028', type: 'dashed' } },
    },
    valueAxis: {
        axisLine: { lineStyle: { color: '#5c5245', width: 2 } },
        axisTick: { lineStyle: { color: '#5c5245' } },
        axisLabel: { color: '#8c8275', fontSize: 12 },
        splitLine: { lineStyle: { color: '#353028', type: 'dashed' } },
    },
}

let themeRegistered = false

export const STRAND_COLORS = {
    quest: '#26a8ff',
    fire: '#ff5c8a',
    constellation: '#7f5af0',
}

export const FORESHADOWING_COLORS = {
    overdue: '#d7263d',
    urgent: '#f5a524',
    active: '#26a8ff',
    resolved: '#2ec27e',
}

export function ensurePixelTheme() {
    if (themeRegistered) return
    echarts.registerTheme(PIXEL_THEME_NAME, PIXEL_THEME)
    echarts.registerTheme(PIXEL_DARK_THEME_NAME, PIXEL_DARK_THEME)
    themeRegistered = true
}

export function getChartTheme() {
    try {
        const theme = document.documentElement.getAttribute('data-theme')
        return theme === 'dark' ? PIXEL_DARK_THEME_NAME : PIXEL_THEME_NAME
    } catch {
        return PIXEL_THEME_NAME
    }
}

function quantile(sortedValues, ratio) {
    if (!sortedValues.length) return 0
    const index = (sortedValues.length - 1) * ratio
    const lower = Math.floor(index)
    const upper = Math.ceil(index)
    if (lower === upper) return sortedValues[lower]
    const weight = index - lower
    return sortedValues[lower] * (1 - weight) + sortedValues[upper] * weight
}

export function buildBoxplotData(groups) {
    return groups.map(group => {
        const values = [...group.values]
            .map(item => Number(item))
            .filter(item => Number.isFinite(item))
            .sort((left, right) => left - right)

        if (!values.length) return [0, 0, 0, 0, 0]

        return [
            values[0],
            quantile(values, 0.25),
            quantile(values, 0.5),
            quantile(values, 0.75),
            values[values.length - 1],
        ].map(item => Math.round(item))
    })
}
