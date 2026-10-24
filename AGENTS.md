# AGENTS.md — Deep-tutor 仓库工作指引

> 给在本仓库工作的 AI agent（与人类协作者）。改造期（Reasonix 架构）生效。

## 必读顺序（新会话开工前）

1. [`stage.md`](./stage.md) — 进度跟踪：先读该文件确定"现在做到哪、下一步做什么"。
2. [`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md) — **改造宪法**：61 项用户逐条确认的决策。任何实现与该决策冲突 → 停下问用户，不得自行变更；答后把修订记回 note/12。
3. [`plan/reasonix/`](./plan/README.md) — 完整改造方案（六份），实施细节以该方案为准。
4. 领域语义基准：[`note/00-INDEX.md`](./note/00-INDEX.md)（01–11 分析笔记）；v1 API 契约：[`API.md`](./API.md)（= shim 验收基准）。

## 工作方式（用户常设要求）

- **进度**：每完成一个步骤、每次 git commit、每次关键决策或阻塞，立即更新 `stage.md`。
- **分支与提交**：改造工作在 `refactor/reasonix` 分支；细粒度 commit 由 agent 自主提交（**不 push**，由用户合并 main）。
- **提问**：出现任何产品级/方向级未明确内容，及时问用户（AskUserQuestion），不要猜。
- **节奏**：全自动推进，但任务拆分尽可能细、每一步谨慎（小步可验证增量）。
- **内核引入后**（M1 起）：工程规约见根 [`DEEPTUTOR.md`](./DEEPTUTOR.md)（M1.4 已建档，2026-10-21），**Go 侧改动以 `DEEPTUTOR.md` 为准**；门禁命令见 `Makefile`。
  - 该文件由上游 `docs/kernel/upstream/REASONIX.md` 转换而来：保留缓存稳定、上下文投影、信任域、沙箱维度、错误身份、注释规范等工程章节，删除上游协作流程章节（PR hygiene），新增本仓规约（决策引用 note/12、stage.md 进度要求、v1 契约冻结、分层铁律、落盘位置约定）。

## 仓库状态速览（2026-10-24 更新）

- **本机已换机（2026-10-24，iter 423）**：项目迁到新电脑（Windows 11，用户 Z，仓库仍在 `F:\Github\repo\Deep-tutor`）。**旧机的事实不再成立**，本节已按新机实测改写；换机前后的差异与修复见 [`stage.md`](./stage.md) 的 iter 423 条目。
  - **工具链（新机实测，全部可用）**：
    - Go **1.26.10**（`F:\go`）——`go.mod` 钉 `go 1.26.0` / `toolchain go1.26.9`，装 1.26.10 即免去工具链下载
    - Node **24.22.1**（**`F:\Program Files\nodejs`**）——**该目录对本用户不可写** → npm 全局前缀因此用 `%APPDATA%\npm`，`pnpm` **10.34.8** 装在此处；`corepack enable` 在 Node 目录不可写时用不了
    - GNU make **4.4.1**（winget 的 `ezwinports.make`，逐用户安装）——**`$(shell ...)` 恒走 cmd.exe** 的老坑依旧
    - Python **3.14.9**（`%LOCALAPPDATA%\Python\pythoncore-3.14-64`，`py -3.14`）与 **3.15.1**（`C:\Python315`；`python` 命中 3.14）
    - Git **2.56.0**（**`F:\Program Files\Git`**，不是 C 盘）
  - **旧机的缓存还在 F 盘上（`F:\DevCache\*`，本轮直接复用，没重下）**：
    - `go`（= 旧 `GOPATH`，1.26 GB，含 `bin/golangci-lint.exe` **2.12.2**——正是 `.golangci-version` 钉的版本）
    - `go-build`（= 旧 `GOCACHE`，**17.3 GB** 热的构建缓存）、`pnpm-store`（v10+v11）、`npm-cache`（2.1 GB）、`pip-cache`
    - 用户级变量照此指回：`GOPATH=F:\DevCache\go`、`GOMODCACHE=F:\DevCache\go\pkg\mod`、`GOCACHE=F:\DevCache\go-build`、`GOBIN=F:\DevCache\go\bin`
    - `npm_config_cache=F:\DevCache\npm-cache`、`PIP_CACHE_DIR=F:\DevCache\pip-cache`；pnpm 的 store 写进它自己的 rc（`%LOCALAPPDATA%\pnpm\config\rc` → `store-dir=F:\DevCache\pnpm-store`）
  - **开工前先扫 F 盘**：这台机器的「环境」不在 C 盘而在 F 盘（`F:\Program Files\*`、`F:\DevCache\*`、`F:\soft`、`F:\dev`…）。**F 盘上没有 Go SDK、也没有 GNU make**（`F:\soft\BuildTools` 里只有 MSVC 的 `nmake.exe`），所以这两样确实要装；但**缓存、Node、Git 都已经存在**——先扫再动手，别重复下载或重复安装。
  - **git 所有权（换机后必做）**：仓库随旧机目录一起复制过来，owner 与当前用户不符 → 须 `git config --system --add safe.directory F:/Github/repo/Deep-tutor`，否则所有 git 命令报 `dubious ownership`（**用系统级而非用户级**——金丝雀隔离 HOME 后用户级配置读不到，见「本机四处环境项」）
  - **Go 模块代理**：`proxy.golang.org` 与 `sum.golang.org` 在本机均超时 → 用户级 `GOPROXY=https://goproxy.cn,direct`、`GOSUMDB=sum.golang.google.cn`（两者在旧机上也曾可用；新机网络走国内出口）
  - **Go 缓存与 pnpm store 落 F 盘**：见上条「旧机的缓存还在 F 盘上」——用户级 `GOPATH`/`GOMODCACHE`/`GOCACHE`/`GOBIN` 已指回 `F:\DevCache\*`（Go 默认值在 C 盘，与「可丢弃产物落 F 盘」相悖）
  - **内核 home**：`DEEPTUTOR_HOME` 与 `REASONIX_HOME` 双拼写都恢复为 `F:\Deeptutor\reasonix`（用户级）。该目录**随迁时没带过来**（新机上是空目录，由内核首次运行自建 `config.toml`）
  - **旧机遗留物**：`backend/.venv` 随迁但**基础解释器已随旧机消失**（旧机是 `F:\Python-Launcher\python.exe`）——venv 文件在、解释器跑不起来，pip 报 `No Python at ...`，服务永远起不来；启动脚本现在**探测解释器是否真的能跑**，跑不起来就重建（改动与实证见 [`stage.md`](./stage.md) 的 iter 423 条目）
  - **本机四处「环境项」**（不是代码回归，但会让门禁看起来像坏了）：
    - **git 所有权要走系统级**：`git config --system --add safe.directory F:/Github/repo/Deep-tutor`——**用户级不够**：金丝雀（`tools/canary`）把 HOME 隔离到一次性目录后 git 读不到用户级配置 → `git status` 报 dubious ownership → Go 的 VCS stamping 失败（`error obtaining VCS status: exit status 128`）→ 一批 effect 用例红
    - **符号链接需要开发者模式**：本机**没开**，`os.Symlink` 报「A required privilege is not held by the client」→ 12 个 symlink 用例红（`internal/platform/repair`、`internal/runtime/receipt`、`internal/platform/packagegrant`）；非管理员改不了（HKLM 写被拒），需「设置 → 系统 → 开发者选项 → 开发人员模式」或管理员
    - **Kaspersky 实时防护**拦下**新写出**的测试二进制（`Access is denied`）——与改动无关，回退到 HEAD 同样被拦，逐包复跑一般即过
    - **Go 测试的 `TMPDIR`/`TEMP`/`TMP` 要指到 `F:\TEMP\deeptutor`**（本仓规约：可丢弃产物落项目所在卷）——否则测试临时目录在 C:、scratch 在 F: → 跨卷 `rename` 报「不能把文件移到不同的磁盘驱动器」
  - **行尾漂移**：随迁的工作树有 201 个文件带 CRLF（`.py`/`.md`/`.json` 等），但**没有 `.go` 与 `.sh`** → Go 与 shell 门禁不受影响；`.gitattributes` 的 `text=auto` 在提交时归一，无需批量改写
- **当前可用实现**：Python v0.1.x（`backend/` + `frontend/`，端口 41173）——保留至改造阶段① M5 验收后停用，阶段④删除。实测规模：后端 28 个 `api/` 文件（24 个路由模块）、OpenAPI 146 operations / 120 paths、29 张 ORM 表、26 个 alembic 迁移、**188 个 `test_*.py` 测试文件（pytest 收集 1736 用例，iter 398 实测 1746 passed / 2 skipped）**；前端 19 页 SPA。
- **进行中**：Reasonix 内核改造。**阶段① M1.1 吸收已完成（2026-10-17）**：R-Tutor studio 快照 8289 文件全量入库（内核 `internal/` `cmd/` `tools/` `sdk/`、桌面端 `desktop/`、上游文档 `docs/kernel/` 等），构建绿。**M1.2 模块改名已完成（2026-10-21，commit `fd05bf6`）**：根模块 `module deep-tutor`、`desktop` 子模块 `module deep-tutor/desktop`（`replace deep-tutor => ../`）、`cmd/deep-tutor`。**M1.4 品牌化与 `DEEPTUTOR.md` 建档已完成（2026-10-22，commits `40e4209`~`4c22dbe`）**：用户可见的展示名改为 Deep-tutor / Deep-tutor Studio，**机器认的名字（发布资产名、安装布局名、协议 ID、哈希域分隔符、Electron profile 名）一律不改或两种拼写都认**——判据见 `DEEPTUTOR.md` 的「Where the name is a contract, not a label」。**M1.4f 环境变量改名已完成（2026-10-22）**、**M1.4g 品牌化半改收口已完成（`85979f4`/`b46aaed`，含 robots.txt 的 UA 漏匹配真缺陷）**、**M1.5 门禁基线重建与 CI 重建已完成（`3a9c267`/`c60b800`）**、**M1.6 Makefile 修复与 `bin/deep-tutor.exe` 冒烟已完成（`ddb4070`）**、**M1.3 多实例 spike 已完成（2026-10-22，矩阵/全局态/深水点回填 `plan/reasonix/04` §四）**。**M1.1–M1.6 全部完成**，见 `plan/reasonix/05` 与 `stage.md`；**下一步进 M2（数据层与认证）**，其第 0 步是 04 §四 的 **K 类内核改动**（见下条）。
- **多用户分流的阻断点（2026-10-22 更新，M1.3 spike 结论 + iter 404/405 已落地的部分）**：
  - `Options.Home` **无任何生产装配点设置**（→ 装配层 `internal/web` 落地时填上）
  - `control.Controller` 的 **roots 字段已加**（`controllerDeps.roots` + `control.Options.Roots` + `Controller.Roots()`，`Rebuild` 让替换件继承 stated home）
  - **control 包生产代码的进程级路径调用点已归零**（原 42 处），**余 182 处**在 serve(67)/cli(45)/state(18)/platform(19)/ext(14)/cmd(9)/assembly(2)/runtime(1)
  - **config 包自身的读路径也已按绑定**（声明来源标注、TOML 读取、凭据加载、恢复快照、provider_access 归属，iter 405）
  - `secrets` 的 4 个包级开关每次 build 被覆盖（其「`[secrets]` 不按项目覆盖所以包级安全」的前提在 per-user 后失效）
  - `healthyConfig` 每进程只记一次、多用户下只有第一个用户被记录
  - **内核已有的缝够用**（`ProviderResolver`/`SharedHost`/`BalanceStore`/`TaskStore`/`Stderr`/`ApprovalTimeout`/`Home`/`SetTutorSpaceResolver`/`config.Roots`），要改的是**让调用点走 build 的 Roots**，而不是重排内核结构
  - **深水点 #21 已落地（iter 409）**：`ApprovalTimeout` 的零值是永久等待，而 `serve`/`web` 的决策经网络到达——浏览器一关答案就没了，会话永久占住一个 Controller。serve 包导出 `serve.ApprovalTimeout = 10min`（超时=拒绝，fail-closed），hub 开的面、Server 重建的面、CLI 的 serve 装配三条路都带上；**终端前端仍是零值**
  - **深水点 #32 已落地（iter 410）**：运行期配置的项目层原先取进程 cwd → serve 会话开在 B 工作区却读服务启动目录的 `reasonix.toml`。改取会话自己的工作区根（与装配层读同一个文件），且改为只读——查询路径不改写用户配置
  - **激活存储（技能/MCP 启用态）与 MCP 声明刻意保持全局**（作用域只有 global/project，无 user 档；§三 的「扩展资产全局一套」+ note/12 决策 #55「管理员装、全员可用」），控制器的 MCP 安装/删除走**进程绑定**、会话设置走**会话绑定**，两者不可混用；其「用户能否为自己单独关掉某项」是待用户定夺的产品级问题
  - **新缺口（深水点 #33，W 类）**：per-user 会话读的是该用户 home，全局 MCP 清单在全局资产库里 → 装配层须把全局安装态**只读投影**进每个用户，否则管理员装的 MCP 服务器 per-user 看不到
- **环境变量（两种拼写都认，2026-10-22）**：产品被改名后，它读的环境变量也换了名——`DEEPTUTOR_HOME` 是当前的，`REASONIX_HOME` 是改名前的。**新名优先、旧名兜底**：当前名设了就以它为准，没设才回落到旧名。解析统一走 `internal/base/envvar`（`Get` / `Lookup` / `Name`），新增变量不要直接 `os.Getenv`。**注入**给插件/钩子/边车的变量两种拼写都注入（`REASONIX_PLUGIN_ROOT` 与 `DEEPTUTOR_PLUGIN_ROOT` 等），因为另一端是已经写好的程序。本机用户级已设 `DEEPTUTOR_HOME=F:\Deeptutor\reasonix`（`REASONIX_HOME` 同时保留，旧构建仍能找到 F 盘 home）。
- **测试隔离守卫（2026-10-22，iter 408）**：「新名优先」有代价——机器级设了 `DEEPTUTOR_HOME`，只设旧拼写 `REASONIX_HOME` 的测试**就失去隔离作用**，产品读到的仍是机器的真实 home。
  - 实测后果：`internal/state/storage` 的测试把 `[storage] state` 指针写进了真实用户配置
  - **规矩**：测试要碰 home/state/cache 根的包，必须在 `TestMain` 里装 `testenv.RunWithIsolatedUserState(m)`
  - 守卫清空两种拼写的 home 变量，并把 `HOME`/`USERPROFILE`/`AppData`/`LocalAppData` 指向 F 盘 scratch 下的一次性目录
  - **形状门禁**：`internal/base/testenv/isolation_gate_test.go` 的 `TestPackagesNamingAGuardedVariableInstallTheGuard` 检查「提到 home 根变量的包是否装了守卫」，漏装即红
  - **子进程不能靠继承环境变量拿位置**（子进程自己的守卫会清空它）——位置走测试专有变量（`REASONIX_PERMISSION_HOME`、`REASONIX_TEST_FATAL_CHILD_HOME`），子进程里再声明一次
- **serve 层长跑修复（2026-10-22，iter 413/414）**：一轮针对 `internal/frontend/serve` 的长跑资源审计
  - **三处泄漏**：帧镜像与 `unsubscribe` 循环依赖、限流器无停止通道的清理协程、已结清问题无上限（且留着用户输入的原文）
  - **两处状态/实现不一致**：分享监听器死后仍报「已开」并发出指向死地址的配对码、市场客户端每请求新建且无超时
  - `/trajectory` 读整份日志不再持写者独占锁
- **落盘位置（本项目自己的产物，2026-10-21 起）**：可丢弃产物统一落在 **F 盘**。Go 侧 `internal/base/scratchroot` 与 Python 侧 `backend/app/core/scratchroot.py` 同规则：调用方重定向过的临时目录（`TMPDIR`/`TEMP`/`TMP` 与系统默认不同）→ `$DEEPTUTOR_SCRATCH_DIR` → 项目所在卷的 `TEMP` 目录（本机 `F:\TEMP\deeptutor`）→ 系统临时目录。内核 home/state/cache 由用户级 `DEEPTUTOR_HOME=F:\Deeptutor\reasonix` 定位（改名前的 `REASONIX_HOME` 同样有效，见上条）。**有意保留在 C 盘的锁根**（`%LOCALAPPDATA%\reasonix`、`~/.reasonix/locks`）：必须让同一 OS 用户的各实例收敛到同一处，改动会让互斥失效。
  - **iter 423 追加（本机的其余产物根）**：
    - Go 的模块与构建缓存**沿用旧机的 `F:\DevCache\*`**（见上条「旧机的缓存还在 F 盘上」——默认值 `%LOCALAPPDATA%\go-build`、`%USERPROFILE%\go` 在 C 盘，与「可丢弃产物落 F 盘」相悖）
    - `golangci-lint` 复用旧机那份 `F:\DevCache\go\bin\golangci-lint.exe`（2.12.2，与 `.golangci-version` 一致，`make lint-install` 可重装）；pnpm store 在 `F:\DevCache\pnpm-store`
    - Python 虚拟环境在仓库内 `backend\.venv`（随仓库走，本身就在 F 盘）
    - **npm 全局包是例外**：`%APPDATA%\npm` 在 C 盘且**必须留在那里**——`F:\Program Files\nodejs` 对本用户不可写，全局前缀改到用户目录才装得上 `pnpm`
  - **换机留下的两处死物**：`F:\Deeptutor\reasonix`（内核 home）**没随迁**——新机上由内核首次运行自建（`config.toml` 里有 seed 出来的 deepseek 供应商档案，密钥未配）；`F:\Deeptutor\.venv` 只剩一个**指向 Linux 路径的死符号链接 `lib64`**（旧机的 venv 残骸，Windows 删不掉也不影响任何东西）。
- **门禁现状（2026-10-22 更新）**：`.github/workflows/ci.yml` 已接上 **Go 内核门禁与桌面端前端门禁**（M1.5b，commit `c60b800`）——保留 Python v1 四步门禁（ruff / pytest 覆盖率≥80% / OpenAPI 契约 / 前端 build），新增 `go-lint`（gofmt / go vet / golangci-lint v2.12.2 / repolint / desktop 子模块）、`go-test`（ubuntu+windows 矩阵，三个模块各测各的：根模块 / `desktop` / `sdk/go`，后两个是嵌套 module）、`desktop-frontend`（pnpm build / test / **census** / census:verify + Electron `node --test`）。Go 门禁命令仍在 `Makefile`（`make lint` / `make check` / `make test` / `make coverage-gate` / `make pricecheck`）。
  - **Windows 上跑的根模块 `go test` 必须带 `-count=1`**：开着结果缓存时 go test 会在运行前后各做一次 `filepath.EvalSymlinks`，`internal/assembly/boot` 记了约 190 万条路径，在 Windows runner 上光这一步就要几分钟且不打印任何东西。`internal/base/testenv` 的 `TestWindowsCILegsBypassTheTestResultCache` 守的就是这一条形状。
  - **Go 测试必须分批 + 超时上限**：单包 `-timeout 30m`（`internal/assembly/boot` 本机 975–1340 秒，超过 Go 默认 600 秒上限，一次性 `go test ./...` 必然 timeout panic）、`-p 4`（满并发时一批与负载/时序敏感的用例会假失败，逐包串行复跑全绿）。
  - **金丝雀门禁（iter 412）**：根模块的 `go test` 走 `go run ./tools/canary run -- ./...`——套件被指向一次性 home，跑完检查那个 home 还空不空。写进用户 home 是静默的，静态形状门禁抓不到（写文件的包不提任何 home 变量，它只是调了解析 home 的路径函数）；`make test` 的根模块步骤同走此路
  - `tools/repolint` 基线**已按本仓树重建**（M1.5a，commit `3a9c267`）：2173 findings / 524 files，复核 clean。其扫描域是 **git 可见的树**（tracked + untracked-not-ignored），故本机的 `REF/` 副本不再让基线漂移。
  - `gofmt` 全绿（iter 399 修掉 M1.2 改名遗留的 import 排序问题：全仓 415 个文件 → 0，`commit fa49eb6`）；`golangci-lint run ./...`（根模块与 `desktop` 子模块）**0 issues**（iter 401）。
  - **iter 423 复核：gofmt 曾有一处真红**——`internal/platform/update/release_unit_test.go`（fa49eb62 提交的复合字面量缩进是 2 空格，Go 1.19+ 的 gofmt 要 3 空格）→ CI 的 `gofmt -l internal cmd tools sdk desktop` 门禁会红。
    - 已 `gofmt -w` 修好，**新机实测 `gofmt -l` 0 文件**。**教训**：gofmt 的判定随 Go 版本走，改 Go 版本后要重跑这一条。
  - **iter 423 新机实测（全绿）**：`make build`（两个二进制，exit 0）· `gofmt -l` 0 · `go vet ./...` exit 0 · `make lint-go`（golangci-lint v2.12.2）**0 issues** · `ruff check app tests` **All checks passed**。
    - `frontend` 的 `npm run build` exit 0 · `desktop/frontend-next` 的 `pnpm build` / `pnpm census`（exit 0，Seal E held）/ `pnpm census:verify` 全绿。
  - **前端门禁**：`desktop/frontend-next` 的 `pnpm test`（**549 文件 / 4683 用例全绿**）、`pnpm census`（**exit 0**；Seal E「渲染出的动作 id 必须都在 `src/actions.ts` 登记」此前是 OPEN）、`pnpm census:verify`、`pnpm build` 全绿。**上游 CI 从不跑 frontend-next 的 vitest**，这批红灯是随快照入库的上游既有债（iter 401 已修 10 处并定性）；本仓 CI 里的这一步是新增义务。
  - `go test ./...` 在本机固定失败的用例如下，均为环境或工作树依赖，**非代码回归**（2026-10-22 iter 401 更新）：

| 包 / 用例 | 失败条件 |
|---|---|
| `internal/ext/skill/builtincontent` | 此前记为「AV 占用 go-build 临时 exe」——实为**两件事**：品牌化漏改让 `TestLoadReasonixGuide` 真失败（iter 411 已修），AV 占用是另一回事（构建到 F 盘即正常） |
| `internal/runtime/schedrun` `TestKillingTheRunEndsItsWholeProcessTree` | Windows 子进程树取锁时序 |
| `internal/tools/builtin` `TestPipeStatusProbePreservesExitStatusAndReports` | Windows 管道退出码探针 |
| `internal/runtime/agent` `TestContractOwesTheCheckTheTaskBeganUnder` | `TempDir RemoveAll cleanup` 目录非空——**负载/时序敏感**（iter 409 实测：并发重活时 1/9 次失败，单独复跑与改前/改后各 4/4 全绿） |

**已于 iter 401 消除的三项**：`internal/safety/sandbox` `TestIsWindowsWSLBash`（改为临时夹具，不再依赖本机是否装 WSL）、`internal/base/testenv` `TestWindowsCILegsBypassTheTestResultCache`（CI 已含 Windows 根模块 `go test` 且带 `-count=1`）、`internal/runtime/agent` `TestChildConstructionForksStayEnumerated`（改按 git 可见的树枚举 `.go`）。

**AV 占用 `go-build` 临时 exe 的绕法**（2026-10-22 实测）：本机实时扫描会拦下**新写出**的测试二进制，报 `open …\go-build…\xxx.test.exe: Access is denied`——**与改动内容无关**（回退到 HEAD 同样会被拦，改动后构建到别处则正常）。绕法：`go test -c -o F:\TEMP\deeptutor\tb\<name>.test.exe ./<pkg>/` 构建到 F 盘，再 `cd` 到包目录直接运行该二进制（测试要用相对路径读仓库内的文件，必须在包目录下执行）。

**Makefile 在 Windows 上（本机事实，iter 402 实测 / iter 423 换机后重测）**：本机的 make 是 GNU make 4.4.1（换机前是 scoop 的 mingw-winlibs 版，现为 winget 的 `ezwinports.make`，都是 GNU make 的 mingw 变体）——**`$(shell ...)` 恒走 cmd.exe，与 makefile 里的 `SHELL` 无关**。

此前 `$(wildcard C:/Program Files/Git/bin/sh.exe)` 因路径含空格恒空 → `SHELL` 落到不存在的 `/bin/sh` → 取值全部走 Windows 的 `date`/`sed`/`cat` → `BUILD_TIME_UTC` 变成带空格的英文句子 → `-X` 的值被拆开 → `make build` **链接失败**（链接器只打印用法）。

已修：shell 探测改用 glob（8.3 短名优先）、取值改成 cmd 与 POSIX 都认的形状（`$(patsubst)`/`$(if)`/`$(file <)`）、时间戳显式点名 POSIX shell 并回落 PowerShell。**新增任何 `$(shell ...)` 都要按「cmd.exe 语义」写**；recipe 侧则是 Git Bash（makefile 的 SHELL 现已指向真实存在的 shell，`lint-go`/`lint-cross`/`cross`/`clean` 这些 POSIX recipe 在 Windows 上可用）。

**iter 423：Git Bash 探测重写（新机 Git 在 `F:\Program Files\Git`）**——旧写法只认 C 盘，且把带空格的结果喂给 `$(firstword ...)`：

- **8.3 短名在 F 盘不存在**（`dir /x F:\` 不给短名），所以 `F:/Progra~1/...` 这种兜底在新机上**无效**——「短名优先」的老思路在 F 盘上不成立。
- **`$(firstword ...)` 会把带空格的结果切开**：`$(wildcard F:/Program*/Git/bin/sh.exe)` 返回 `F:/Program Files/Git/bin/sh.exe`，经 `firstword` 后只剩 `F:/Program` → shell 拿到半条路径。**同一个坑还有 `$(foreach ...)`**：它的结果按空白切词。
- **`$(wildcard $(变量)/...)` 也不行**：变量展开后的空格会把 pattern 拆成两个 pattern（`$(wildcard)` 收到的是「拆分后」的值）。**只有「pattern 本身不含空格」或「空格被 `\` 转义」两种写法能命中**。
- **现写法**：候选 pattern 一律**不含空格**（`C:/Progra~1/…`、`F:/Progra~1/…`），最后一个候选**问 git 自己**（`git --exec-path` → 推出安装根 → 空格转义后再喂 `$(wildcard)`）。
  - 每个候选是**独立的一次 `$(wildcard)`**，不经 `foreach`/`firstword`；结果整体加引号给 `$(shell ...)`（`$(shell)` 的参数文本在展开**前**按空白切分，quotes 必须落在值里）。
- **新机实测**：`GITBASH = F:/Program Files/Git/bin/sh.exe`、`SHELL` 同值、`BUILD_TIME_UTC = 2026-10-24T12:03:07Z`（单词、UTC、ISO 形状）、`make build` 产出 `bin/deep-tutor.exe` 与 `bin/reasonix-plugin-example.exe`（exit 0）。
- **另有一条与 SHELL 无关的 Windows 行为**：recipe 里**不含 shell 元字符**的行**不经过 shell**（make 直接 CreateProcess），所以 `date -u +…` 这种行会因「找不到 date.exe」而失败——`$(shell ...)` 取值一律走 Git Bash，别写在 recipe 里。

另：`internal/assembly/boot` 单包本机耗时 **974.9 秒**（iter 398 实测 1340 秒），超过 Go 默认 600 秒超时上限，`go test ./...` 一次性执行必然报 timeout panic（`make test` 同）；该包单独执行（`-timeout 25m`）通过，`internal/frontend/serve` 单独执行亦通过。Go 侧门禁的实际执行方式（分批 + 超时上限）属 M1.5 待定项。iter 398 全量复跑另见一批**负载/时序敏感**的失败（`internal/base/proc` `internal/frontend/tui` `internal/platform/worktree` `internal/state/sessionstore` `internal/state/workspacelease` `internal/tools/builtin` 的 bash/超时用例、两个包的 `TempDir RemoveAll cleanup` 目录非空），单独复跑可通过，判定为环境项。

- **工作区外文件的删除**：只能用 `C:\Users\Z\.minimax\bin\mavis-trash.cmd -- <绝对路径>`（`rm --` 拒收工作区外目标；`Remove-Item` 与 `.NET Directory.Delete` 被硬策略拒绝）。目录内文件数过大（>5 万）时 `mavis-trash` 也会失败，改用一次性 Go 程序处理（非跟踪，放 `.git/dt_tools/`）。
- **引用合规**：上游 https://github.com/esengine/DeepSeek-Reasonix （MIT）。声明与修改披露见根 [`NOTICE`](./NOTICE)，许可全文 [`LICENSE-REASONIX`](./LICENSE-REASONIX)；修改上游代码须同步更新 NOTICE 修改披露。上游无关资产树（`release-notes/` `benchmarks/` `examples/` `workers/` `npm/` `output/`）已于 2026-10-20 移除，范围偏差登记于 note/12 修订记录。
- **参考副本**：`REF/`（gitignore，不入库）= 吸收用源快照（zip 为备份），仅供对照。**注意**：iter 401 起，两个曾扫工作树全集的门禁都改成看 **git 可见的树**（`tools/repolint` 的 `visibleFiles`、`internal/runtime/agent` 的 `TestChildConstructionForksStayEnumerated` 用 `git ls-files`），故本机的 `REF/` 副本不再影响门禁结果。
- **归档**：Python 时代计划与工作档案在 `docs/archive/`（git 历史完整）。

## Reasonix host checks

下列路径决定运行时允许执行的行为，路径下的改动需要 `review` + `security_review` 才能结束一轮。敏感性为显式声明，不由路径名推断。同一份清单同时是覆盖率门禁（`make coverage-gate`）的对象：每条路径被锁定在既有覆盖率下限，下调需 `make coverage-gate-update -allow-drop` 并说明理由。清单原文见根 [`DEEPTUTOR.md`](./DEEPTUTOR.md) 的「Deep-tutor host checks」一节（`tools/covergate` 从该文件读取）。

- sensitive: internal/safety/permission/**
- sensitive: internal/safety/sandbox/**
- sensitive: internal/safety/shellsafe/**
- sensitive: internal/base/shellparse/**
- sensitive: internal/session/control/approval.go
- sensitive: internal/session/control/approval_bridge.go
- sensitive: internal/session/control/approval_orchestration.go
- sensitive: internal/model/providerbroker/**
- sensitive: internal/ext/installsource/**
- sensitive: internal/ext/plugin/**
- sensitive: internal/ext/pluginpkg/**
- sensitive: internal/base/netclient/**
- sensitive: internal/safety/redirectguard/**
- sensitive: internal/safety/endpointclient/**
- sensitive: internal/safety/egress/**
- sensitive: internal/platform/browser/**
- sensitive: internal/platform/computer/**

`internal/web/auth/**`、`internal/web/session/**`、`internal/web/shim/**` 三条待阶段① M2/M3 建成 `internal/web` 服务层后追加（见 `plan/reasonix/01` §五）。
