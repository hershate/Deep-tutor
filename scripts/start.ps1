# Deep-tutor 本地直起（免 Docker，Windows PowerShell）：
#   venv -> 依赖 -> .env -> 前端构建 -> 数据库迁移 -> 单进程启动（后端托管 SPA + /api，:8000）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#       端口覆盖：$env:PORT=9000; scripts\start.ps1
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

# 2. .env（iter 89 零配置开箱即用：缺失不再退出——后端以「开放本机模式」启动，
#    首次打开浏览器进入设置向导，可在界面内完成密码/API Key 配置；不代为生成 .env，
#    避免模板弱 token change-me-please 反成认证配置）
if (-not (Test-Path ".env")) {
    Write-Host "==> 未找到 .env：以零配置模式启动（首次打开浏览器将引导完成初始设置）"
}

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
$port = if ($env:PORT) { $env:PORT } else { "8000" }
Write-Host "==> 启动 Deep-tutor：http://localhost:$port（局域网 http://<本机IP>:$port）"
# 自动打开浏览器（NO_BROWSER=1 关闭）：延迟 3s 等服务就绪
if ($env:NO_BROWSER -ne "1") {
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList "http://localhost:$port" | Out-Null
}
& $venvPy -m uvicorn app.main:app --host 0.0.0.0 --port $port --app-dir backend
