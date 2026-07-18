/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class', // 深浅双主题（plan/06 §一）：.dark 挂 <html>，useUIStore 切换
  theme: {
    extend: {},
  },
  plugins: [],
}
