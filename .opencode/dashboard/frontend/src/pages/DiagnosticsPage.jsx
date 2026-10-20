import { useEffect, useState } from 'react'
import { useDashboardContext } from '../App.jsx'
import { fetchDiagnostics } from '../api.js'
import Badge from '../components/Badge.jsx'
import DataTable from '../components/DataTable.jsx'
import ChartWrapper from '../components/ChartWrapper.jsx'
import { formatChapterLabel, formatDateTime, formatShortNumber } from '../lib/format.js'

function StatCard({ label, value, sub, tone }) {
    return (
        <article className="card stat-card">
            <span className="stat-label">{label}</span>
            <span className={`stat-value${tone ? ` ${tone}` : ''}`}>{value}</span>
            {sub ? <span className="stat-sub">{sub}</span> : null}
        </article>
    )
}

function toneForLatency(p95) {
    if (p95 === null || p95 === undefined) return 'blue'
    if (p95 >= 3000) return 'red'
    if (p95 >= 1000) return 'amber'
    return 'green'
}

function toneForFailureRate(rate) {
    if (rate >= 0.2) return 'red'
    if (rate >= 0.05) return 'amber'
    return 'green'
}

function buildQueryTypeOption(byType) {
    const entries = Object.entries(byType || {}).filter(([, count]) => count > 0)
    if (!entries.length) return null
    return {
        tooltip: { trigger: 'item', formatter: '{b}: {c} 次 ({d}%)' },
        series: [{
            type: 'pie',
            radius: ['45%', '70%'],
            data: entries.map(([name, value], index) => ({
                name,
                value,
                itemStyle: {
                    color: ['#26a8ff', '#f5a524', '#7f5af0', '#2ec27e', '#d7263d'][index % 5],
                    borderColor: '#2a220f',
                    borderWidth: 2,
                },
            })),
            label: { color: '#5d5035', fontSize: 12, fontWeight: 600 },
        }],
    }
}

function buildToolOption(byTool) {
    const entries = Object.entries(byTool || {}).filter(([, meta]) => meta.count > 0)
    if (!entries.length) return null
    const names = entries.map(([name]) => (name.length > 28 ? `${name.slice(0, 27)}…` : name))
    return {
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        legend: { data: ['成功', '失败'], bottom: 0 },
        grid: { left: 150, right: 24, top: 20, bottom: 44 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: names },
        series: [
            {
                name: '成功',
                type: 'bar',
                stack: 'total',
                data: entries.map(([, meta]) => meta.count - meta.failures),
                itemStyle: { color: '#2ec27e', borderColor: '#2a220f', borderWidth: 2 },
            },
            {
                name: '失败',
                type: 'bar',
                stack: 'total',
                data: entries.map(([, meta]) => meta.failures),
                itemStyle: { color: '#d7263d', borderColor: '#2a220f', borderWidth: 2 },
            },
        ],
    }
}

export default function DiagnosticsPage() {
    const { refreshToken } = useDashboardContext()
    const [data, setData] = useState(null)
    const [error, setError] = useState(null)
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        let cancelled = false
        setLoading(true)
        fetchDiagnostics(100)
            .then(payload => {
                if (cancelled) return
                setData(payload)
                setError(null)
            })
            .catch(err => {
                if (cancelled) return
                setData(null)
                setError(err?.message || '加载失败')
            })
            .finally(() => { if (!cancelled) setLoading(false) })
        return () => { cancelled = true }
    }, [refreshToken])

    const rag = data?.rag || {}
    const tools = data?.tools || {}
    const queryTypeOption = buildQueryTypeOption(rag.by_query_type)
    const toolOption = buildToolOption(tools.by_tool)

    if (loading && !data) {
        return (
            <section className="dashboard-page">
                <header className="page-header"><h2>运维诊断</h2></header>
                <div className="empty-state"><p>正在加载…</p></div>
            </section>
        )
    }

    if (error) {
        return (
            <section className="dashboard-page">
                <header className="page-header"><h2>运维诊断</h2></header>
                <div className="empty-state" style={{ color: 'var(--accent-red)' }}>
                    <p>加载失败：{error}</p>
                </div>
            </section>
        )
    }

    const isEmpty = !rag.total && !tools.total

    return (
        <section className="dashboard-page">
            <header className="page-header">
                <h2>运维诊断</h2>
                <Badge tone="purple">只读</Badge>
            </header>

            {isEmpty ? (
                <div className="empty-state">
                    <p>还没有诊断数据。</p>
                    <p className="empty-hint">
                        <code>rag_query_log</code> 在每次 RAG 检索时写入，
                        <code>tool_call_stats</code> 在上下文组装等工具调用时写入。
                        跑几章写作流程后再回来查看。
                    </p>
                </div>
            ) : (
                <>
                    <div className="stat-grid">
                        <StatCard
                            label="RAG 检索次数"
                            value={formatShortNumber(rag.total || 0)}
                            sub={`均值 ${formatShortNumber(rag.avg_latency_ms || 0)} ms`}
                        />
                        <StatCard
                            label="检索延迟 P95"
                            value={`${formatShortNumber(rag.p95_latency_ms || 0)} ms`}
                            sub={`P50 ${formatShortNumber(rag.p50_latency_ms || 0)} ms`}
                            
                            tone={toneForLatency(rag.p95_latency_ms) === 'green' ? 'plain' : undefined}
                        />
                        <StatCard
                            label="空命中"
                            value={formatShortNumber(rag.empty_hits_total || 0)}
                            sub={rag.total
                                ? `占全部检索 ${formatShortNumber((rag.empty_hits_total / rag.total) * 100)}%`
                                : '—'}
                        />
                        <StatCard
                            label="工具失败率"
                            value={`${formatShortNumber((tools.failure_rate || 0) * 100)}%`}
                            sub={`${formatShortNumber(tools.failures || 0)} / ${formatShortNumber(tools.total || 0)} 次调用`}
                            tone={toneForFailureRate(tools.failure_rate || 0) === 'green' ? 'plain' : undefined}
                        />
                    </div>

                    <div className="content-grid two-columns">
                        {queryTypeOption ? (
                            <article className="card">
                                <div className="card-header">
                                    <span className="card-title">检索类型分布</span>
                                    <Badge tone="blue">{Object.keys(rag.by_query_type || {}).length} 类</Badge>
                                </div>
                                <ChartWrapper option={queryTypeOption} height={300} />
                            </article>
                        ) : null}

                        {toolOption ? (
                            <article className="card">
                                <div className="card-header">
                                    <span className="card-title">工具调用健康度</span>
                                    <Badge tone={toneForFailureRate(tools.failure_rate || 0)}>
                                        {formatShortNumber(tools.total || 0)} 次
                                    </Badge>
                                </div>
                                <ChartWrapper option={toolOption} height={300} />
                            </article>
                        ) : null}
                    </div>

                    {rag.slowest?.length ? (
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">最慢的检索</span>
                                <Badge tone="amber">Top {rag.slowest.length}</Badge>
                            </div>
                            <DataTable
                                columns={[
                                    {
                                        key: 'query',
                                        label: '查询',
                                        render: row => (
                                            <span title={row.query} style={{ wordBreak: 'break-word' }}>
                                                {row.query || '（空）'}
                                            </span>
                                        ),
                                    },
                                    { key: 'query_type', label: '类型', render: row => <Badge tone="cyan">{row.query_type || 'unknown'}</Badge> },
                                    {
                                        key: 'latency_ms',
                                        label: '延迟',
                                        style: { textAlign: 'right', whiteSpace: 'nowrap' },
                                        render: row => `${formatShortNumber(row.latency_ms)} ms`,
                                    },
                                    {
                                        key: 'results_count',
                                        label: '命中',
                                        style: { textAlign: 'right' },
                                        render: row => (
                                            row.results_count > 0
                                                ? formatShortNumber(row.results_count)
                                                : <Badge tone="amber">0</Badge>
                                        ),
                                    },
                                    { key: 'chapter', label: '章节', render: row => formatChapterLabel(row.chapter) },
                                ]}
                                rows={rag.slowest}
                                rowKey="query"
                                pageSize={5}
                                minWidth={480}
                                emptyText="暂无检索记录"
                            />
                        </article>
                    ) : null}

                    {tools.recent_failures?.length ? (
                        <article className="card">
                            <div className="card-header">
                                <span className="card-title">最近失败</span>
                                <Badge tone="red">{tools.recent_failures.length} 条</Badge>
                            </div>
                            <DataTable
                                columns={[
                                    { key: 'tool_name', label: '工具' },
                                    { key: 'error_code', label: '错误码', render: row => row.error_code || '—' },
                                    {
                                        key: 'error_message',
                                        label: '信息',
                                        render: row => (
                                            <span title={row.error_message} style={{ wordBreak: 'break-word' }}>
                                                {row.error_message || '—'}
                                            </span>
                                        ),
                                    },
                                    { key: 'chapter', label: '章节', render: row => formatChapterLabel(row.chapter) },
                                    { key: 'created_at', label: '时间', render: row => formatDateTime(row.created_at) },
                                ]}
                                rows={tools.recent_failures}
                                rowKey="created_at"
                                pageSize={5}
                                minWidth={560}
                                emptyText="没有失败记录"
                            />
                        </article>
                    ) : null}
                </>
            )}
        </section>
    )
}
