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
    // 110#1：dev server 文件访问面显式收窄到 src——vite ≤6.4.3 的
    // server.fs.deny Windows 备用路径绕过等通告族（已随 vite 6.4.6 修复）
    // 再叠一层显式 allow 防未来回移
    fs: { allow: ['src'] },
  },
})
