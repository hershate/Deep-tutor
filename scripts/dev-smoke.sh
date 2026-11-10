#!/usr/bin/env bash
# 116#3：dev server 冒烟（make dev-smoke / make check 内跑）——
# 111#2 实证 vite 配置错误可让全部既有门禁绿灯而 dev 工作流 403。
# 专用高位端口 43198 + strictPort，与开发者常驻的 1420 隔离；
# 结束时按 netstat 找到的监听 PID 精确停服，不按进程名广杀 node。
set -u
cd "$(dirname "$0")/../desktop"
PORT=43198
npm run dev -- --port "$PORT" --strictPort >/dev/null 2>&1 &
launcher=$!
cleanup() {
  for pid in $(netstat -ano | grep ":$PORT" | grep LISTENING | awk '{print $5}' | sort -u); do
    taskkill //F //PID "$pid" >/dev/null 2>&1 || true
  done
  kill "$launcher" >/dev/null 2>&1 || true
}
trap cleanup EXIT
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    echo "dev server 冒烟通过（http://127.0.0.1:$PORT/ 返回 200）"
    exit 0
  fi
  sleep 1
done
echo "dev server 冒烟失败：20s 内 index 未返回 200（检查 vite 配置与依赖）" >&2
exit 1
