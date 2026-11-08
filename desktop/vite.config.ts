import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
    // 49#12：dev 模式下 src-tauri 的构建产物变化不触发前端无谓重载/清屏
    watch: { ignored: ['**/src-tauri/**'] },
  },
})
