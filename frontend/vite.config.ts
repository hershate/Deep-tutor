import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite 配置（plan/01 前端栈）。
// dev: 5173；/api 反代到后端 8000（CORS_ORIGINS 默认允许 5173）。
export default defineConfig({
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
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
