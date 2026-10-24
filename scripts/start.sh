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

# 1. 虚拟环境 + 依赖（幂等：已装好则秒过）
# iter 424：本项目的虚拟环境改由 **uv** 创建（落在项目内 backend/.venv），并加一段
# 「uv 环境检查」——uv 在不在、版本、venv 健不健康、venv 是不是 uv 建的。
VENV_DIR=backend/.venv
VENV_PY=$VENV_DIR/bin/python
VENV_CFG=$VENV_DIR/pyvenv.cfg

# uv 的落盘位置：uv 默认把缓存放在 ~/.cache/uv（C 盘上的用户目录）——与本仓
# 「可丢弃产物落项目所在卷」的规约相悖；且缓存与 venv 跨卷时硬链接失效，uv 会退化为
# 全量拷贝。故默认指到**项目内**的 .uv-cache：跟着仓库走（与 backend/.venv 同卷，
# 硬链接可用），随仓库走也意味着换机后不必重下。已显式设置 UV_CACHE_DIR 则尊重之。
[ -n "${UV_CACHE_DIR:-}" ] || UV_CACHE_DIR="$(pwd)/.uv-cache"
export UV_CACHE_DIR

# uv 检查：显式指定 DEEPTUTOR_UV 优先，否则用 PATH 上的 uv；每个候选都要**实跑**
# 一次 --version（找得到不等于跑得起来）。Windows 版另优先用本机 F 盘那份（F:\uv\bin）。
UV_CMD=""
UV_SOURCE=""
if [ "${SKIP_UV:-}" != "1" ]; then
  for cand in "${DEEPTUTOR_UV:-}" uv; do
    [ -n "$cand" ] || continue
    if [ "$cand" = "uv" ]; then
      command -v uv >/dev/null 2>&1 || continue
    elif [ ! -x "$cand" ]; then
      continue
    fi
    if "$cand" --version >/dev/null 2>&1; then
      UV_CMD=$cand
      if [ "$cand" = "uv" ]; then UV_SOURCE="PATH"; else UV_SOURCE=$cand; fi
      break
    fi
  done
fi
if [ -n "$UV_CMD" ]; then
  echo "==> uv：$("$UV_CMD" --version)（$UV_SOURCE；缓存 $UV_CACHE_DIR）"
elif [ "${SKIP_UV:-}" = "1" ]; then
  echo "==> 按 SKIP_UV=1 跳过 uv：虚拟环境用标准库 venv + pip"
else
  echo "==> 未检测到可用的 uv：回退到标准库 venv + pip（装 uv 可快很多：https://docs.astral.sh/uv/）"
fi

# 「可用的 venv」＝解释器在 **且** 真跑得起来。换机/重装 Python 后，venv 仍指向旧的
# 基础解释器：文件在，pip 却报 "No Python at ..."，服务永远起不来。
# 另记：这 venv 是不是 uv 建的（uv 在 pyvenv.cfg 里写 `uv = <版本>`）。
VENV_USABLE=0
VENV_BY_UV=0
if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import sys' >/dev/null 2>&1; then
  VENV_USABLE=1
  if [ -f "$VENV_CFG" ] && grep -q '^uv[[:space:]]*=' "$VENV_CFG" 2>/dev/null; then
    VENV_BY_UV=1
  fi
fi
if [ "$VENV_USABLE" -eq 1 ] && [ -n "$UV_CMD" ] && [ "$VENV_BY_UV" -eq 0 ]; then
  # 一次性迁移：项目内的 venv 一律由 uv 建（用户可用 SKIP_UV=1 退出本行为）
  echo "==> 现有 backend/.venv 不是 uv 建的，重建（SKIP_UV=1 可保留它）"
  VENV_USABLE=0
fi
if [ "$VENV_USABLE" -eq 0 ]; then
  [ -d "$VENV_DIR" ] && rm -rf "$VENV_DIR"
  if [ -n "$UV_CMD" ]; then
    # 用已探测到的解释器（≥3.11）建 venv，落在项目内 backend/.venv。
    # --seed：把 pip 装进 venv——OCR 栈那段仍走 pip（见下），且没有 pip 的 venv
    # 跑 `python -m pip` 会直接报 No module named pip。
    PY_ABS=$("$PY" -c 'import sys; print(sys.executable)' 2>/dev/null)
    echo "==> 用 uv 创建虚拟环境 backend/.venv（解释器 $PY_ABS）"
    "$UV_CMD" venv --seed --python "$PY_ABS" "$VENV_DIR" || {
      echo "错误：uv venv 失败"; exit 1
    }
  else
    echo "==> 创建虚拟环境 backend/.venv"
    "$PY" -m venv "$VENV_DIR" || { echo "错误：创建虚拟环境失败"; exit 1; }
  fi
  # 建完立刻复检：venv 的解释器必须真能跑（uv 建的也要验，不假设）
  if ! "$VENV_PY" -c 'import sys' >/dev/null 2>&1; then
    echo "错误：新建的虚拟环境不可用（$VENV_PY 跑不起来）"; exit 1
  fi
fi
echo "==> 安装/校验后端依赖"
if [ -n "$UV_CMD" ]; then
  "$UV_CMD" pip install --python "$VENV_PY" -r backend/requirements.txt || {
    echo "警告：uv pip install 失败，回退到 pip 重试" >&2
    "$VENV_PY" -m pip install -q -r backend/requirements.txt || {
      echo "错误：pip install 失败"; exit 1
    }
  }
else
  "$VENV_PY" -m pip install -q -r backend/requirements.txt || {
    echo "错误：pip install 失败"; exit 1
  }
fi
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
# 开放模式提醒：.env 存在但没配真实凭证时，服务可能免登录且监听全部网卡（README §2.1）。
# 只提醒，不改绑定语义。
# 判据要说准：服务端开放模式判据是「未配有效 AUTH_TOKEN」且「库里没有账号设过密码」，
# 脚本读不到库状态，故只按 .env 判并照此措辞。ADMIN_PASSWORD 与 AUTH_TOKEN 任意一项
# 有效即关闭开放模式，故这里是「或」。
admin_pwd=$(sed -n 's/^[[:space:]]*ADMIN_PASSWORD[[:space:]]*=[[:space:]]*//p' .env 2>/dev/null | tail -n 1 | tr -d '\r"')
auth_tok=$(sed -n 's/^[[:space:]]*AUTH_TOKEN[[:space:]]*=[[:space:]]*//p' .env 2>/dev/null | tail -n 1 | tr -d '\r"')
if { [ -z "$admin_pwd" ] || [ "$admin_pwd" = "change-me-please" ]; } && \
   { [ -z "$auth_tok" ] || [ "$auth_tok" = "change-me-please" ]; }; then
  case "$HOST_BIND" in
    127.0.0.1|localhost) : ;;
    *) echo "警告：未配置真实 ADMIN_PASSWORD / AUTH_TOKEN —— 若库里也没有账号设过密码，服务将以免登录「开放模式」运行；当前绑定 $HOST_BIND（局域网内可访问）。设置访问密码可关闭开放模式（见 README §2.1）。" >&2 ;;
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
