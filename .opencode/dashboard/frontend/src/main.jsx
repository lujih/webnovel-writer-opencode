import React, { Suspense, lazy, Component } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import App from './App.jsx'
import './index.css'

class ErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { hasError: false, error: null }
    }
    static getDerivedStateFromError(error) {
        return { hasError: true, error }
    }
    render() {
        if (this.state.hasError) {
            return (
                <div className="loading-screen">
                    <div className="loading-card">
                        <div className="section-label">ERROR</div>
                        <p>页面渲染出错，请刷新重试。</p>
                        <button className="page-btn" onClick={() => { this.setState({ hasError: false }); window.location.reload() }}>
                            刷新页面
                        </button>
                    </div>
                </div>
            )
        }
        return this.props.children
    }
}

const OverviewPage = lazy(() => import('./pages/OverviewPage.jsx'))
const CharactersPage = lazy(() => import('./pages/CharactersPage.jsx'))
const PacingPage = lazy(() => import('./pages/PacingPage.jsx'))
const ForeshadowingPage = lazy(() => import('./pages/ForeshadowingPage.jsx'))
const DebtPage = lazy(() => import('./pages/DebtPage.jsx'))
const DiagnosticsPage = lazy(() => import('./pages/DiagnosticsPage.jsx'))
const FilesPage = lazy(() => import('./pages/FilesPage.jsx'))
const SystemPage = lazy(() => import('./pages/SystemPage.jsx'))
const StyleEditorPage = lazy(() => import('./pages/StyleEditorPage.jsx'))
const ContextHealthPage = lazy(() => import('./pages/ContextHealthPage.jsx'))
const ReviewAnalyticsPage = lazy(() => import('./pages/ReviewAnalyticsPage.jsx'))

function LoadingScreen() {
    return (
        <div className="loading-screen">
            <div className="loading-card">
                <div className="section-label">LOADING</div>
                <p>正在加载 Dashboard…</p>
            </div>
        </div>
    )
}

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <BrowserRouter>
            <ErrorBoundary>
            <Suspense fallback={<LoadingScreen />}>
                <Routes>
                    <Route path="/" element={<App />}>
                        <Route index element={<OverviewPage />} />
                        <Route path="characters" element={<CharactersPage />} />
                        <Route path="pacing" element={<PacingPage />} />
                        <Route path="foreshadowing" element={<ForeshadowingPage />} />
                        <Route path="debts" element={<DebtPage />} />
                        <Route path="files" element={<FilesPage />} />
                        <Route path="system" element={<SystemPage />} />
                        <Route path="diagnostics" element={<DiagnosticsPage />} />
                        <Route path="style" element={<StyleEditorPage />} />
                        <Route path="context" element={<ContextHealthPage />} />
                        <Route path="review" element={<ReviewAnalyticsPage />} />
                        <Route path="*" element={<Navigate to="/" replace />} />
                    </Route>
                </Routes>
            </Suspense>
            </ErrorBoundary>
        </BrowserRouter>
    </React.StrictMode>,
)
