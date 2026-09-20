import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import Badge from '../components/Badge.jsx'
import ChartWrapper from '../components/ChartWrapper.jsx'
import { fetchContextHealth, fetchContextHistory } from '../api.js'
import { formatChapterLabel } from '../lib/format.js'

const SECTION_LABELS = {
    core: '核心',
    story_contract: '故事合同',
    runtime_status: '运行态',
    latest_commit: '最新提交',
    prewrite_validation: '预写验证',
    scene: '场景',
    global: '全局',
    user_prompts: '自定义提示词',
    reader_signal: '读者信号',
    genre_profile: '题材档案',
    writing_guidance: '写作指导',
    override_hints: '覆盖提示',
    plot_structure: '剧情结构',
    story_skeleton: '故事骨架',
    memory: '记忆',
    long_term_memory: '长期记忆',
    preferences: '偏好',
    alerts: '告警',
}

function buildWeightsOption(weights) {
    const entries = Object.entries(weights || {}).filter(([, v]) => v > 0)
    if (!entries.length) return null

    return {
        tooltip: { trigger: 'item', formatter: '{b}: {d}%' },
        legend: { bottom: 0, type: 'scroll' },
        series: [{
            type: 'pie',
            radius: ['35%', '65%'],
            avoidLabelOverlap: false,
            itemStyle: { borderColor: '#2a220f', borderWidth: 2 },
            label: {
                show: true,
                formatter: '{b}\n{d}%',
                color: '#5d5035',
                fontSize: 11,
                fontWeight: 600,
            },
            data: entries.map(([name, value]) => ({
                name: SECTION_LABELS[name] || name,
                value: Math.round(value * 100),
            })),
        }],
    }
}

function buildTokensOption(sectionTokens) {
    const entries = Object.entries(sectionTokens || {})
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12)
    if (!entries.length) return null

    return {
        tooltip: { trigger: 'axis' },
        grid: { left: 120, right: 24, top: 12, bottom: 36 },
        xAxis: {
            type: 'value',
            axisLabel: { formatter: v => `${v}` },
        },
        yAxis: {
            type: 'category',
            data: entries.map(([name]) => SECTION_LABELS[name] || name).reverse(),
            axisLabel: { fontSize: 11, fontWeight: 600, color: '#5d5035' },
        },
        series: [{
            type: 'bar',
            data: entries.map(([, v]) => v).reverse(),
            barWidth: '56%',
            itemStyle: {
                color: '#26a8ff',
                borderColor: '#2a220f',
                borderWidth: 2,
            },
        }],
    }
}

function buildHistoryOption(items) {
    return {
        tooltip: { trigger: 'axis' },
        legend: { bottom: 0 },
        grid: { left: 52, right: 24, top: 36, bottom: 46 },
        xAxis: {
            type: 'category',
            data: items.map(item => item.chapter),
            axisLabel: { interval: Math.max(1, Math.floor(items.length / 10)), formatter: v => `${v}` },
        },
        yAxis: {
            type: 'value',
            min: 0,
        },
        series: [
            {
                name: '已包含',
                type: 'line',
                data: items.map(item => item.included_count),
                lineStyle: { width: 3, color: '#2ec27e' },
                itemStyle: { color: '#2ec27e', borderColor: '#2a220f', borderWidth: 2 },
                symbol: 'rect',
                symbolSize: 6,
            },
            {
                name: '已排除',
                type: 'line',
                data: items.map(item => item.excluded_count),
                lineStyle: { width: 2, color: '#8f7f5c', type: 'dashed' },
                itemStyle: { color: '#8f7f5c', borderColor: '#2a220f', borderWidth: 2 },
                symbol: 'rect',
                symbolSize: 6,
            },
            {
                name: '关键排除',
                type: 'bar',
                data: items.map(item => item.critical_excluded_count),
                itemStyle: {
                    color: params => params.value > 0 ? '#d7263d' : 'transparent',
                    borderColor: '#2a220f',
                    borderWidth: 1,
                },
                barWidth: '40%',
            },
        ],
    }
}

function HealthScore({ score }) {
    const tone = score >= 80 ? 'green' : score >= 60 ? 'amber' : 'red'
    return <Badge tone={tone}>{score} 分</Badge>
}

export default function ContextHealthPage() {
    const { projectInfo, refreshToken } = useDashboardContext()
    const [health, setHealth] = useState(null)
    const [history, setHistory] = useState({ items: [] })
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    const currentChapter = Number(projectInfo?.progress?.current_chapter || 0)
    const [selectedChapter, setSelectedChapter] = useState(currentChapter)

    useEffect(() => {
        if (currentChapter > 0 && selectedChapter === 0) {
            setSelectedChapter(currentChapter)
        }
    }, [currentChapter, selectedChapter])

    // history 只在 refreshToken 变化时刷新
    useEffect(() => {
        fetchContextHistory(20)
            .then(data => setHistory(data && typeof data === 'object' ? data : { items: [] }))
            .catch(e => {
                // 空 catch 会静默吞掉历史加载失败——记录到 error 区让用户可感知
                setHistory({ items: [] })
                setError(prev => prev || `上下文历史加载失败：${e.message || '网络错误'}`)
            })
    }, [refreshToken])

    // health 在 selectedChapter 或 refreshToken 变化时刷新
    useEffect(() => {
        if (selectedChapter <= 0) return
        setLoading(true)
        setError(null)
        fetchContextHealth(selectedChapter)
            .then(data => {
                setHealth(data)
                if (!data) setError('暂无上下文数据')
            })
            .catch(() => {
                setHealth(null)
                setError('暂无上下文数据')
            })
            .finally(() => setLoading(false))
    }, [selectedChapter, refreshToken])

    const weightsOption = useMemo(() => buildWeightsOption(health?.weights_used), [health])
    const tokensOption = useMemo(() => buildTokensOption(health?.section_tokens), [health])
    const historyOption = useMemo(() => buildHistoryOption(history.items || []), [history])

    const chapterOptions = useMemo(() => {
        const chapters = new Set()
        for (const item of (history.items || [])) {
            if (item.chapter > 0) chapters.add(item.chapter)
        }
        if (currentChapter > 0) chapters.add(currentChapter)
        return [...chapters].sort((a, b) => a - b)
    }, [history, currentChapter])

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>上下文健康</h2>
                {health && <HealthScore score={health.health_score} />}
            </header>

            <div style={{ marginBottom: 16 }}>
                <select
                    value={selectedChapter}
                    onChange={e => setSelectedChapter(Number(e.target.value))}
                    style={{
                        padding: '6px 10px',
                        border: '2px solid var(--border-main)',
                        borderRadius: 0,
                        background: 'var(--bg-card-2)',
                        color: 'var(--text-main)',
                        fontWeight: 700,
                        minWidth: 200,
                    }}
                >
                    {chapterOptions.map(ch => (
                        <option key={ch} value={ch}>{formatChapterLabel(ch)}</option>
                    ))}
                </select>
            </div>

            {loading && <div className="empty-state">加载中...</div>}
            {error && <div className="empty-state" style={{ color: 'var(--accent-red)' }}>加载失败: {error}</div>}

            {!loading && !error && health && (
                <>
                    {/* 概览卡片 */}
                    <div className="stat-grid">
                        <article className="card stat-card">
                            <span className="stat-label">阶段</span>
                            <span className="stat-value plain">{health.stage}</span>
                            <span className="stat-sub">模板: {health.template}</span>
                        </article>
                        <article className="card stat-card">
                            <span className="stat-label">已包含</span>
                            <span className="stat-value">{health.included.length}</span>
                            <span className="stat-sub">section</span>
                        </article>
                        <article className="card stat-card">
                            <span className="stat-label">已排除</span>
                            <span className="stat-value" style={{ color: health.excluded.length > 0 ? 'var(--accent-amber)' : 'var(--accent-green)' }}>
                                {health.excluded.length}
                            </span>
                            <span className="stat-sub">section</span>
                        </article>
                        <article className="card stat-card">
                            <span className="stat-label">估算 Token</span>
                            <span className="stat-value plain">{health.total_tokens.toLocaleString()}</span>
                            <span className="stat-sub">粗略估算</span>
                        </article>
                    </div>

                    {/* 关键排除告警 */}
                    {health.critical_excluded.length > 0 && (
                        <div style={{ padding: '12px 16px', background: 'var(--bg-card)', border: '2px solid var(--accent-red)', marginBottom: 16 }}>
                            <div style={{ fontWeight: 700, color: 'var(--accent-red)', marginBottom: 4 }}>
                                ⚠ 关键 section 被排除
                            </div>
                            <div style={{ fontSize: 13, color: '#e0e0e0' }}>
                                以下关键 section 未出现在上下文中，可能导致 AI 遗忘重要信息：
                                {health.critical_excluded.map(s => (
                                    <Badge key={s} tone="red" style={{ marginLeft: 4 }}>{SECTION_LABELS[s] || s}</Badge>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Section 状态 */}
                    <div className="content-grid two-columns">
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">已包含 Section</span>
                                <Badge tone="green">{health.included.length}</Badge>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                {health.included.map(s => (
                                    <Badge key={s} tone="blue">
                                        {SECTION_LABELS[s] || s}
                                    </Badge>
                                ))}
                            </div>
                        </article>

                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">已排除 Section</span>
                                <Badge tone={health.excluded.length > 0 ? 'amber' : 'green'}>{health.excluded.length}</Badge>
                            </div>
                            {health.excluded.length === 0 ? (
                                <div className="empty-state compact">无排除的 section</div>
                            ) : (
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                    {health.excluded.map(s => (
                                        <Badge key={s} tone={health.critical_excluded.includes(s) ? 'red' : 'amber'}>
                                            {SECTION_LABELS[s] || s}
                                            {health.critical_excluded.includes(s) && ' ⚠'}
                                        </Badge>
                                    ))}
                                </div>
                            )}
                        </article>
                    </div>

                    {/* 图表 */}
                    <div className="content-grid two-columns">
                        {weightsOption && (
                            <article className="card">
                                <div className="card-header">
                                    <span className="card-title">权重分布</span>
                                    <Badge tone="purple">{health.template}</Badge>
                                </div>
                                <ChartWrapper option={weightsOption} height={280} />
                            </article>
                        )}

                        {tokensOption && (
                            <article className="card">
                                <div className="card-header">
                                    <span className="card-title">Token 估算（按 Section）</span>
                                    <Badge tone="blue">Top 12</Badge>
                                </div>
                                <ChartWrapper option={tokensOption} height={280} />
                            </article>
                        )}
                    </div>

                    {/* 历史趋势 */}
                    {history.items.length > 0 && (
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">历史趋势</span>
                                <Badge tone="cyan">{history.items.length} 章</Badge>
                            </div>
                            <ChartWrapper option={historyOption} loading={loading} />
                        </article>
                    )}
                </>
            )}

            {!loading && !error && !health && selectedChapter > 0 && (
                <div className="empty-state">
                    <p>第 {selectedChapter} 章暂无上下文数据。需要先执行一次写入操作生成 trace 文件。</p>
                </div>
            )}
        </section>
    )
}
