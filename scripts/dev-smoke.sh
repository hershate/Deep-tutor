#!/usr/bin/env bash
# 116#3：dev server 冒烟（make dev-smoke / make check 内跑）——
# 111#2 实证 vite 配置错误可让全部既有门禁绿灯而 dev 工作流 403。
# 专用高位端口 43198 + strictPort，与开发者常驻的 1420 隔离。
# 131#1/#2/#9：启动前先探测端口空闲——被占即失败退出，不启动 vite、
# 不杀任何进程（原先 cleanup 按 netstat 找监听 PID 强杀会误杀无关程序；
# 且端口被占时 strictPort 使 vite 启动即死，curl 应答的是占用者——
# 仅看 200 状态码会假绿）；cleanup 只终止自己启动的进程树。
set -u
cd "$(dirname "$0")/../desktop"
PORT=43198

# 131#1：占用者可能是残留的 dev server，也可能是无关本地服务——归属无法
# 自动判定，直接失败交人工处理，本脚本不代杀。LISTEN 同时命中 Windows 的
# LISTENING 与类 Unix 的 LISTEN（非 Windows 探测为尽力而为）
if netstat -ano 2>/dev/null | grep ":$PORT" | grep -q LISTEN; then
  echo "dev server 冒烟失败：端口 $PORT 已被占用（残留 dev server 或无关本地服务），请手动确认并释放后重试" >&2
  exit 1
fi

npm run dev -- --port "$PORT" --strictPort >/dev/null 2>&1 &
launcher=$!

# 131#1/#9：只终止自己启动的 $launcher 进程树，不按端口找 PID 强杀
# （占用者可能是无关程序）。Windows：taskkill //T 连 npm 包装进程带
# vite 子进程整树终止；非 Windows（本仓库开发与 CI 均 Windows，此分支
# 为尽力而为）：先 pkill 子进程再 kill 包装进程——顺序不能反，先杀父
# 会让 vite 被 init 收养，pkill -P 随后按旧父 pid 查不到而留孤儿
cleanup() {
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //F //T //PID "$launcher" >/dev/null 2>&1 || true
  else
    pkill -P "$launcher" >/dev/null 2>&1 || true
    kill "$launcher" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# 131#2：仅「GET / 返回 200」不足——端口被占且占用者恰好应答 200 时假绿；
# 须再断言响应体含本应用标记（desktop/index.html 的 <div id="root">）
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q '<div id="root">'; then
    echo "dev server 冒烟通过（http://127.0.0.1:$PORT/ 返回含 <div id=\"root\"> 的本应用 index）"
    exit 0
  fi
  sleep 1
done
echo "dev server 冒烟失败：20s 内未取回含 <div id=\"root\"> 的本应用 index（检查 vite 配置与依赖）" >&2
exit 1
