# Deep-tutor 本地直起（免 Docker，Windows PowerShell）：
#   venv -> 依赖 -> .env -> 前端构建 -> 数据库迁移 -> 单进程启动（后端托管 SPA + /api，:41173）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#       端口覆盖：scripts\start.ps1 -Port 9000（或 $env:PORT=9000；参数优先于环境变量）
param([int]$Port = 0)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = $null
$pySeen = @()
# 换机后 PATH 上的 python 可能是「应用执行别名」（WindowsApps 里的占位入口）或版本过低的
# 解释器——Get-Command 找得到它，跑起来却不是能用的 Python。故逐个候选**实跑**一遍：
# 第一个既能执行、又满足 3.11+ 的胜出（err.md E2 的前置校验随之完成）。
#
# 探针必须**包在 try/catch 里**：解释器跑不起来的表现恰恰是「非零退出码 + stderr 输出」，
# 而 Windows PowerShell 5.1 会把原生命令的 stderr 变成错误记录——在
# $ErrorActionPreference=Stop 下那是**终止性错误**：探针自己先把脚本打断，走不到
# 「换一个候选 / 重建 venv」那一步（iter 423 实测：死 venv 场景直接抛异常退出）。
# 异常一律当作「这个候选不可用」。
foreach ($cand in @("python", "py", "python3")) {
    if (-not (Get-Command $cand -ErrorAction SilentlyContinue)) { continue }
    $ver = ""
    $ok = $false
    try {
        $ver = (& $cand -V 2>&1) -join " "
        & $cand -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        $ver = $_.Exception.Message
        $ok = $false
    }
    $pySeen += "$cand ($ver)"
    if ($ok) { $py = $cand; break }
}
if (-not $py) {
    $seenTxt = if ($pySeen.Count) { $pySeen -join "、" } else { "PATH 上没有 python / py" }
    throw "未找到可用的 Python 3.11+。已试：$seenTxt。请安装 Python 3.11+（https://www.python.org/downloads/）后重跑。"
}

# 1. 虚拟环境 + 依赖（幂等：已装好则秒过）
# iter 424：本项目的虚拟环境改由 **uv** 创建（落在项目内 backend\.venv），并加一段
# 「uv 环境检查」——uv 在不在、版本、venv 健不健康、venv 是不是 uv 建的。
$venvDir = "backend\.venv"
$venvPy = "$venvDir\Scripts\python.exe"
$venvCfg = "$venvDir\pyvenv.cfg"

# uv 的落盘位置：uv 默认把缓存放在 %LOCALAPPDATA%\uv\cache（C 盘）——与本仓
# 「可丢弃产物落项目所在卷」的规约相悖；且缓存与 venv 跨卷时硬链接失效，uv 会退化为
# 全量拷贝（实测警告：Failed to hardlink files; falling back to full copy）。
# 故默认指到**项目内**的 .uv-cache：跟着仓库走（与 backend\.venv 同卷，硬链接可用），
# 换机后不必重下。调用方已显式设置 UV_CACHE_DIR 则尊重之。
if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path (Get-Location) ".uv-cache"
}

# uv 检查：优先用**本机 F 盘**那份（uv 装在 F:\uv\bin），再退到 PATH 上的 uv。
# 顺序理由：PATH 上可能是别的机器留下的旧版或占位入口；F 盘那份是本机明确安装的。
# 候选顺序：显式指定 DEEPTUTOR_UV > F 盘本地 > PATH。每个候选都要**实跑**一次
# --version（找得到不等于跑得起来）。
$uvCmd = $null
$uvVer = ""
$uvSource = ""
if ($env:SKIP_UV -ne "1") {
    $uvCandidates = @()
    if ($env:DEEPTUTOR_UV) { $uvCandidates += $env:DEEPTUTOR_UV }
    $uvCandidates += @("F:\uv\bin\uv.exe", "F:\uv\uv.exe")
    foreach ($c in $uvCandidates) {
        if (-not (Test-Path $c)) { continue }
        try {
            $v = (& $c --version 2>&1) -join " "
            if ($LASTEXITCODE -eq 0) { $uvCmd = $c; $uvVer = $v; $uvSource = $c; break }
        } catch {
            $uvCmd = $null
        }
    }
    if (-not $uvCmd) {
        $uvFound = Get-Command uv -ErrorAction SilentlyContinue
        if ($uvFound) {
            try {
                $v = (& uv --version 2>&1) -join " "
                if ($LASTEXITCODE -eq 0) { $uvCmd = "uv"; $uvVer = $v; $uvSource = "PATH" }
            } catch {
                $uvCmd = $null
            }
        }
    }
}
if ($uvCmd) {
    Write-Host "==> uv：$uvVer（$uvSource；缓存 $env:UV_CACHE_DIR）"
} elseif ($env:SKIP_UV -eq "1") {
    Write-Host "==> 按 SKIP_UV=1 跳过 uv：虚拟环境用标准库 venv + pip"
} else {
    Write-Host "==> 未检测到可用的 uv：回退到标准库 venv + pip（装 uv 可快很多：https://docs.astral.sh/uv/）"
}
# 「可用的 venv」＝解释器文件在 **且** 真的跑得起来。
# 换机/重装 Python 后，venv 里的解释器仍指向旧机器的基础解释器（换机前本机是
# F:\Python-Launcher\python.exe，该路径已随旧机消失）——文件在，pip 却报
# "No Python at ..."，服务永远起不来；只看文件存在就会跳过重建，把坑留在第一步。
$venvUsable = $false
$venvMadeByUv = $false
if (Test-Path $venvPy) {
    try {
        & $venvPy -c "import sys" 2>$null
        $venvUsable = ($LASTEXITCODE -eq 0)
    } catch {
        # 死 venv 的 stderr（"No Python at ..."）在 Windows PowerShell 5.1 下是终止性错误
        # ——不接住的话，脚本会在这里中断，而不是去重建 venv。
        $venvUsable = $false
    }
    if ($venvUsable -and (Test-Path $venvCfg)) {
        try { $venvMadeByUv = ((Get-Content $venvCfg -Raw) -match "(?m)^uv\s*=") } catch { $venvMadeByUv = $false }
    }
}
if ($venvUsable -and $uvCmd -and -not $venvMadeByUv) {
    # 一次性迁移：项目内的 venv 一律由 uv 建（用户可用 SKIP_UV=1 退出本行为）
    Write-Host "==> 现有 backend\.venv 不是 uv 建的，重建（SKIP_UV=1 可保留它）"
    $venvUsable = $false
}
if (-not $venvUsable) {
    if (Test-Path $venvDir) {
        Write-Host "==> 现有 backend\.venv 不可用，重建"
        Remove-Item -Recurse -Force $venvDir
    }
    if ($uvCmd) {
        # 用已探测到的解释器（≥3.11）建 venv，落在项目内 backend\.venv。
        # --seed：把 pip 装进 venv——OCR 栈那段仍走 pip（见下），且没有 pip 的 venv
        # 跑 `python -m pip` 会直接报 No module named pip。
        $pyAbs = (& $py -c "import sys; print(sys.executable)" 2>$null) -join ""
        Write-Host "==> 用 uv 创建虚拟环境 backend\.venv（解释器 $pyAbs）"
        & $uvCmd venv --seed --python "$pyAbs" $venvDir
        if ($LASTEXITCODE -ne 0) { throw "uv venv 失败（$LASTEXITCODE）" }
    } else {
        Write-Host "==> 创建虚拟环境 backend\.venv"
        & $py -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败（$LASTEXITCODE）" }
    }
    # 建完立刻复检：venv 的解释器必须真能跑（uv 建的也要验，不假设）
    try {
        & $venvPy -c "import sys" 2>$null
        $venvUsable = ($LASTEXITCODE -eq 0)
    } catch {
        $venvUsable = $false
    }
    if (-not $venvUsable) { throw "新建的虚拟环境不可用（$venvPy 跑不起来）" }
}
Write-Host "==> 安装/校验后端依赖"
if ($uvCmd) {
    & $uvCmd pip install --python $venvPy -r backend\requirements.txt
    if ($LASTEXITCODE -ne 0) {
        # uv 失败（网络、索引、解析器差异都可能）不直接放弃——回退 pip 再试一次：
        # 服务栈装不上就永远起不来，两条路都断了才报错。
        Write-Warning "uv pip install 失败（$LASTEXITCODE），回退到 pip 重试"
        & $venvPy -m pip install -q -r backend\requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "pip install 失败（$LASTEXITCODE）" }
    }
} else {
    & $venvPy -m pip install -q -r backend\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install 失败（$LASTEXITCODE）" }
}
# OCR/PDF 深度解析栈（marker）：单独一个 requirements 文件——它与 Pillow 下限互斥，
# 混装在一个文件里 pip 判为无解（见 backend\requirements-ocr.txt 的说明）。
# 只在尚未安装时装一次：每次启动都重装它，会把 Pillow 一路拽回 <11（实测 pip 会为
# 已装包的旧约束降级 Pillow）。装不上不阻断启动——缺 marker 时 PDF 走 pypdf 快速档，
# full 档按 R9 报 dependency_missing 并提示安装。
$markerBin = "backend\.venv\Scripts\marker_single.exe"
# OCR/PDF 深度解析栈是可选的重型栈（marker + torch，约 2GB），装它会拖慢首次启动。
# 显式开关：SKIP_OCR_STACK=1 只装服务栈（PDF 走 pypdf 快速档，full 档按 R9 报
# dependency_missing）——网络慢或只想先跑起来时用。
if ($env:SKIP_OCR_STACK -eq "1") {
    Write-Host "==> 按 SKIP_OCR_STACK=1 跳过 OCR/PDF 深度解析栈"
} elseif (-not (Test-Path $markerBin)) {
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
# 开放模式提醒：.env 存在但没配真实凭证时，服务可能免登录且监听全部网卡——同一局域网内
# 任意设备可访问数据（README §2.1 已列为已知限制）。此处只提醒，不改绑定语义。
#
# 判据要说准：服务端的开放模式判据是 app.core.security.passwordless_open——
# 「未配有效 AUTH_TOKEN」**且**「库里没有任何账号设过密码」。本脚本读不到库状态，
# 所以只按 .env 判，并照此措辞：不把「可能开放」说成「一定开放」。
# 同理，ADMIN_PASSWORD 与 AUTH_TOKEN 任意一项有效即会关闭开放模式，故这里是「或」。
#
# 匹配用 [ \t]* 而不是 \s*：.NET 的 \s 含换行，多行模式下 \s* 会把「空值那一行」的
# 换行一起吞掉，于是把**下一行**当成了值（注释行也算）——本机的 .env 正是这样被误判成
# 「已配置密码」，提醒因此从不打印。
$envText = if (Test-Path ".env") { Get-Content ".env" -Raw -ErrorAction SilentlyContinue } else { "" }
$adminPwd = ""
if ($envText -match "(?m)^[ \t]*ADMIN_PASSWORD[ \t]*=[ \t]*(.*)$") { $adminPwd = $Matches[1].Trim().Trim('"').Trim("'") }
$authTok = ""
if ($envText -match "(?m)^[ \t]*AUTH_TOKEN[ \t]*=[ \t]*(.*)$") { $authTok = $Matches[1].Trim().Trim('"').Trim("'") }
$hasRealAdmin = ($adminPwd.Length -gt 0) -and ($adminPwd -ne "change-me-please")
$hasRealToken = ($authTok.Length -gt 0) -and ($authTok -ne "change-me-please")
if (-not ($hasRealAdmin -or $hasRealToken) -and $hostBind -ne "127.0.0.1") {
    Write-Warning "未配置真实 ADMIN_PASSWORD / AUTH_TOKEN —— 若库里也没有账号设过密码，服务将以免登录「开放模式」运行；当前绑定 $hostBind（局域网内可访问）。设置访问密码可关闭开放模式（见 README §2.1）。"
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
