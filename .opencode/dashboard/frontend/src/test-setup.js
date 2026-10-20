import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// 组件测试的运行环境补丁。
//
// 这里只做两件事：补 jsdom 缺的浏览器 API，以及 stub 掉无法在 jsdom 里跑的
// 第三方渲染器（ECharts 依赖 canvas）。**不要**在这里 mock 业务模块——
// 那样测的就不是真实组件了。

// 每个用例后清理 DOM，避免上一个用例的节点泄漏到下一个。
afterEach(() => {
  cleanup()
})

// jsdom 没有 matchMedia，ECharts 与部分主题逻辑会读它。
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

// jsdom 没有 ResizeObserver，ECharts 容器尺寸监听会崩。
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// jsdom 未实现 canvas。ECharts 初始化会直接抛
// "Not implemented: HTMLCanvasElement.prototype.getContext"。
//
// 返回一个**最小可用的 2D 上下文桩**，而不是 null：ECharts 在组件卸载时
// 会调 clearRect / 读写 dpr，返回 null 会让清理阶段炸出
// "Cannot read properties of null (reading 'clearRect')" ——
// 那会把被测页面本身的问题淹没在无关报错里。
//
// 组件测试只关心"图表容器挂载了没有"，不验证像素。真正验证图画得对不对，
// 靠 Playwright 真机截图 + 统计 canvas 非空像素比例（见雷达图空白的验证）。
const NOOP = () => {}
const FAKE_2D_CONTEXT = {
  canvas: null,
  save: NOOP, restore: NOOP, clearRect: NOOP, fillRect: NOOP, strokeRect: NOOP,
  beginPath: NOOP, closePath: NOOP, moveTo: NOOP, lineTo: NOOP, arc: NOOP,
  bezierCurveTo: NOOP, quadraticCurveTo: NOOP, rect: NOOP, ellipse: NOOP,
  fill: NOOP, stroke: NOOP, clip: NOOP, translate: NOOP, rotate: NOOP,
  scale: NOOP, setTransform: NOOP, resetTransform: NOOP, transform: NOOP,
  drawImage: NOOP, putImageData: NOOP, setLineDash: NOOP,
  createLinearGradient: () => ({ addColorStop: NOOP }),
  createRadialGradient: () => ({ addColorStop: NOOP }),
  createPattern: () => null,
  measureText: () => ({ width: 0 }),
  fillText: NOOP, strokeText: NOOP,
  getImageData: () => ({ data: [] }),
  globalAlpha: 1, globalCompositeOperation: 'source-over',
  fillStyle: '#000', strokeStyle: '#000', lineWidth: 1, font: '',
  textAlign: 'start', textBaseline: 'alphabetic', lineCap: 'butt', lineJoin: 'miter',
}

HTMLCanvasElement.prototype.getContext = vi.fn(function () {
  const ctx = Object.create(FAKE_2D_CONTEXT)
  ctx.canvas = this
  return ctx
})

// jsdom 的 scrollTo 是空函数会告警，组件里偶有调用
window.scrollTo = vi.fn()
