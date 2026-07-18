/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class', // 深浅双主题（plan/06 §一）：.dark 挂 <html>，useUIStore 切换
  theme: {
    extend: {
      // iter 92 前端美化：统一动画关键帧（均受 index.css prefers-reduced-motion 兜底关闭）
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'fade-in-up': {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(.96)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'slide-in-left': {
          from: { transform: 'translateX(-100%)' },
          to: { transform: 'translateX(0)' },
        },
        // 表单错误轻晃（登录/设置向导校验失败反馈）
        shake: {
          '10%, 90%': { transform: 'translateX(-1px)' },
          '20%, 80%': { transform: 'translateX(2px)' },
          '30%, 50%, 70%': { transform: 'translateX(-3px)' },
          '40%, 60%': { transform: 'translateX(3px)' },
        },
      },
      animation: {
        'fade-in': 'fade-in .25s ease-out both',
        'fade-in-up': 'fade-in-up .35s cubic-bezier(.21,1.02,.73,1) both',
        'scale-in': 'scale-in .2s ease-out both',
        'slide-in-left': 'slide-in-left .25s ease-out both',
        shake: 'shake .4s ease-in-out both',
      },
    },
  },
  plugins: [],
}
