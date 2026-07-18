#!/usr/bin/env bash
# Deep-tutor 本地直起（免 Docker，Linux/macOS）：
#   venv -> 依赖 -> .env -> 前端构建 -> 数据库迁移 -> 单进程启动（后端托管 SPA + /api，:8000）
# 用法：bash scripts/start.sh    （端口覆盖：PORT=9000 bash scripts/start.sh）
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || { echo "错误：未找到 Python 3.11+，请先安装"; exit 1; }

# 1. 虚拟环境 + 依赖（幂等：已安装则 pip 秒过）
if [ ! -d backend/.venv ]; then
  echo "==> 创建虚拟环境 backend/.venv"
  "$PY" -m venv backend/.venv
fi
VENV_PY=backend/.venv/bin/python
echo "==> 安装/校验后端依赖"
"$VENV_PY" -m pip install -q -r backend/requirements.txt

# 2. .env（缺失则从模板生成；必填项见 README 部署章节）
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> 已生成 .env：请编辑填写 JWT_SECRET / ADMIN_PASSWORD / ANTHROPIC_API_KEY 后重新运行本脚本"
  exit 0
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

# 5. 启动（后端同端口托管 SPA 与 /api；0.0.0.0 供局域网访问）
PORT=${PORT:-8000}
echo "==> 启动 Deep-tutor：http://localhost:${PORT}（局域网 http://<本机IP>:${PORT}）"
exec "$VENV_PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --app-dir backend
