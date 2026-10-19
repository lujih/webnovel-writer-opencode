import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'

// 刻意保守：只开两条规则集。
//
// 这个前端此前零 lint，一次性引入全套 recommended 会报出上百条历史问题，
// 结果是"改成禁用"或"长期带病"。因此先只启用**能抓真 bug**的两组：
//   - js.configs.recommended：未定义变量、重复声明、不可达代码
//   - react-hooks：条件调用 Hook、依赖数组缺失（这个面板大量用
//     useEffect + refreshToken，依赖写错会导致数据不刷新或无限循环）
//
// 格式化类规则（缩进/引号/分号）一律不开——本项目用 4 空格，与 eslint 默认
// 冲突，且这类问题由人眼和编辑器负责，不该让 CI 变红。
export default [
    {
        ignores: ['dist/**', 'node_modules/**'],
    },
    js.configs.recommended,
    {
        files: ['src/**/*.{js,jsx}'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            parserOptions: {
                ecmaFeatures: { jsx: true },
            },
            globals: {
                window: 'readonly',
                document: 'readonly',
                localStorage: 'readonly',
                fetch: 'readonly',
                EventSource: 'readonly',
                console: 'readonly',
                URL: 'readonly',
                setTimeout: 'readonly',
                clearTimeout: 'readonly',
                Intl: 'readonly',
                ResizeObserver: 'readonly',
                requestAnimationFrame: 'readonly',
                cancelAnimationFrame: 'readonly',
                // 浏览器内置、但一份手写 globals 表很容易漏掉的：
                confirm: 'readonly',
                alert: 'readonly',
                AbortController: 'readonly',
                FormData: 'readonly',
                File: 'readonly',
                Blob: 'readonly',
                navigator: 'readonly',
            },
        },
        plugins: {
            'react-hooks': reactHooks,
        },
        rules: {
            ...reactHooks.configs.recommended.rules,
            // 未使用变量降级为警告：历史代码里有一些预留参数，
            // 但**报出来**比静默删掉安全——删错会改变行为。
            'no-unused-vars': ['warn', { args: 'none', varsIgnorePattern: '^_' }],
            // JSX 里 <Icon /> 这类大写标识符 u 会被判为未使用，交给构建处理
            'no-undef': 'error',
            // 空 catch 是有意的：localStorage 在隐私模式/配额满时会抛，
            // 而"主题没存上"不该让页面白屏。允许 `catch {}` 但禁止空 if/for。
            'no-empty': ['error', { allowEmptyCatch: true }],

            // ⚠️ 这条规则在 React 19 的 eslint-plugin-react-hooks 里才出现，
            // 会把"effect 里同步调 setState"判为 error。本项目 9 个页面全都
            // 是这个写法（useEffect → setLoading(true) → fetch → setData），
            // 一次性重构 17 处的风险远大于收益，且与本次加固无关。
            // 因此降为 warn 并**留在这里显式说明**——将来若要把数据层换成
            // SWR/react-query，这条会自然消失，那时再升回 error。
            'react-hooks/set-state-in-effect': 'warn',
        },
    },
    {
        // 测试文件允许 describe/it/expect 等全局
        files: ['src/**/*.test.js', 'vitest.config.js'],
        languageOptions: {
            globals: {
                describe: 'readonly',
                it: 'readonly',
                expect: 'readonly',
                beforeEach: 'readonly',
                afterEach: 'readonly',
                vi: 'readonly',
            },
        },
    },
]
