import { useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import { fetchDebtDetail, fetchDebtsSummary } from '../api.js'
import Badge from '../components/Badge.jsx'
import DataTable from '../components/DataTable.jsx'
import { formatChapterLabel, formatDateTime, formatShortNumber } from '../lib/format.js'

// 债务类型的中文标签。库里存英文键（foreshadowing / reader_promise /
// pacing_debt / …），直接显示英文对作者没有意义。
const DEBT_TYPE_LABELS = {
    foreshadowing: '伏笔未收',
    reader_promise: '对读者的承诺',
    pacing_debt: '节奏欠账',
    character_absence: '角色失踪',
    unresolved_conflict: '未解冲突',
    unknown: '未分类',
}

const EVENT_TYPE_LABELS = {
    create: '建债',
    accrue: '计息',
    pay: '偿还',
    resolve: '销债',
    adjust: '调整',
}

function debtTypeLabel(type) {
    return DEBT_TYPE_LABELS[type] || type || '未分类'
}

function eventTypeLabel(type) {
    return EVENT_TYPE_LABELS[type] || type || '—'
}

function StatCard({ label, value, sub, tone }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className={`stat-value${tone ? ` ${tone}` : ''}`}>{value}</span>
            {sub ? <span className="stat-sub">{sub}</span> : null}
        </article>
    )
}

/**
 * 到期状态：优先用「当前章 vs 到期章」判断，而不是库里的 status 字段。
 *
 * 库里 status 只在 `accrue_interest` 被调用时才推进，作者若许久没跑章节
 * 提交，一堆早已过期的债会仍标着 'active'。两者不一致本身就是信号——
 * 页面上要能看出「这债其实早该还了」。
 */
function dueState(row) {
    if (row.status === 'resolved' || row.status === 'paid') {
        return { tone: 'green', text: '已结清' }
    }
    if (row.is_overdue) return { tone: 'red', text: '已超期' }
    if (row.chapters_left !== null && row.chapters_left !== undefined && row.chapters_left <= 10) {
        return { tone: 'amber', text: '临近到期' }
    }
    return { tone: 'blue', text: '进行中' }
}

function growthTone(growth) {
    if (growth === null || growth === undefined) return 'blue'
    if (growth >= 2.5) return 'red'
    if (growth >= 1.8) return 'amber'
    return 'green'
}

export default function DebtPage() {
    const { refreshToken } = useDashboardContext()
    const [summary, setSummary] = useState(null)
    const [error, setError] = useState(null)
    const [loading, setLoading] = useState(true)
    const [selectedId, setSelectedId] = useState(null)
    const [detail, setDetail] = useState(null)

    useEffect(() => {
        let cancelled = false
        setLoading(true)
        fetchDebtsSummary(100)
            .then(data => {
                if (cancelled) return
                setSummary(data)
                setError(null)
            })
            .catch(e => {
                if (cancelled) return
                setSummary(null)
                // 区分「库还没建这张表」与「真的出错」：前者是新项目常态，
                // 不该用红字吓人。
                setError(e.message || '加载失败')
            })
            .finally(() => {
                if (!cancelled) setLoading(false)
            })
        return () => { cancelled = true }
    }, [refreshToken])

    // 选中某笔债时拉取它的完整事件时间线
    useEffect(() => {
        if (!selectedId) {
            setDetail(null)
            return undefined
        }
        let cancelled = false
        fetchDebtDetail(selectedId)
            .then(data => { if (!cancelled) setDetail(data) })
            .catch(() => { if (!cancelled) setDetail(null) })
        return () => { cancelled = true }
    }, [selectedId, refreshToken])

    const items = summary?.items || []
    const totals = summary?.totals || {}
    const byType = summary?.by_type || {}

    const maxBalance = useMemo(
        () => items.reduce((max, row) => Math.max(max, row.current_amount || 0), 0),
        [items],
    )

    const columns = useMemo(() => [
        {
            key: 'debt_type',
            label: '类型',
            render: row => (
                <span title={row.debt_type}>{debtTypeLabel(row.debt_type)}</span>
            ),
        },
        {
            key: 'current_amount',
            label: '当前金额',
            style: { textAlign: 'right' },
            render: row => (
                <strong>{formatShortNumber(row.current_amount)}</strong>
            ),
        },
        {
            key: 'growth',
            label: '滚存倍数',
            style: { textAlign: 'right' },
            render: row => (
                row.growth === null || row.growth === undefined
                    ? '—'
                    : <Badge tone={growthTone(row.growth)}>×{formatShortNumber(row.growth)}</Badge>
            ),
        },
        {
            key: 'interest_rate',
            label: '利率',
            style: { textAlign: 'right' },
            render: row => `${formatShortNumber((row.interest_rate || 0) * 100)}%/章`,
        },
        {
            key: 'source_chapter',
            label: '埋设章',
            render: row => formatChapterLabel(row.source_chapter),
        },
        {
            key: 'due_chapter',
            label: '到期章',
            render: row => formatChapterLabel(row.due_chapter),
        },
        {
            key: 'chapters_left',
            label: '剩余',
            style: { textAlign: 'right' },
            render: row => {
                if (row.chapters_left === null || row.chapters_left === undefined) return '—'
                if (row.chapters_left < 0) return `超 ${Math.abs(row.chapters_left)} 章`
                return `${row.chapters_left} 章`
            },
        },
        {
            key: 'state',
            label: '状态',
            render: row => {
                const state = dueState(row)
                return <Badge tone={state.tone}>{state.text}</Badge>
            },
        },
        {
            key: 'action',
            label: '',
            render: row => (
                <button
                    className="page-btn"
                    type="button"
                    onClick={() => setSelectedId(row.id)}
                >
                    明细
                </button>
            ),
        },
    ], [])

    if (loading && !summary) {
        return (
            <section className="dashboard-page">
                <header className="page-header">
                    <h2>追读债</h2>
                </header>
                <div className="empty-state">
                    <p>正在加载…</p>
                </div>
            </section>
        )
    }

    if (error) {
        return (
            <section className="dashboard-page">
                <header className="page-header">
                    <h2>追读债</h2>
                </header>
                <div className="empty-state" style={{ color: 'var(--accent-red)' }}>
                    <p>加载失败：{error}</p>
                </div>
            </section>
        )
    }

    const overdueCount = totals.overdue || 0
    const activeTypeCount = Object.keys(byType).length

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>追读债</h2>
                <Badge tone="purple">{formatChapterLabel(summary?.current_chapter)}</Badge>
            </header>

            <div className="stat-grid">
                <StatCard
                    label="未结债务"
                    value={formatShortNumber(totals.count || 0)}
                    // totals 是全表口径，items 只有当前页；截断时要说清楚，
                    // 否则作者会以为「最大单笔」也是全表的最大值。
                    sub={summary?.truncated
                        ? `涉及 ${activeTypeCount} 种类型 · 列表仅显示前 ${items.length} 笔`
                        : `涉及 ${activeTypeCount} 种类型`}
                />
                <StatCard
                    label="总余额"
                    value={formatShortNumber(totals.balance || 0)}
                    sub="未结债务的当前金额合计"
                />
                <StatCard
                    label="已超期"
                    value={formatShortNumber(overdueCount)}
                    sub="按当前章推算，非库内 status"
                    tone={overdueCount > 0 ? 'plain' : undefined}
                />
                <StatCard
                    label="最大单笔"
                    value={formatShortNumber(maxBalance)}
                    sub={items.length ? debtTypeLabel(items.reduce(
                        (a, b) => ((a.current_amount || 0) >= (b.current_amount || 0) ? a : b),
                    ).debt_type) : '—'}
                />
            </div>

            <article className="card">
                <div className="card-header">
                    <span className="card-title">债务列表</span>
                    <Badge tone={overdueCount > 0 ? 'red' : 'green'}>
                        {overdueCount > 0 ? `${overdueCount} 笔已超期` : '全部在期内'}
                    </Badge>
                </div>
                <DataTable
                    columns={columns}
                    rows={items}
                    rowKey="id"
                    pageSize={10}
                    minWidth={900}
                    emptyText="该项目暂无追读债记录"
                />
            </article>

            {selectedId ? (
                <article className="card">
                    <div className="card-header">
                        <span className="card-title">
                            债务 #{selectedId} 事件时间线
                        </span>
                        <button
                            className="page-btn"
                            type="button"
                            onClick={() => setSelectedId(null)}
                        >
                            关闭
                        </button>
                    </div>
                    {detail ? (
                        <>
                            <div className="detail-meta">
                                <span>类型：{debtTypeLabel(detail.debt?.debt_type)}</span>
                                <span>当前：{formatShortNumber(detail.debt?.current_amount)}</span>
                                <span>
                                    到期：{formatChapterLabel(detail.debt?.due_chapter)}
                                    {detail.chapters_left !== null && detail.chapters_left !== undefined
                                        ? `（${detail.chapters_left < 0 ? `已超 ${Math.abs(detail.chapters_left)} 章` : `还剩 ${detail.chapters_left} 章`}）`
                                        : ''}
                                </span>
                            </div>
                            <DataTable
                                columns={[
                                    { key: 'chapter', label: '章节', render: row => formatChapterLabel(row.chapter) },
                                    { key: 'event_type', label: '事件', render: row => eventTypeLabel(row.event_type) },
                                    {
                                        key: 'amount',
                                        label: '金额',
                                        style: { textAlign: 'right' },
                                        render: row => formatShortNumber(row.amount),
                                    },
                                    { key: 'note', label: '说明' },
                                    { key: 'created_at', label: '时间', render: row => formatDateTime(row.created_at) },
                                ]}
                                rows={detail.events || []}
                                rowKey="id"
                                pageSize={8}
                                minWidth={720}
                                emptyText="这笔债还没有任何事件记录"
                            />
                        </>
                    ) : (
                        <div className="empty-state compact"><p>正在加载明细…</p></div>
                    )}
                </article>
            ) : null}

            {!items.length ? (
                <div className="empty-state">
                    <p>该项目还没有追读债。</p>
                    <p className="empty-hint">
                        债务由 <code>chapter-commit</code> 在 <code>open_loop_created</code> 事件时自动建立，
                        并在伏笔回收时销账。写几章之后再回来看看。
                    </p>
                </div>
            ) : null}
        </section>
    )
}
