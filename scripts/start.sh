#!/usr/bin/env bash
# Deep-tutor 本地直起（免 Docker，Linux/macOS）：
#   venv -> 依赖 -> .env -> 前端构建 -> 数据库迁移 -> 单进程启动（后端托管 SPA + /api，:41173）
# 用法：bash scripts/start.sh [--port 端口]
#       端口覆盖优先级：--port 参数 > PORT 环境变量 > 默认 41173
set -euo pipefail
cd "$(dirname "$0")/.."

# 端口参数解析（默认 41173：8000 常落入 Windows Hyper-V/winNAT 排除端口段，bind 失败）
PORT_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    -p|--port)
      [ $# -ge 2 ] || { echo "错误：$1 需要端口参数（用法：bash scripts/start.sh [--port 端口]）"; exit 1; }
      PORT_ARG=$2; shift 2;;
    --port=*)
      PORT_ARG=${1#*=}; shift;;
    -h|--help)
      echo "用法：bash scripts/start.sh [--port 端口]（默认 41173；亦可用 PORT 环境变量）"; exit 0;;
    *)
      echo "错误：未知参数 $1（用法：bash scripts/start.sh [--port 端口]）"; exit 1;;
  esac
done
PORT=${PORT_ARG:-${PORT:-41173}}
case "$PORT" in
  *[!0-9]*|"") echo "错误：端口须为 1-65535 的整数（当前：$PORT）"; exit 1;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "错误：端口须在 1-65535 之间（当前：$PORT）"; exit 1
fi

# 逐个候选**实跑**一遍：第一个既能执行、又满足 3.11+ 的胜出（err.md E2 的前置校验
# 随之完成）。PATH 上有 python3 不等于它能用——换机后常见的是指向已卸载解释器的
# 残留入口或版本过低的解释器。
PY=""
for cand in python3 python; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY=$cand; break
  fi
done
[ -n "$PY" ] || { echo "错误：未找到可用的 Python 3.11+（python3 / python 均不可用或版本过低）"; exit 1; }

# 1. 虚拟环境 + 依赖（幂等：已安装则 pip 秒过）
VENV_PY=backend/.venv/bin/python
# err.md E1：检测 venv python 可执行而非目录存在——venv 创建被中断时目录在但
# 无 python，此前后续运行跳过重建直接 pip 失败；残留半成品先清再建
# 「可用的 venv」＝解释器在 **且** 真跑得起来。换机/重装 Python 后，venv 仍指向旧的
# 基础解释器：文件在，pip 却报 "No Python at ..."，服务永远起不来。
VENV_USABLE=0
if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import sys' >/dev/null 2>&1; then
  VENV_USABLE=1
fi
if [ "$VENV_USABLE" -eq 0 ]; then
  [ -d backend/.venv ] && rm -rf backend/.venv
  echo "==> 创建虚拟环境 backend/.venv"
  "$PY" -m venv backend/.venv
fi
echo "==> 安装/校验后端依赖"
"$VENV_PY" -m pip install -q -r backend/requirements.txt
# OCR/PDF 深度解析栈（marker）：单独一个 requirements 文件——它与 Pillow 下限互斥，
# 混装在一个文件里 pip 判为无解（见 backend/requirements-ocr.txt 的说明）。
# 只在尚未安装时装一次：每次启动都重装它，会把 Pillow 一路拽回 <11（实测 pip 会为
# 已装包的旧约束降级 Pillow）。装不上不阻断启动——缺 marker 时 PDF 走 pypdf 快速档，
# full 档按 R9 报 dependency_missing 并提示安装。
# OCR/PDF 深度解析栈是可选的重型栈（marker + torch，约 2GB），装它会拖慢首次启动。
# 显式开关：SKIP_OCR_STACK=1 只装服务栈（PDF 走 pypdf 快速档）。
if [ "${SKIP_OCR_STACK:-}" = "1" ]; then
  echo "==> 按 SKIP_OCR_STACK=1 跳过 OCR/PDF 深度解析栈"
elif [ ! -x "backend/.venv/bin/marker_single" ]; then
  echo "==> 安装 OCR/PDF 深度解析栈（marker；体积较大，仅首次）"
  "$VENV_PY" -m pip install -q -r backend/requirements-ocr.txt || \
    echo "警告：OCR 栈安装失败——PDF 深度解析不可用，其余功能不受影响"
  # OCR 栈把 Pillow 拽回了 <11；服务进程必须留在安全版本（直接要求优先，pip 只警告不中止）
  "$VENV_PY" -m pip install -q -r backend/requirements.txt
fi

# 2. .env（iter 89 零配置开箱即用：缺失不再退出——后端以「开放本机模式」启动，
#    首次打开浏览器进入设置向导，可在界面内完成密码/API Key 配置；不代为生成 .env，
#    避免模板弱 token change-me-please 反成认证配置）
# iter 370 B18：零配置引导态默认只绑 127.0.0.1——开放模式在首个用户设密码前完全免认证，
# 绑 0.0.0.0 会把「任何人可认领」的服务暴露给局域网。有 .env（用户已显式配置）仍默认
# 0.0.0.0 供局域网访问；两种情况均可用 HOST 环境变量覆盖（如 HOST=0.0.0.0）。
# iter 394：即便绑 0.0.0.0，「首次设置向导」的两个写端点（/api/setup、/api/setup/dismiss）
# 在开放模式下也仅接受本机回环请求（它们是唯一的「认领管理员」入口）——局域网用户须在
# .env 配好 ADMIN_PASSWORD / AUTH_TOKEN 后再用向导。
if [ ! -f .env ]; then
  echo "==> 未找到 .env：以零配置模式启动（首次打开浏览器将引导完成初始设置）"
  HOST_DEFAULT=127.0.0.1
else
  HOST_DEFAULT=0.0.0.0
fi
HOST_BIND="${HOST:-$HOST_DEFAULT}"
# 开放模式提醒：.env 存在但没配真实凭证时，服务免登录且监听全部网卡（README §2.1）。
# 只提醒，不改绑定语义。
admin_pwd=$(sed -n 's/^[[:space:]]*ADMIN_PASSWORD[[:space:]]*=[[:space:]]*//p' .env 2>/dev/null | tail -n 1)
auth_tok=$(sed -n 's/^[[:space:]]*AUTH_TOKEN[[:space:]]*=[[:space:]]*//p' .env 2>/dev/null | tail -n 1)
if [ "$admin_pwd" = "change-me-please" ] || [ "$auth_tok" = "change-me-please" ] || [ -z "$admin_pwd" ]; then
  case "$HOST_BIND" in
    127.0.0.1|localhost) : ;;
    *) echo "警告：未配置真实 ADMIN_PASSWORD / AUTH_TOKEN —— 服务以免登录「开放模式」运行，且绑定 $HOST_BIND（局域网内可访问）。设置访问密码可关闭开放模式（见 README §2.1）。" >&2 ;;
  esac
fi

# 3. 前端构建产物（无 dist 且有 npm 则自动构建；两者皆无则报错）
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> 构建前端（frontend/dist）"
    (cd frontend && npm install && npm run build)
  else
    echo "错误：frontend/dist 不存在且未找到 npm，无法构建前端。请安装 Node.js 20+ 后重跑。"
    exit 1
  fi
fi
export FRONTEND_DIST=frontend/dist
export PYTHONPATH=backend

# 4. 数据库迁移（新库建表 / 旧库增量 / 无版本库自动接续）
echo "==> 数据库迁移"
"$VENV_PY" -m app.db.migrate

# 5. 启动（后端同端口托管 SPA 与 /api；HOST_BIND 见步骤 2；PORT 已在脚本头解析）
echo "==> 启动 Deep-tutor：http://localhost:${PORT}（绑定 ${HOST_BIND}；局域网访问需绑 0.0.0.0）"
# 自动打开浏览器（NO_BROWSER=1 关闭）：延迟 3s 等服务就绪
if [ "${NO_BROWSER:-}" != "1" ]; then
  ( sleep 3; xdg-open "http://localhost:${PORT}" >/dev/null 2>&1 \
      || open "http://localhost:${PORT}" >/dev/null 2>&1 || true ) &
fi
# 单进程运行（不加 --workers）：SSE 流 / 限流 / 并发配额 / 插件事件总线 / 后台任务 / LLM 信号量
# 均为进程内状态——多 worker 下各进程独立（SSE 续接/取消、配额计数、事件推送会失准）。
# 多进程部署需 sticky session + Redis 共享这些状态（当前未实现，按单进程设计）。
exec "$VENV_PY" -m uvicorn app.main:app --host "$HOST_BIND" --port "$PORT" --app-dir backend --timeout-graceful-shutdown 20
