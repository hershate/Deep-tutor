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
    // 110#1（37 轮修正）：fs.allow=['src'] 实测会把根 index.html 与 vite
    // client 一并挡掉（dev server 403 全挂）——回退为 vite 默认工作区根；
    // server.fs.deny Windows 备用路径绕过等通告族已由 vite 6.4.6 本体修复
  },
})
