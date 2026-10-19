import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import Badge from '../components/Badge.jsx'
import ChartWrapper from '../components/ChartWrapper.jsx'
import { fetchReviewAnalytics } from '../api.js'

const DIMENSION_LABELS = {
    continuity: '叙事连贯',
    setting: '设定一致性',
    character: '角色一致性',
    timeline: '时间线',
    ai_flavor: 'AI味',
    logic: '逻辑',
    pacing: '节奏',
    other: '其他',
}

function buildRadarOption(averages) {
    if (!averages) return null
    const dims = Object.keys(averages)
    if (!dims.length) return null

    return {
        tooltip: {},
        radar: {
            indicator: dims.map(d => ({
                name: DIMENSION_LABELS[d] || d,
                max: 100,
            })),
            shape: 'polygon',
            splitArea: { show: true },
            axisName: { color: '#5d5035', fontSize: 11, fontWeight: 600 },
        },
        series: [{
            type: 'radar',
            data: [{
                value: dims.map(d => Math.round(averages[d] * 10) / 10),
                name: '平均分',
                areaStyle: { color: 'rgba(38, 168, 255, 0.2)' },
                lineStyle: { color: '#26a8ff', width: 2 },
                itemStyle: { color: '#26a8ff', borderColor: '#2a220f', borderWidth: 2 },
            }],
        }],
    }
}

function buildTrendOption(trends) {
    if (!trends) return null
    const dims = Object.keys(trends)
    if (!dims.length) return null

    // 收集所有章节并排序
    const allChapters = new Set()
    for (const points of Object.values(trends)) {
        for (const p of points) allChapters.add(p.chapter)
    }
    const chapters = [...allChapters].sort((a, b) => a - b)

    const colors = ['#26a8ff', '#7f5af0', '#2ec27e', '#f5a524', '#d7263d', '#00b8d4', '#ff6b6b', '#ffd93d']

    return {
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0, type: 'scroll' },
        grid: { left: 52, right: 24, top: 36, bottom: 46 },
        xAxis: {
            type: 'category',
            data: chapters,
            axisLabel: { interval: Math.max(1, Math.floor(chapters.length / 10)), formatter: v => `${v}` },
        },
        yAxis: {
            type: 'value',
            min: 0,
            max: 100,
        },
        series: dims.map((dim, i) => {
            const pointMap = new Map(trends[dim].map(p => [p.chapter, p.score]))
            return {
                name: DIMENSION_LABELS[dim] || dim,
                type: 'line',
                data: chapters.map(ch => pointMap.get(ch) ?? null),
                lineStyle: { width: 2, color: colors[i % colors.length] },
                itemStyle: { color: colors[i % colors.length], borderColor: '#2a220f', borderWidth: 1 },
                symbol: 'circle',
                symbolSize: 4,
                connectNulls: true,
            }
        }),
    }
}

function buildSeverityOption(severityTotals) {
    const entries = Object.entries(severityTotals || {}).filter(([, v]) => v > 0)
    if (!entries.length) return null

    const colorMap = { critical: '#d7263d', high: '#f5a524', medium: '#26a8ff', low: '#8f7f5c' }

    return {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        series: [{
            type: 'pie',
            radius: ['35%', '65%'],
            avoidLabelOverlap: false,
            itemStyle: { borderColor: '#2a220f', borderWidth: 2 },
            label: {
                show: true,
                formatter: '{b}\n{c}',
                color: '#5d5035',
                fontSize: 12,
                fontWeight: 600,
            },
            data: entries.map(([name, value]) => ({
                name,
                value,
                itemStyle: { color: colorMap[name] || '#8f7f5c' },
            })),
        }],
    }
}

function scoreTone(score) {
    if (score >= 80) return 'green'
    if (score >= 60) return 'amber'
    return 'red'
}

export default function ReviewAnalyticsPage() {
    const { refreshToken } = useDashboardContext()
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        setLoading(true)
        setError(null)
        fetchReviewAnalytics(50)
            .then(setData)
            .catch(e => setError(e.message))
            .finally(() => setLoading(false))
    }, [refreshToken])

    const radarOption = useMemo(() => buildRadarOption(data?.dimension_averages), [data])
    const trendOption = useMemo(() => buildTrendOption(data?.dimension_trends), [data])
    const severityOption = useMemo(() => buildSeverityOption(data?.severity_totals), [data])

    // 维度数是**数据驱动**的，不是固定 8 个：旧书可能只跑过 plot/pacing/character
    // 三个维度，硬写「8 维度」会与图上的实际顶点数不符（2026-10 实测暴露）。
    const dimensionCount = data?.dimension_averages
        ? Object.keys(data.dimension_averages).length
        : 0

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>审查分析</h2>
                {data && <Badge tone="blue">{data.total_reviews} 次审查</Badge>}
            </header>

            {loading && <div className="empty-state">加载中...</div>}
            {error && <div className="empty-state" style={{ color: 'var(--accent-red)' }}>加载失败: {error}</div>}

            {!loading && !error && data && (
                <>
                    {/* 最薄弱维度 */}
                    {data.weakest_dimensions?.length > 0 && (
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                            <span style={{ fontWeight: 700, lineHeight: '28px' }}>最薄弱维度：</span>
                            {data.weakest_dimensions.map(w => (
                                <Badge key={w.dimension} tone={scoreTone(w.avg_score)}>
                                    {DIMENSION_LABELS[w.dimension] || w.dimension}: {Math.round(w.avg_score * 10) / 10}
                                </Badge>
                            ))}
                        </div>
                    )}

                    {/* 雷达图 + 严重程度分布 */}
                    <div className="content-grid two-columns">
                        {radarOption && (
                            <article className="card">
                                <div className="card-header">
                                    <span className="card-title">维度雷达图</span>
                                    <Badge tone="purple">{dimensionCount} 维度</Badge>
                                </div>
                                <ChartWrapper option={radarOption} height={320} />
                            </article>
                        )}

                        {severityOption && (
                            <article className="card">
                                <div className="card-header">
                                    <span className="card-title">严重程度分布</span>
                                    <Badge tone="amber">累计</Badge>
                                </div>
                                <ChartWrapper option={severityOption} height={320} />
                            </article>
                        )}
                    </div>

                    {/* 维度趋势 */}
                    {trendOption && (
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">维度得分趋势</span>
                                <Badge tone="green">最近 50 次审查</Badge>
                            </div>
                            <ChartWrapper option={trendOption} loading={loading} />
                        </article>
                    )}

                    {/* Critical Issues */}
                    {data.critical_issues?.length > 0 && (
                        <article className="card" style={{ borderColor: 'var(--accent-red)' }}>
                            <div className="card-header">
                                <span className="card-title" style={{ color: 'var(--accent-red)' }}>Critical Issues</span>
                                <Badge tone="red">{data.critical_issues.length} 条</Badge>
                            </div>
                            <ul style={{ margin: 0, paddingLeft: 18 }}>
                                {data.critical_issues.map((issue, i) => (
                                    <li key={i} style={{ marginBottom: 6, color: 'var(--accent-red)', fontWeight: 500 }}>
                                        {issue}
                                    </li>
                                ))}
                            </ul>
                        </article>
                    )}
                </>
            )}

            {!loading && !error && !data && (
                <div className="empty-state">
                    <p>暂无审查数据。需要先执行至少一次审查。</p>
                </div>
            )}
        </section>
    )
}
