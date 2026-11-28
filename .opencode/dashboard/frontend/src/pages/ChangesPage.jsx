import { useCallback, useEffect, useMemo, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import Badge from '../components/Badge.jsx'
import {
    fetchChangesDiff,
    fetchChapterChanges,
    fetchProjectInfo,
    HttpError,
} from '../api.js'

//: 变更审计页。两个视图，回答两个不同的问题：
//:   * 单章 —— **这一章改了什么**（全量，含低危）
//:   * 对比 —— **两章之间变了什么**（changed/added/removed 三态）
//:
//: 后者的价值集中在 `removed`：某字段在前一章有、后一章消失，通常意味着投影漏了
//: 一条，而不是作者真的删了它。纯表格视图看不出这种「静默丢失」。
//:
//: 三种加载状态必须分开（与 ContextHealthPage 同一条教训）：
//:   1. 404      → 还没提交。**正常状态**，不能报红。
//:   2. 5xx/网络 → 真的出错，红色。
//:   3. 有数据   → 表格。

const RISK_LABELS = { high: '高危', medium: '中危', low: '低' }
const RISK_TONES = { high: 'red', medium: 'amber', low: 'blue' }

function stringify(value) {
    if (value === null || value === undefined) return '—'
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
}

function statusView(loading, error, missing, chapter) {
    if (loading) return <div className="empty-state">加载中...</div>
    if (error) {
        return <div className="empty-state" style={{ color: 'var(--accent-red)' }}>加载失败: {error}</div>
    }
    if (missing) {
        return (
            <div className="empty-state">
                <p>第 {chapter} 章还没有提交记录。写完之后再回来看它的状态变更。</p>
            </div>
        )
    }
    return null
}

export default function ChangesPage() {
    const { refreshToken } = useDashboardContext()
    const [mode, setMode] = useState('single')
    const [chapter, setChapter] = useState(1)
    const [chapterInput, setChapterInput] = useState('1')
    const [fromInput, setFromInput] = useState('1')
    const [toInput, setToInput] = useState('2')
    const [data, setData] = useState(null)
    const [diff, setDiff] = useState(null)
    const [loading, setLoading] = useState(false)
    const [missing, setMissing] = useState(false)
    const [error, setError] = useState(null)
    const [highRiskOnly, setHighRiskOnly] = useState(false)

    // 默认停在上次写的章（长书用户不该每次手填），diff 默认「上一章 → 当前章」。
    useEffect(() => {
        fetchProjectInfo()
            .then(info => {
                const current = Number(info?.progress?.current_chapter) || 1
                setChapter(current)
                setChapterInput(String(current))
                setFromInput(String(Math.max(1, current - 1)))
                setToInput(String(current))
            })
            .catch(() => { /* 拿不到就留在默认值 */ })
    }, [])

    const loadSingle = useCallback(() => {
        let cancelled = false
        setLoading(true)
        setMissing(false)
        setError(null)
        fetchChapterChanges(chapter)
            .then(payload => { if (!cancelled) setData(payload) })
            .catch(e => {
                if (cancelled) return
                if (e instanceof HttpError && e.status === 404) {
                    setMissing(true)
                    setData(null)
                } else {
                    setError(e?.message || String(e))
                }
            })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [chapter])

    useEffect(() => {
        if (mode !== 'single') return undefined
        return loadSingle()
    }, [mode, loadSingle, refreshToken])

    const loadDiff = useCallback(() => {
        let cancelled = false
        setLoading(true)
        setMissing(false)
        setError(null)
        fetchChangesDiff(Number(fromInput), Number(toInput))
            .then(payload => { if (!cancelled) setDiff(payload) })
            .catch(e => {
                if (cancelled) return
                if (e instanceof HttpError && e.status === 404) {
                    setMissing(true)
                    setDiff(null)
                } else {
                    setError(e?.message || String(e))
                }
            })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [fromInput, toInput])

    useEffect(() => {
        if (mode !== 'diff') return undefined
        return loadDiff()
    }, [mode, loadDiff, refreshToken])

    const rows = useMemo(() => {
        const all = data?.changes || []
        return highRiskOnly ? all.filter(r => r.risk !== 'low') : all
    }, [data, highRiskOnly])

    function handleGoSingle(event) {
        event.preventDefault()
        const next = Number.parseInt(chapterInput, 10)
        if (Number.isFinite(next) && next >= 1) setChapter(next)
    }

    function handleGoDiff(event) {
        event.preventDefault()
        loadDiff()
    }

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>变更审计</h2>
                {mode === 'single' && data && (
                    <Badge tone="blue">第 {data.chapter} 章 · {data.count} 项</Badge>
                )}
                {mode === 'diff' && diff && (
                    <Badge tone="purple">
                        {diff.from} → {diff.to} · 变化 {diff.counts.changed}
                        {' / '}新增 {diff.counts.added}
                        {' / '}消失 {diff.counts.removed}
                    </Badge>
                )}
            </header>

            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button type="button" onClick={() => setMode('single')}
                    style={{ fontWeight: mode === 'single' ? 700 : 400 }}>单章</button>
                <button type="button" onClick={() => setMode('diff')}
                    style={{ fontWeight: mode === 'diff' ? 700 : 400 }}>对比两章</button>
            </div>

            {mode === 'single' && (
                <form onSubmit={handleGoSingle}
                    style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
                    <label htmlFor="changes-chapter">章号</label>
                    <input id="changes-chapter" type="number" min="1" value={chapterInput}
                        onChange={e => setChapterInput(e.target.value)}
                        style={{ width: 96, padding: '4px 8px' }} />
                    <button type="submit">查看</button>
                    <label style={{ display: 'flex', gap: 4, alignItems: 'center', marginLeft: 8 }}>
                        <input type="checkbox" checked={highRiskOnly}
                            onChange={e => setHighRiskOnly(e.target.checked)} />
                        只看高危/中危
                    </label>
                </form>
            )}

            {mode === 'diff' && (
                <form onSubmit={handleGoDiff}
                    style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
                    <label htmlFor="changes-from">从</label>
                    <input id="changes-from" type="number" min="1" value={fromInput}
                        onChange={e => setFromInput(e.target.value)}
                        style={{ width: 96, padding: '4px 8px' }} />
                    <label htmlFor="changes-to">到</label>
                    <input id="changes-to" type="number" min="1" value={toInput}
                        onChange={e => setToInput(e.target.value)}
                        style={{ width: 96, padding: '4px 8px' }} />
                    <button type="submit">对比</button>
                </form>
            )}

            {statusView(loading, error, missing, mode === 'single' ? chapter : `${fromInput}/${toInput}`)}

            {!loading && !error && !missing && mode === 'single' && data && rows.length === 0 && (
                <div className="empty-state">
                    <p>
                        {highRiskOnly
                            ? '这一章没有高危/中危字段的变更。取消勾选可以看全部。'
                            : '这一章没有状态变更。'}
                    </p>
                </div>
            )}

            {!loading && !error && !missing && mode === 'single' && rows.length > 0 && (
                <article className="card">
                    <div className="card-header">
                        <span className="card-title">第 {data.chapter} 章的状态变更</span>
                        <Badge tone="purple">{rows.length} 项</Badge>
                    </div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                        <thead>
                            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border, #d0d7de)' }}>
                                <th style={{ padding: '6px 8px' }}>实体</th>
                                <th style={{ padding: '6px 8px' }}>字段</th>
                                <th style={{ padding: '6px 8px' }}>旧值</th>
                                <th style={{ padding: '6px 8px' }}>新值</th>
                                <th style={{ padding: '6px 8px' }}>风险</th>
                                <th style={{ padding: '6px 8px' }}>来源</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row, i) => (
                                <tr key={`${row.entity_id}-${row.field}-${i}`}
                                    style={{ borderBottom: '1px solid var(--border, #d0d7de)' }}>
                                    <td style={{ padding: '6px 8px', fontWeight: 600 }}>{row.entity_id}</td>
                                    <td style={{ padding: '6px 8px' }}>{row.field}</td>
                                    <td style={{ padding: '6px 8px', color: 'var(--text-muted, #8b949e)' }}>
                                        {stringify(row.old)}
                                    </td>
                                    <td style={{ padding: '6px 8px', fontWeight: 600 }}>{stringify(row.new)}</td>
                                    <td style={{ padding: '6px 8px' }}>
                                        <Badge tone={RISK_TONES[row.risk] || 'blue'}>
                                            {RISK_LABELS[row.risk] || row.risk}
                                        </Badge>
                                        {row.locked_value !== undefined && (
                                            <Badge tone="red">违背锁定 {stringify(row.locked_value)}</Badge>
                                        )}
                                    </td>
                                    <td style={{ padding: '6px 8px', color: 'var(--text-muted, #8b949e)', fontSize: 12 }}>
                                        {row.source}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </article>
            )}

            {!loading && !error && !missing && mode === 'diff' && diff && (
                <>
                    {diff.counts.changed === 0 && diff.counts.added === 0 && diff.counts.removed === 0 && (
                        <div className="empty-state">
                            <p>这两章的状态变更完全一致。</p>
                        </div>
                    )}

                    {diff.changed.length > 0 && (
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">值发生变化</span>
                                <Badge tone="amber">{diff.counts.changed} 项</Badge>
                            </div>
                            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>
                                {diff.changed.map((row, i) => (
                                    <li key={`${row.entity_id}-${row.field}-${i}`} style={{ marginBottom: 4 }}>
                                        {row.entity_id}.{row.field}：{stringify(row.from)} →{' '}
                                        <span style={{ fontWeight: 600 }}>{stringify(row.to)}</span>
                                    </li>
                                ))}
                            </ul>
                        </article>
                    )}

                    {diff.added.length > 0 && (
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">后者新增</span>
                                <Badge tone="blue">{diff.counts.added} 项</Badge>
                            </div>
                            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>
                                {diff.added.map((row, i) => (
                                    <li key={`${row.entity_id}-${row.field}-${i}`} style={{ marginBottom: 4 }}>
                                        {row.entity_id}.{row.field} = {stringify(row.new)}
                                    </li>
                                ))}
                            </ul>
                        </article>
                    )}

                    {diff.removed.length > 0 && (
                        <article className="card" style={{ borderColor: 'var(--accent-red)' }}>
                            <div className="card-header">
                                <span className="card-title" style={{ color: 'var(--accent-red)' }}>
                                    后者不再有（可能是投影漏了一条）
                                </span>
                                <Badge tone="red">{diff.counts.removed} 项</Badge>
                            </div>
                            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>
                                {diff.removed.map((row, i) => (
                                    <li key={`${row.entity_id}-${row.field}-${i}`} style={{ marginBottom: 4 }}>
                                        {row.entity_id}.{row.field}（原值 {stringify(row.new)}）
                                    </li>
                                ))}
                            </ul>
                        </article>
                    )}
                </>
            )}
        </section>
    )
}
