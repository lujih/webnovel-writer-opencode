import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端单测配置。
//
// 范围刻意保守：只测**纯函数**（lib/ 下的格式化、分箱、伏笔聚合），
// 不测组件渲染。理由是这个面板的 bug 大多出在"数据形状 → 展示值"这一段
// （格式化崩溃、分位算错、状态判定反了），而不是 React 本身的渲染时序；
// 给 9 个页面都补渲染测试的成本远高于收益。
//
// 后续若要加组件测试，把 environment 换成 jsdom 即可（依赖已装）。
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
    reporters: 'default',
  },
})
