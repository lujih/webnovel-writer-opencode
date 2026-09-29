import { startTransition, useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet, useOutletContext } from 'react-router-dom'
import { fetchProjectInfo, subscribeSSE } from './api.js'
import {
    BookmarkIcon,
    ChartBarIcon,
    ClipboardIcon,
    FolderIcon,
    LayersIcon,
    PenIcon,
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
    { to: '/files', label: '文档浏览', icon: FolderIcon },
    { to: '/style', label: '文风约束', icon: PenIcon },
    { to: '/system', label: '系统状态', icon: SlidersIcon },
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

export default function App() {
    const [projectInfo, setProjectInfo] = useState(null)
    const [refreshToken, setRefreshToken] = useState(0)
    const [connected, setConnected] = useState(false)

    const loadProjectInfo = useCallback(() => {
        fetchProjectInfo()
            .then(setProjectInfo)
            .catch(() => setProjectInfo(null))
    }, [])

    // refreshToken 由**每一个** SSE 事件递增：写一章会触碰几十个文件，
    // 于是短时间内并发发起大量 fetchProjectInfo()。不丢弃过期响应时，先发的
    // 慢请求可能在后发的快请求之后落地，把页头字���/章数刷成旧值。
    useEffect(() => {
        let cancelled = false
        fetchProjectInfo()
            .then(data => { if (!cancelled) setProjectInfo(data) })
            .catch(() => { if (!cancelled) setProjectInfo(null) })
        return () => { cancelled = true }
    }, [refreshToken])

    useEffect(() => {
        const unsubscribe = subscribeSSE(
            () => {
                startTransition(() => {
                    setRefreshToken(current => current + 1)
                })
            },
            {
                onOpen: () => setConnected(true),
                onError: () => setConnected(false),
            },
        )

        return () => {
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
