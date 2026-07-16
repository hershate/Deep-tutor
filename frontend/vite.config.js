import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// Vite 配置（plan/01 前端栈）。
// dev: 5173；/api 反代到后端 8000（CORS_ORIGINS 默认允许 5173）。
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
            },
        },
    },
});
