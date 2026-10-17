import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置（plan/01 前端栈）。
// dev: 5173；/api 反代到后端（默认 :8000，uvicorn 默认端口；CORS_ORIGINS 默认允许 5173）。
export default defineConfig(({ mode }) => {
  // iter 370 B18：后端若起在其他端口（如复用 start 脚本的 41173），用
  // VITE_API_PROXY_TARGET（.env.* 或 shell 环境变量）覆盖代理目标，免改代码。
  // loadEnv 第三参 '' = 不过滤前缀，process.env 与 .env.* 均可见（且不引入 node 类型）
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    build: {
      // iter 103：vendor 分包——react 系与 markdown 渲染系各自独立 chunk，
      // 利用浏览器长缓存（node_modules 内容稳定），主包随业务迭代变化
      rollupOptions: {
        output: {
          manualChunks: {
            'react-vendor': ['react', 'react-dom', 'react-router-dom'],
            'markdown-vendor': ['react-markdown', 'remark-gfm', 'rehype-sanitize'],
          },
        },
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
