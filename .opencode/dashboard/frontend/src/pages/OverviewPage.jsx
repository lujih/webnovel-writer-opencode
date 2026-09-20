import { Fragment, useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import { fetchChapterTrend, fetchChapters, fetchStoryRuntimeHealth, fetchForeshadowingReminders, fetchJSON } from '../api.js'
import Badge from '../components/Badge.jsx'
import ChartWrapper from '../components/ChartWrapper.jsx'
import DataTable from '../components/DataTable.jsx'
import Pager from '../components/Pager.jsx'
import { STRAND_COLORS } from '../lib/charts.js'
import { buildForeshadowingRecords, summarizeForeshadowing } from '../lib/foreshadowing.js'
import {
    average,
    formatChapterLabel,
    formatNumber,
    formatPercent,
    formatShortNumber,
} from '../lib/format.js'
import { groupChaptersByVolume } from '../lib/story.js'

const WINDOW_SIZE = 50

const FORESHADOW_PRIORITY = {
    overdue: 4,
    urgent: 3,
    active: 2,
    resolved: 1,
}

function toneForForeshadowing(level) {
    if (level === 'overdue') return 'red'
    if (level === 'urgent') return 'amber'
    if (level === 'resolved') return 'green'
    return 'blue'
}

function toneForHookStrength(strength) {
    if (strength === 'strong') return 'green'
    if (strength === 'medium') return 'amber'
    if (strength === 'weak') return 'red'
    return 'blue'
}

function toneForUrgencyBadge(level) {
    if (level === 'critical') return 'red'
    if (level === 'high') return 'amber'
    if (level === 'medium') return 'amber'
    if (level === 'resolved') return 'green'
    return 'blue'
}

function formatRuntimeText(runtimeHealth) {
    if (!runtimeHealth) return '运行态未加载'
    const fallback = Array.isArray(runtimeHealth.fallback_sources) && runtimeHealth.fallback_sources.length
        ? runtimeHealth.fallback_sources.join(' / ')
        : 'none'
    return `${runtimeHealth.latest_commit_status || 'missing'} · fallback ${fallback}`
}

function buildReviewOption(items) {
    const scores = items
        .map(item => Number(item.review_score))
        .filter(score => Number.isFinite(score))
    const averageScore = average(scores)

    return {
        tooltip: { trigger: 'axis' },
        grid: { left: 52, right: 24, top: 36, bottom: 46 },
        xAxis: {
            type: 'category',
            data: items.map(item => item.chapter),
            axisLabel: { interval: 9, formatter: value => `${value}` },
        },
        yAxis: {
            type: 'value',
            min: 0,
            max: Math.max(100, ...scores, 0),
        },
        series: [
            {
                type: 'line',
                name: '审查得分',
                data: items.map(item => item.review_score ?? null),
                symbol: 'rect',
                symbolSize: 8,
                lineStyle: { width: 3, color: '#26a8ff' },
                itemStyle: {
                    color: '#26a8ff',
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
                connectNulls: false,
                markLine: averageScore
                    ? {
                        symbol: 'none',
                        lineStyle: { color: '#f5a524', width: 2, type: 'dashed' },
                        label: { formatter: `均值 ${formatShortNumber(averageScore)}` },
                        data: [{ yAxis: Number(averageScore.toFixed(2)) }],
                    }
                    : undefined,
            },
        ],
    }
}

function buildVolumeOption(groups) {
    return {
        tooltip: { trigger: 'axis' },
        xAxis: {
            type: 'category',
            data: groups.map(group => group.label),
        },
        yAxis: {
            type: 'value',
            axisLabel: {
                formatter: value => `${formatShortNumber(Number(value) / 10000)}万`,
            },
        },
        series: [
            {
                type: 'bar',
                data: groups.map(group => group.totalWords),
                barWidth: '56%',
                itemStyle: {
                    color: '#7f5af0',
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
                label: {
                    show: true,
                    position: 'top',
                    formatter: params => formatNumber(params.value),
                    color: '#5d5035',
                    fontSize: 12,
                },
            },
        ],
    }
}

function buildStrandOption(entries) {
    return {
        tooltip: { trigger: 'item' },
        legend: { bottom: 0 },
        series: [
            {
                type: 'pie',
                radius: ['42%', '68%'],
                avoidLabelOverlap: false,
                itemStyle: {
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
                label: {
                    show: true,
                    formatter: '{b}\n{d}%',
                    color: '#5d5035',
                    fontSize: 12,
                    fontWeight: 600,
                },
                data: entries,
            },
        ],
    }
}

function StatCard({ label, value, sub, tone = 'accent', progress }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className={`stat-value ${tone === 'plain' ? 'plain' : ''}`.trim()}>{value}</span>
            <span className="stat-sub">{sub}</span>
            {progress !== undefined ? (
                <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
                </div>
            ) : null}
        </article>
    )
}

function RecentSummaryCard({ item }) {
    return (
        <article className="summary-card">
            <div className="summary-card-header">
                <span className="summary-chapter">{formatChapterLabel(item.chapter)}</span>
                <div className="summary-badges">
                    <Badge tone={toneForHookStrength(item.hook_strength)}>
                        {item.hook_strength || '无钩子'}
                    </Badge>
                    {item.review_score ? <Badge tone="purple">{item.review_score} 分</Badge> : null}
                </div>
            </div>
            <h3>{item.title || '未命名章节'}</h3>
            <p>{item.summary || '暂无章节概要。'}</p>
            <div className="summary-meta">
                <span>{item.location || '未记录地点'}</span>
                <span>{formatNumber(item.word_count)} 字</span>
                <span>{(item.characters || []).join('、') || '未记录角色'}</span>
            </div>
        </article>
    )
}

export default function OverviewPage() {
    const { projectInfo, refreshToken } = useDashboardContext()
    const [runtimeHealth, setRuntimeHealth] = useState(null)
    const [allChapters, setAllChapters] = useState([])
    const [trendWindow, setTrendWindow] = useState({ items: [], total: 0, latest_chapter: 0 })
    const [latestWindow, setLatestWindow] = useState({ items: [], total: 0, latest_chapter: 0 })
    const [windowIndex, setWindowIndex] = useState(0)
    const [loadingTrend, setLoadingTrend] = useState(true)
    const [workflow, setWorkflow] = useState({})
    const [alerts, setAlerts] = useState([])
    const [reminders, setReminders] = useState([])
    const [showAllReminders, setShowAllReminders] = useState(false)

    useEffect(() => {
        setWindowIndex(0)
    }, [refreshToken])

    useEffect(() => {
        let cancelled = false

        Promise.allSettled([
            fetchStoryRuntimeHealth(),
            fetchChapters(),
            fetchChapterTrend({ limit: WINDOW_SIZE, offset: 0 }),
            fetchJSON('/api/workflow/status').catch(() => ({})),
            fetchJSON('/api/alerts').catch(() => ({ alerts: [] })),
            fetchForeshadowingReminders(5).catch(() => ({ reminders: [] })),
        ]).then(results => {
            if (cancelled) return

            setRuntimeHealth(results[0].status === 'fulfilled' ? results[0].value : null)
            setAllChapters(results[1].status === 'fulfilled' ? results[1].value : [])
            // null 解包防御：fulfilled 但响应体为 JSON null 时 .value 为 null，
            // 直接 .alerts/.reminders 会抛同步异常（catch 不捕获同步 throw）
            const wf = results[3].status === 'fulfilled' ? results[3].value : {}
            setWorkflow(wf && typeof wf === 'object' ? wf : {})
            const al = results[4].status === 'fulfilled' ? results[4].value : {}
            setAlerts(al && typeof al === 'object' ? (al.alerts || []) : [])
            const re = results[5].status === 'fulfilled' ? results[5].value : {}
            setReminders(re && typeof re === 'object' ? (re.reminders || []) : [])

            const latest = results[2].status === 'fulfilled'
                ? results[2].value
                : { items: [], total: 0, latest_chapter: 0 }
            setLatestWindow(latest)
            setTrendWindow(latest)
            setLoadingTrend(false)
        })

        return () => {
            cancelled = true
        }
    }, [refreshToken])

    // 仅当 windowIndex > 0 时发起增量 fetch；windowIndex === 0 直接用
    // 首载的 latestWindow（避免与 refreshToken 重叠时并发两次 offset:0
    // fetch 造成旧响应 late-resolve 覆盖新数据的状态竞态）。
    useEffect(() => {
        if (windowIndex === 0) {
            setTrendWindow(latestWindow)
            return
        }

        let cancelled = false
        setLoadingTrend(true)

        fetchChapterTrend({ limit: WINDOW_SIZE, offset: windowIndex * WINDOW_SIZE })
            .then(payload => {
                if (!cancelled) {
                    setTrendWindow(payload)
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setTrendWindow({ items: [], total: latestWindow.total || 0, latest_chapter: latestWindow.latest_chapter || 0 })
                }
            })
            .finally(() => {
                if (!cancelled) {
                    setLoadingTrend(false)
                }
            })

        return () => {
            cancelled = true
        }
    }, [windowIndex, refreshToken])

    // latestWindow 更新（refreshToken 刷新）且当前在第 0 页时同步窗口。
    // 不在 latestWindow 变化时无条件 setWindowIndex(0)——用户翻到后面页时
    // 刷新不应把页码拉回第 0 页（页码重置已由 [refreshToken] 效果负责）。
    useEffect(() => {
        if (windowIndex === 0) {
            setTrendWindow(latestWindow)
        }
    }, [latestWindow, windowIndex])

    const info = projectInfo?.project_info || {}
    const progress = projectInfo?.progress || {}

    const windowItems = trendWindow.items || []
    const totalPages = Math.max(1, Math.ceil((trendWindow.total || latestWindow.total || 0) / WINDOW_SIZE))
    const displayPage = Math.max(1, totalPages - windowIndex)
    const currentStart = windowItems[0]?.chapter || 0
    const currentEnd = windowItems[windowItems.length - 1]?.chapter || 0

    const volumeGroups = useMemo(
        () => groupChaptersByVolume(allChapters, projectInfo),
        [allChapters, projectInfo],
    )

    const foreshadowRecords = useMemo(
        () => buildForeshadowingRecords(projectInfo),
        [projectInfo],
    )
    const foreshadowSummary = useMemo(
        () => summarizeForeshadowing(foreshadowRecords),
        [foreshadowRecords],
    )

    const urgentRows = useMemo(() => {
        return [...foreshadowRecords]
            .sort((left, right) => {
                const priority = (FORESHADOW_PRIORITY[right.level] || 0) - (FORESHADOW_PRIORITY[left.level] || 0)
                if (priority !== 0) return priority
                return (right.urgencyScore || 0) - (left.urgencyScore || 0)
            })
            .slice(0, 5)
    }, [foreshadowRecords])

    const latestReviewAverage = useMemo(() => {
        return average((latestWindow.items || []).map(item => item.review_score))
    }, [latestWindow.items])

    const recentSummaries = useMemo(() => {
        return [...(latestWindow.items || [])].slice(-3).reverse()
    }, [latestWindow.items])

    const strandEntries = useMemo(() => {
        const history = Array.isArray(projectInfo?.strand_tracker?.history)
            ? projectInfo.strand_tracker.history
            : []
        const counts = new Map([
            ['quest', 0],
            ['fire', 0],
            ['constellation', 0],
        ])

        history.forEach(item => {
            const key = String(item?.strand || item?.dominant || '').toLowerCase()
            if (counts.has(key)) {
                counts.set(key, counts.get(key) + 1)
            }
        })

        return [...counts.entries()]
            .filter(([, value]) => value > 0)
            .map(([key, value]) => ({
                name: key,
                value,
                itemStyle: {
                    color: STRAND_COLORS[key] || '#00b8d4',
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
            }))
    }, [projectInfo?.strand_tracker?.history])

    const totalWords = Number(progress.total_words || 0)
    const targetWords = Number(info.target_words || 0)
    const progressPercent = targetWords > 0 ? (totalWords / targetWords) * 100 : 0

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>总览</h2>
                <Badge tone="blue">{info.genre || '未知题材'}</Badge>
            </header>

            {alerts.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 16 }}>
                    {alerts.slice(0, 5).map((a, i) => {
                        const bg = a.severity === 'critical' ? '#5c1a1a' : a.severity === 'warning' ? '#5c4a1a' : '#1a3a5c'
                        return (
                            <div key={i} style={{ padding: '8px 12px', background: bg, borderRadius: 4, fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span>{a.severity === 'critical' ? '🔴' : a.severity === 'warning' ? '🟡' : '🔵'}</span>
                                <span>{a.detail}</span>
                            </div>
                        )
                    })}
                </div>
            )}

            {reminders.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 16 }}>
                    <div style={{ padding: '8px 12px', background: 'var(--bg-card-2)', border: '2px solid var(--accent-amber)', borderRadius: 4, fontSize: 13, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
                         onClick={() => setShowAllReminders(!showAllReminders)}>
                        <span>📌</span>
                        <span style={{ fontWeight: 700 }}>活跃伏笔 ({reminders.length})</span>
                        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
                            {showAllReminders ? '收起 ▲' : '展开 ▼'}
                        </span>
                    </div>
                    {(showAllReminders ? reminders : reminders.slice(0, 1)).map(r => {
                        const desc = r.content || '未命名伏笔'
                        const targetLabel = r.target_chapter ? formatChapterLabel(r.target_chapter) : '未定'
                        return (
                        <div key={r.id} style={{ padding: '6px 12px', background: 'var(--bg-panel)', border: '1px solid var(--border-soft)', borderRadius: 4, fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Badge tone={r.target_chapter ? 'amber' : 'blue'}>{targetLabel}</Badge>
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{desc}</span>
                            {r.tier && <Badge tone="blue" style={{ fontSize: 10 }}>{r.tier}</Badge>}
                            {reminders.length > 1 && !showAllReminders && (
                                <span style={{ fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                                    +{reminders.length - 1} 条
                                </span>
                            )}
                        </div>
                    )})}
                </div>
            )}

            <div className="stat-grid">
                <StatCard
                    label="总字数"
                    value={formatNumber(totalWords)}
                    sub={`目标 ${formatNumber(targetWords || 0)} 字 · ${formatPercent(progressPercent)}`}
                    progress={progressPercent}
                />
                <StatCard
                    label="当前章节"
                    value={formatChapterLabel(progress.current_chapter)}
                    sub={`目标 ${info.target_chapters || '—'} 章 · 卷 ${progress.current_volume || '—'}`}
                />
                <StatCard
                    label="Story Runtime"
                    value={runtimeHealth?.mainline_ready ? 'Mainline' : 'Fallback'}
                    sub={formatRuntimeText(runtimeHealth)}
                    tone="plain"
                />
                <StatCard
                    label="审查均分"
                    value={latestReviewAverage ? formatShortNumber(latestReviewAverage) : '—'}
                    sub="最近 50 章平均"
                />
                <StatCard
                    label="紧急伏笔"
                    value={String(foreshadowSummary.attention)}
                    sub={`总计 ${foreshadowSummary.total} 条伏笔`}
                />
            </div>

            {Object.keys(workflow).length > 0 && (
                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">WORKFLOW</div>
                            <div className="card-title">写作进度</div>
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', padding: '12px 0' }}>
                        {Object.entries(workflow).sort(([a], [b]) => Number(a) - Number(b)).slice(-1).map(([ch, stages]) => (
                            <div key={ch} style={{ width: '100%' }}>
                                <div style={{ fontWeight: 600, marginBottom: 8 }}>第 {ch} 章</div>
                                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                                    {['PLANNING', 'DRAFTING', 'REVIEWING', 'REVISING', 'COMMITTED'].map(stage => {
                                        const done = stages[stage] !== undefined
                                        const current = stages._current_stage === stage
                                        return (
                                            <Fragment key={stage}>
                                                <div style={{
                                                    width: 16, height: 16, borderRadius: '50%',
                                                    background: current ? 'var(--accent)' : done ? '#4caf50' : '#555',
                                                    boxShadow: current ? '0 0 8px var(--accent)' : 'none',
                                                    transition: 'background 0.3s',
                                                }} title={stage} />
                                                {stage !== 'COMMITTED' && (
                                                    <div style={{ flex: 1, height: 2, background: done ? '#4caf50' : '#333', minWidth: 16 }} />
                                                )}
                                            </Fragment>
                                        )
                                    })}
                                </div>
                                <div style={{ display: 'flex', gap: 4, marginTop: 4, fontSize: 10, color: '#888' }}>
                                    {['PLANNING', 'DRAFTING', 'REVIEWING', 'REVISING', 'COMMITTED'].map(stage => (
                                        <span key={stage} style={{ flex: 1, textAlign: 'center' }}>{stage === 'COMMITTED' ? 'DONE' : stage.slice(0, 4)}</span>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </article>
            )}

            <article className="card">
                <div className="card-header">
                    <div>
                        <div className="section-label">REVIEW TREND</div>
                        <div className="card-title">审查得分趋势</div>
                    </div>
                    <Badge tone="green">
                        {currentStart && currentEnd ? `${formatChapterLabel(currentStart)} - ${formatChapterLabel(currentEnd)}` : '最近窗口'}
                    </Badge>
                </div>
                {windowItems.length ? (
                    <>
                        <ChartWrapper option={buildReviewOption(windowItems)} loading={loadingTrend} />
                        <Pager
                            page={displayPage}
                            totalPages={totalPages}
                            currentStart={currentStart}
                            currentEnd={currentEnd}
                            totalItems={trendWindow.total || latestWindow.total || 0}
                            onPrevious={() => setWindowIndex(current => Math.min(Math.max(1, totalPages) - 1, current + 1))}
                            onNext={() => setWindowIndex(current => Math.max(0, current - 1))}
                            onLatest={() => setWindowIndex(0)}
                            stepLabel={String(WINDOW_SIZE)}
                        />
                    </>
                ) : (
                    <div className="empty-state">
                        <p>暂无章节趋势数据</p>
                    </div>
                )}
            </article>

            <div className="content-grid two-columns">
                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">WORD DISTRIBUTION</div>
                            <div className="card-title">字数分布（按卷）</div>
                        </div>
                        <Badge tone="purple">{volumeGroups.length} 卷</Badge>
                    </div>
                    {volumeGroups.length ? (
                        <ChartWrapper option={buildVolumeOption(volumeGroups)} />
                    ) : (
                        <div className="empty-state">
                            <p>暂无章节字数数据</p>
                        </div>
                    )}
                </article>

                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">STRAND OVERVIEW</div>
                            <div className="card-title">Strand Weave 整体分布</div>
                        </div>
                        <Badge tone="purple">{projectInfo?.strand_tracker?.current_dominant || 'unknown'}</Badge>
                    </div>
                    {strandEntries.length ? (
                        <ChartWrapper option={buildStrandOption(strandEntries)} height={260} />
                    ) : (
                        <div className="empty-state">
                            <p>暂无 Strand 历史</p>
                        </div>
                    )}
                </article>
            </div>

            <div className="content-grid two-columns">
                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">FORESHADOWING</div>
                            <div className="card-title">紧急伏笔 Top 5</div>
                        </div>
                        <Badge tone="amber">按紧急度排序</Badge>
                    </div>
                    <DataTable
                        columns={[
                            { key: 'content', label: '内容' },
                            {
                                key: 'statusText',
                                label: '状态',
                                render: row => <Badge tone={toneForForeshadowing(row.level)}>{row.statusText}</Badge>,
                            },
                            {
                                key: 'plantedChapter',
                                label: '埋设章',
                                render: row => formatChapterLabel(row.plantedChapter),
                            },
                            {
                                key: 'targetChapter',
                                label: '目标章',
                                render: row => formatChapterLabel(row.targetChapter),
                            },
                            {
                                key: 'urgencyText',
                                label: '紧急度',
                                render: row => <Badge tone={toneForUrgencyBadge(row.urgencyText)}>{row.urgencyText}</Badge>,
                            },
                        ]}
                        rows={urgentRows}
                        rowKey="id"
                        pageSize={5}
                        emptyText="暂无伏笔数据"
                        minWidth={680}
                    />
                </article>

                <article className="card">
                    <div className="card-header">
                        <div>
                            <div className="section-label">LATEST CHAPTERS</div>
                            <div className="card-title">最近 3 章概要</div>
                        </div>
                        <Badge tone="cyan">{recentSummaries.length} 条</Badge>
                    </div>
                    {recentSummaries.length ? (
                        <div className="summary-card-list">
                            {recentSummaries.map(item => (
                                <RecentSummaryCard key={item.chapter} item={item} />
                            ))}
                        </div>
                    ) : (
                        <div className="empty-state">
                            <p>暂无最新章节概要</p>
                        </div>
                    )}
                </article>
            </div>
        </section>
    )
}
