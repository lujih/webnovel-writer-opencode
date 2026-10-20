import { render } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'

// 页面组件的测试脚手架。
//
// 为什么需要它：所有页面都通过 `useDashboardContext()` 取数据，而那是
// `useOutletContext()` —— 直接 render(<Page />) 会拿到 undefined 并在解构时崩。
// 必须让页面成为某个 `<Outlet context={...}/>` 的子路由。
//
// 刻意**不**提供默认 mock 数据：每个用例自己给 context，才能看清
// "这个页面拿到 X 时应当显示 Y"。隐式默认值会让测试悄悄依赖一份
// 没人维护的假数据。

/**
 * 渲染页面，并把 `context` 作为 Outlet context 注入。
 *
 * @param {React.ComponentType} Page
 * @param {object} context  传给 `useOutletContext()`，例如 { refreshToken: 0 }
 * @param {string} [route='/'] 初始路径
 */
export function renderPage(Page, context = {}, route = '/') {
  return render(buildPageTree(Page, context, route))
}

/**
 * 构造页面元素树（不 render）。
 *
 * 供需要 `rerender` 的用例使用——Outlet context 是闭合在树里的，
 * 想改变 context 必须重建整棵树（只换 props 不会让 useOutletContext 更新）。
 * `key` 用于强制重挂，避免 React 复用旧节点。
 */
export function buildPageTree(Page, context = {}, route = '/', key) {
  function Layout() {
    return <Outlet context={context} />
  }

  return (
    <MemoryRouter key={key} initialEntries={[route]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<Page />} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}
