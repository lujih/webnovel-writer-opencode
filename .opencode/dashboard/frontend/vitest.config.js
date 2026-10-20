import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端单测配置。
//
// 分两个环境跑，而不是全局切成 jsdom：
//   * lib/ 与 api 的纯函数测试留在 **node** —— 它们不碰 DOM，跑得更快，
//     而且能顺手证明这些模块没有意外依赖浏览器全局；
//   * 组件测试用 **jsdom** —— 需要 document / window。
//
// 之前是 environment: 'node' 单环境、只测纯函数。补组件层的起因：11 个页面的
// loading / 空态 / 竞态全靠人工截图，回归了没人知道。组件测试覆盖的是
// **状态与渲染的对应关系**（给了什么 props、渲染出什么、点了按钮发生什么），
// 不覆盖真实网络与 ECharts 画布（后者在 test-setup.js 里 stub）。
export default defineConfig({
  plugins: [react()],
  test: {
    reporters: 'default',
    projects: [
      {
        // 纯函数：node 环境
        extends: true,
        test: {
          name: 'unit',
          environment: 'node',
          include: ['src/lib/**/*.test.js', 'src/api.test.js'],
        },
      },
      {
        // 组件：jsdom 环境
        extends: true,
        test: {
          name: 'component',
          environment: 'jsdom',
          include: ['src/components/**/*.test.jsx', 'src/pages/**/*.test.jsx'],
          setupFiles: ['./src/test-setup.js'],
        },
      },
    ],
  },
})
