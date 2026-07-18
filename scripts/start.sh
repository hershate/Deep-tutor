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

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "错误：未找到 Python 3.11+，请先安装"; exit 1; }
# err.md E2：前置版本校验（低版本建 venv 后到运行期才报错，排障困难）
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "错误：需要 Python 3.11+（当前：$("$PY" -V 2>&1)）"; exit 1; }

# 1. 虚拟环境 + 依赖（幂等：已安装则 pip 秒过）
VENV_PY=backend/.venv/bin/python
# err.md E1：检测 venv python 可执行而非目录存在——venv 创建被中断时目录在但
# 无 python，此前后续运行跳过重建直接 pip 失败；残留半成品先清再建
if [ ! -x "$VENV_PY" ]; then
  [ -d backend/.venv ] && rm -rf backend/.venv
  echo "==> 创建虚拟环境 backend/.venv"
  "$PY" -m venv backend/.venv
fi
echo "==> 安装/校验后端依赖"
"$VENV_PY" -m pip install -q -r backend/requirements.txt

# 2. .env（iter 89 零配置开箱即用：缺失不再退出——后端以「开放本机模式」启动，
#    首次打开浏览器进入设置向导，可在界面内完成密码/API Key 配置；不代为生成 .env，
#    避免模板弱 token change-me-please 反成认证配置）
if [ ! -f .env ]; then
  echo "==> 未找到 .env：以零配置模式启动（首次打开浏览器将引导完成初始设置）"
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

# 5. 启动（后端同端口托管 SPA 与 /api；0.0.0.0 供局域网访问；PORT 已在脚本头解析）
echo "==> 启动 Deep-tutor：http://localhost:${PORT}（局域网 http://<本机IP>:${PORT}）"
# 自动打开浏览器（NO_BROWSER=1 关闭）：延迟 3s 等服务就绪
if [ "${NO_BROWSER:-}" != "1" ]; then
  ( sleep 3; xdg-open "http://localhost:${PORT}" >/dev/null 2>&1 \
      || open "http://localhost:${PORT}" >/dev/null 2>&1 || true ) &
fi
exec "$VENV_PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --app-dir backend
