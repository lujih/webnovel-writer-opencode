import { startTransition, useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet, useOutletContext } from 'react-router-dom'
import { fetchProjectInfo, subscribeSSE } from './api.js'
import {
    BookmarkIcon,
    ChartBarIcon,
    ClipboardIcon,
    CoinsIcon,
    FolderIcon,
    LayersIcon,
    PenIcon,
    PulseIcon,
    SlidersIcon,
    TrendingUpIcon,
    UsersIcon,
    WifiIcon,
    WifiOffIcon,
} from './icons.jsx'

const NAV_ITEMS = [
    { to: '/', label: '总览', icon: ChartBarIcon, end: true },
    { to: '/context', label: '上下文健康', icon: LayersIcon },
    { to: '/characters', label: '角色图鉴', icon: UsersIcon },
    { to: '/review', label: '审查分析', icon: ClipboardIcon },
    { to: '/pacing', label: '节奏雷达', icon: TrendingUpIcon },
    { to: '/foreshadowing', label: '伏笔追踪', icon: BookmarkIcon },
    { to: '/debts', label: '追读债', icon: CoinsIcon },
    { to: '/files', label: '文档浏览', icon: FolderIcon },
    { to: '/style', label: '文风约束', icon: PenIcon },
    { to: '/system', label: '系统状态', icon: SlidersIcon },
    { to: '/diagnostics', label: '运维诊断', icon: PulseIcon },
]

function ThemeToggle() {
    const [theme, setTheme] = useState(() => {
        try { return localStorage.getItem('theme') || 'light' } catch { return 'light' }
    })

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme)
        try { localStorage.setItem('theme', theme) } catch {}
    }, [theme])

    return (
        <button
            className="theme-toggle"
            onClick={() => setTheme(t => t === 'light' ? 'dark' : 'light')}
            title={theme === 'light' ? '切换到暗色模式' : '切换到亮色模式'}
        >
            {theme === 'light' ? '🌙' : '☀️'}
        </button>
    )
}

/** SSE 事件合并窗口（毫秒）：一批文件事件收敛成一次刷新。 */
const REFRESH_DEBOUNCE_MS = 300

export default function App() {
    const [projectInfo, setProjectInfo] = useState(null)
    const [refreshToken, setRefreshToken] = useState(0)
    const [connected, setConnected] = useState(false)

    const loadProjectInfo = useCallback(() => {
        fetchProjectInfo()
            .then(setProjectInfo)
            .catch(() => setProjectInfo(null))
    }, [])

    // refreshToken 递增即触发全量重取。但**绝不能**每个 SSE 事件都递增：
    // 写一章会触碰几十个文件（state.json / index.db / 每个 .story-system/*.json），
    // watchdog 逐个推送，而 9 个页面共 29 处 useEffect 依赖 refreshToken。
    // 实测：20 次写事件 → 160 次额外 API 调用（每次事件 8 个请求）。
    //
    // 这里做 trailing debounce：一批紧密相连的文件事件收敛成一次刷新。
    // 300ms 的选取——写一章的落盘事件集中在几百毫秒内，再短会漏合并，
    // 再长会让用户觉得"改了没反应"。
    //
    // 与「过期响应丢弃」正交：debounce 减少的是**发起的请求数**，
    // 取消标记防的是**乱序落地**。两者都要有。
    useEffect(() => {
        let cancelled = false
        fetchProjectInfo()
            .then(data => { if (!cancelled) setProjectInfo(data) })
            .catch(() => { if (!cancelled) setProjectInfo(null) })
        return () => { cancelled = true }
    }, [refreshToken])

    useEffect(() => {
        let timer = null

        const scheduleRefresh = () => {
            if (timer !== null) clearTimeout(timer)
            timer = setTimeout(() => {
                timer = null
                startTransition(() => {
                    setRefreshToken(current => current + 1)
                })
            }, REFRESH_DEBOUNCE_MS)
        }

        const unsubscribe = subscribeSSE(
            scheduleRefresh,
            {
                onOpen: () => setConnected(true),
                onError: () => setConnected(false),
            },
        )

        return () => {
            if (timer !== null) clearTimeout(timer)
            unsubscribe()
            setConnected(false)
        }
    }, [])

    const title = projectInfo?.project_info?.title || '未加载项目'

    return (
        <div className="app-layout">
            <aside className="sidebar">
                <div className="sidebar-header">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <h1 style={{ margin: 0 }}>PIXEL WRITER HUB</h1>
                        <ThemeToggle />
                    </div>
                    <div className="subtitle" title={title}>{title}</div>
                </div>
                <nav className="sidebar-nav">
                    {NAV_ITEMS.map(item => {
                        const Icon = item.icon
                        return (
                            <NavLink
                                key={item.to}
                                to={item.to}
                                end={item.end}
                                className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`.trim()}
                            >
                                <span className="icon">
                                    <Icon />
                                </span>
                                <span>{item.label}</span>
                            </NavLink>
                        )
                    })}
                </nav>
                <div className="live-indicator">
                    <span className="icon">
                        {connected ? <WifiIcon /> : <WifiOffIcon />}
                    </span>
                    {connected ? '实时同步中' : '实时连接断开'}
                </div>
            </aside>

            <main className="main-content">
                <Outlet context={{ projectInfo, refreshToken, connected, reloadProjectInfo: loadProjectInfo }} />
            </main>
        </div>
    )
}

export function useDashboardContext() {
    return useOutletContext()
}
