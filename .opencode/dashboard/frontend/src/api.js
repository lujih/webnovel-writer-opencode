const BASE = ''

/**
 * 带 HTTP 状态码的错误。
 *
 * 页面需要区分「这个资源本来就没有」（404，属于正常状态，该给引导文案）
 * 与「服务真的坏了」（5xx / 网络错误，该给红色报错）。此前一律抛
 * `new Error("404 Not Found")`，调用方只能拿字符串比对，于是上下文健康页
 * 把"还没生成快照"渲染成了"加载失败"。
 */
export class HttpError extends Error {
    constructor(status, statusText, detail) {
        super(detail || `${status} ${statusText}`)
        this.name = 'HttpError'
        this.status = status
        this.statusText = statusText
        this.detail = detail || ''
    }

    get isNotFound() {
        return this.status === 404
    }
}

export async function fetchJSON(path, params = {}) {
    const url = new URL(`${BASE}${path}`, window.location.origin)
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') {
            url.searchParams.set(key, value)
        }
    }

    const response = await fetch(url.toString())
    if (!response.ok) {
        // FastAPI 的错误体形如 {"detail": "..."}，能读到就用它，
        // 读不到（HTML 错误页）就退回状态文本。
        let detail = ''
        try {
            const body = await response.json()
            detail = typeof body?.detail === 'string' ? body.detail : ''
        } catch { /* 非 JSON 响应，忽略 */ }
        throw new HttpError(response.status, response.statusText, detail)
    }
    return response.json()
}

export function fetchProjectInfo() {
    return fetchJSON('/api/project/info')
}

export function fetchStoryRuntimeHealth() {
    return fetchJSON('/api/story-runtime/health')
}

export function fetchChapterTrend(params = {}) {
    return fetchJSON('/api/stats/chapter-trend', params)
}

export function fetchChapters() {
    return fetchJSON('/api/chapters')
}

export function fetchEntities(params = {}) {
    return fetchJSON('/api/entities', params)
}

export function fetchStateChanges(params = {}) {
    return fetchJSON('/api/state-changes', params)
}

export function fetchRelationships(params = {}) {
    return fetchJSON('/api/relationships', params)
}

export function fetchRelationshipEvents(params = {}) {
    return fetchJSON('/api/relationship-events', params)
}

export function fetchCommits(params = {}) {
    return fetchJSON('/api/commits', params)
}

export function fetchContractsSummary() {
    return fetchJSON('/api/contracts/summary')
}

export function fetchEnvStatus() {
    return fetchJSON('/api/env-status')
}

export function probeEnvStatus() {
    return fetchJSON('/api/env-status/probe')
}

export function fetchFilesTree() {
    return fetchJSON('/api/files/tree')
}

export function fetchFileContent(path) {
    return fetchJSON('/api/files/read', { path })
}

export function saveFileContent(path, content) {
    return apiPut('/api/files/write', { path, content })
}

// --- 写入 helper ---

export async function apiPost(path, body = {}) {
    const response = await fetch(`${BASE}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (!response.ok) {
        const err = await response.json().catch(() => ({}))
        throw new Error(err.detail || `${response.status} ${response.statusText}`)
    }
    return response.json()
}

export async function apiPut(path, body = {}) {
    const response = await fetch(`${BASE}${path}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (!response.ok) {
        const err = await response.json().catch(() => ({}))
        throw new Error(err.detail || `${response.status} ${response.statusText}`)
    }
    return response.json()
}

export async function apiDelete(path) {
    const response = await fetch(`${BASE}${path}`, { method: 'DELETE' })
    if (!response.ok) {
        const err = await response.json().catch(() => ({}))
        throw new Error(err.detail || `${response.status} ${response.statusText}`)
    }
    return response.json()
}

// --- 文风约束 API ---

export function fetchMasterSetting() {
    return fetchJSON('/api/style/master-setting')
}

export function updateMasterSetting(masterConstraints) {
    return apiPut('/api/style/master-setting', { master_constraints: masterConstraints })
}

export function fetchAntiPatterns() {
    return fetchJSON('/api/style/anti-patterns')
}

export function addAntiPattern(text) {
    return apiPost('/api/style/anti-patterns', { text })
}

export function deleteAntiPattern(text) {
    return apiDelete(`/api/style/anti-patterns?text=${encodeURIComponent(text)}`)
}

export function fetchTechniques() {
    return fetchJSON('/api/style/techniques')
}

export function fetchChapterContracts() {
    return fetchJSON('/api/style/chapters')
}

export function fetchChapterContract(chapter) {
    return fetchJSON(`/api/style/chapters/${chapter}`)
}

export function fetchReviewerChecklist() {
    return fetchJSON('/api/style/reviewer-checklist')
}

export function fetchPrompts() {
    return fetchJSON('/api/style/prompts')
}

export function createPrompt(name, content) {
    return apiPost('/api/style/prompts', { name, content })
}

export function updatePrompt(filename, content) {
    return apiPut(`/api/style/prompts/${encodeURIComponent(filename)}`, { content })
}

export function deletePrompt(filename) {
    return apiDelete(`/api/style/prompts/${encodeURIComponent(filename)}`)
}

export function fetchContextHealth(chapter) {
    return fetchJSON(`/api/context/health/${chapter}`)
}

export function fetchContextHistory(limit = 20) {
    return fetchJSON('/api/context/history', { limit })
}

export function fetchEntityTimeline(entityId) {
    return fetchJSON(`/api/entities/${encodeURIComponent(entityId)}/timeline`)
}

export function fetchConsistencyAnomalies(chapter) {
    const params = chapter ? { chapter } : {}
    return fetchJSON('/api/consistency/anomalies', params)
}

export function fetchReviewAnalytics(limit = 50) {
    return fetchJSON('/api/review/analytics', { limit })
}

export function fetchForeshadowingReminders(threshold = 5) {
    return fetchJSON('/api/foreshadowing/reminders', { threshold })
}

// --- 追读债 ---

export function fetchDebtsSummary(limit = 50) {
    return fetchJSON('/api/debts/summary', { limit })
}

export function fetchDebtDetail(debtId) {
    return fetchJSON(`/api/debts/${encodeURIComponent(debtId)}`)
}

export function runBatchAction(action, chapters, confirm = false) {
    return apiPost(`/api/batch/${action}`, { chapters, confirm })
}

export function subscribeSSE(onMessage, handlers = {}) {
    const { onOpen, onError } = handlers
    const eventSource = new EventSource(`${BASE}/api/events`)

    eventSource.onopen = () => {
        if (onOpen) onOpen()
    }

    eventSource.onmessage = event => {
        try {
            onMessage(JSON.parse(event.data))
        } catch { /* ignore non-JSON messages */ }
    }

    eventSource.onerror = error => {
        if (onError) onError(error)
    }

    return () => eventSource.close()
}
