# Deep-tutor 本地直起（免 Docker，Windows PowerShell）：
#   venv -> 依赖 -> .env -> 前端构建 -> 数据库迁移 -> 单进程启动（后端托管 SPA + /api，:41173）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#       端口覆盖：scripts\start.ps1 -Port 9000（或 $env:PORT=9000；参数优先于环境变量）
param([int]$Port = 0)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = $null
foreach ($cand in @("python", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) { throw "未找到 Python 3.11+，请先安装（https://www.python.org/downloads/）" }
# err.md E2：前置版本校验（低版本建 venv 后到运行期才报错，排障困难）
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) { throw "需要 Python 3.11+（当前：$(& $py -V 2>&1)）" }

# 1. 虚拟环境 + 依赖（幂等：已安装则 pip 秒过）
$venvPy = "backend\.venv\Scripts\python.exe"
# err.md E1：检测 venv python 可执行而非目录存在——venv 创建被中断时目录在但
# 无 python，此前后续运行跳过重建直接 pip 失败；残留半成品先清再建
if (-not (Test-Path $venvPy)) {
    if (Test-Path "backend\.venv") { Remove-Item -Recurse -Force "backend\.venv" }
    Write-Host "==> 创建虚拟环境 backend\.venv"
    & $py -m venv backend\.venv
}
Write-Host "==> 安装/校验后端依赖"
& $venvPy -m pip install -q -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install 失败（$LASTEXITCODE）" }
# OCR/PDF 深度解析栈（marker）：单独一个 requirements 文件——它与 Pillow 下限互斥，
# 混装在一个文件里 pip 判为无解（见 backend\requirements-ocr.txt 的说明）。
# 只在尚未安装时装一次：每次启动都重装它，会把 Pillow 一路拽回 <11（实测 pip 会为
# 已装包的旧约束降级 Pillow）。装不上不阻断启动——缺 marker 时 PDF 走 pypdf 快速档，
# full 档按 R9 报 dependency_missing 并提示安装。
$markerBin = "backend\.venv\Scripts\marker_single.exe"
if (-not (Test-Path $markerBin)) {
    Write-Host "==> 安装 OCR/PDF 深度解析栈（marker；体积较大，仅首次）"
    & $venvPy -m pip install -q -r backend\requirements-ocr.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "OCR 栈安装失败（$LASTEXITCODE）——PDF 深度解析不可用，其余功能不受影响"
    } else {
        # OCR 栈把 Pillow 拽回了 <11；服务进程必须留在安全版本（直接要求优先，pip 只警告不中止）
        & $venvPy -m pip install -q -r backend\requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "pip install 失败（$LASTEXITCODE）" }
    }
}

# 2. .env（iter 89 零配置开箱即用：缺失不再退出——后端以「开放本机模式」启动，
#    首次打开浏览器进入设置向导，可在界面内完成密码/API Key 配置；不代为生成 .env，
#    避免模板弱 token change-me-please 反成认证配置）
# iter 370 B18：零配置引导态默认只绑 127.0.0.1——开放模式在首个用户设密码前完全免认证，
# 绑 0.0.0.0 会把「任何人可认领」的服务暴露给局域网。有 .env（用户已显式配置）仍默认
# 0.0.0.0 供局域网访问；两种情况均可用 HOST 环境变量覆盖（如 $env:HOST="0.0.0.0"）。
# iter 394：即便绑 0.0.0.0，「首次设置向导」的两个写端点（/api/setup、/api/setup/dismiss）
# 在开放模式下也仅接受本机回环请求（它们是唯一的「认领管理员」入口）——局域网用户须在
# .env 配好 ADMIN_PASSWORD / AUTH_TOKEN 后再用向导。
if (-not (Test-Path ".env")) {
    Write-Host "==> 未找到 .env：以零配置模式启动（首次打开浏览器将引导完成初始设置）"
    $hostDefault = "127.0.0.1"
} else {
    $hostDefault = "0.0.0.0"
}
$hostBind = if ($env:HOST) { $env:HOST } else { $hostDefault }

# 3. 前端构建产物（无 dist 且有 npm 则自动构建；两者皆无则报错）
if (-not (Test-Path "frontend\dist\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "==> 构建前端（frontend\dist）"
        Push-Location frontend
        try {
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install 失败（$LASTEXITCODE）" }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build 失败（$LASTEXITCODE）" }
        } finally { Pop-Location }
    } else {
        throw "frontend\dist 不存在且未找到 npm，无法构建前端。请安装 Node.js 20+ 后重跑。"
    }
}
$env:FRONTEND_DIST = "frontend/dist"
$env:PYTHONPATH = "backend"

# 4. 数据库迁移（新库建表 / 旧库增量 / 无版本库自动接续）
Write-Host "==> 数据库迁移"
& $venvPy -m app.db.migrate
if ($LASTEXITCODE -ne 0) { throw "数据库迁移失败（$LASTEXITCODE）" }

# 5. 启动（后端同端口托管 SPA 与 /api；0.0.0.0 供局域网访问）
# 端口优先级：-Port 参数 > PORT 环境变量 > 默认 41173
# （默认 41173：8000 常落入 Windows Hyper-V/winNAT 排除端口段，bind 报 WinError 10013）
# 注意：PowerShell 变量名不区分大小写——局部变量绝不可命名 $port（与参数 $Port
# 是同一变量，默认值赋值会把传入参数覆盖掉，实机冒烟实证）
$listenPort = 41173
if ($Port -gt 0) {
    $listenPort = $Port
} elseif ($env:PORT) {
    $parsed = 0
    if (-not [int]::TryParse($env:PORT, [ref]$parsed)) { throw "PORT 环境变量须为 1-65535 的整数（当前：$env:PORT）" }
    $listenPort = $parsed
}
if ($listenPort -lt 1 -or $listenPort -gt 65535) { throw "端口须在 1-65535 之间（当前：$listenPort）" }
Write-Host "==> 启动 Deep-tutor：http://localhost:$listenPort（绑定 $hostBind；局域网访问需绑 0.0.0.0）"
# 自动打开浏览器（NO_BROWSER=1 关闭）：iter 370 B18 改为轮询健康检查通过后再开——
# 固定 3s 在冷启动（迁移/插件装载慢）时打开的是尚未就绪的页面，用户看到连接失败
if ($env:NO_BROWSER -ne "1") {
    Start-Job -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 1
            try {
                $r = Invoke-WebRequest -Uri "$u/api/health" -UseBasicParsing -TimeoutSec 2
                if ($r.StatusCode -eq 200) { break }
            } catch { }
        }
        Start-Process $u
    } -ArgumentList "http://localhost:$listenPort" | Out-Null
}
# 单进程运行（不加 --workers）：SSE 流 / 限流 / 并发配额 / 插件事件总线 / 后台任务 / LLM 信号量
# 均为进程内状态——多 worker 下各进程独立（SSE 续接/取消、配额计数、事件推送会失准）。
# 多进程部署需 sticky session + Redis 共享这些状态（当前未实现，按单进程设计）。
& $venvPy -m uvicorn app.main:app --host $hostBind --port $listenPort --app-dir backend --timeout-graceful-shutdown 20
