# stage.md - Deep-tutor 实施进度跟踪

> **本文件用途（用户原始要求，须始终遵循）**：
> 分阶段执行计划，不需要在本次会话全部完成，但是注意，你的所有操作进度都应该写入目录下的 `stage.md` 以便于后期在其他会话继续工作，或者中断后随时继续（请把这一段要求也直接写入 stage.md）。
>
> 因此：**每完成一个步骤、每次 git commit、每次关键决策或阻塞，都必须更新本文件**。新会话或中断恢复时，先读本文件确定"下一步做什么"，再继续。

---

## 〇、如何续接（给未来会话的自己）

1. 读本文下方「Reasonix 改造日志」（最新在上）确定当前进度与下一步；当前下一步以最新 iter 条目的"下一步"行为准。
2. 读 [`note/12`](./note/12-Reasonix改造决策.md)（改造宪法，61 项决策 + 修订记录）与 [`plan/reasonix/`](./plan/README.md)（六份方案）确认实现细节；工作方式（分支/提交/提问/规约）见根 [`AGENTS.md`](./AGENTS.md)。
3. 检查 `git log --oneline` 与本文件记录的 commit hash 是否一致。
4. 遵守下方「长期执行约定」。
5. 继续执行下一个未完成步骤，完成后更新本文件并 commit。

## 长期执行约定（历次会话沉淀，长期有效）

- **git commit**：细粒度、每逻辑变更独立 commit；commit 信息中文；**严禁写 co-author / 协作者**（用户全局规范）。
- **不编造**：引用计划/技能/文档前先读原文；不臆测接口与字段。
- **问而不猜**：遇产品级/方向级歧义先问用户（AskUserQuestion），除非用户已明确表示自行决定。
- **文字模型**：不读取/不处理图片等多模态内容。
- **环境（Windows 11 + Git Bash）**：本机 `grep` 实为 ugrep，行尾过滤类管道（如 `'/$'`）会静默失效——清单比对/计数改用 `awk`；`/tmp` 在 Git Bash 与 Python 解析不同，跨工具传路径用相对或 Windows 绝对路径。

## 历史归档说明（2026-10-17 清理）

**Python 时代完整记录（2026-07-16 – 2026-07-27，v0.1.0.0 定稿）已从本文件清理，git 历史完整可溯**：总体路线图（P1–P5 全部完成）、执行约定与实现期决策、Phase 1 实施步骤、iter 1–365 全部进度日志——

```bash
git show <清理 commit>^:stage.md    # 清理前全量（1290 行）
git log --follow -- stage.md        # 本文件全部历史
```

Python 时代一手资料替代入口：项目分析笔记 [`note/00-INDEX.md`](./note/00-INDEX.md)（01–11）；设计文档归档 [`docs/archive/plan-v1/`](./docs/archive/plan-v1/)；2026-07-18 全项目审计清单 [`docs/archive/err.md`](./docs/archive/err.md)。

---

## Reasonix 改造日志（2026-10-15 起；分支 `refactor/reasonix`，最新在上）

**iter 398（2026-10-21，M1.2 模块改名 + 内核崩溃修复 + 落盘位置收敛到 F 盘 + 配置锁回收，✅ 完成）**：承接 iter 397 的「下一步」，并落实用户新增要求「**所有缓存/环境优先使用 F 盘已有的位置；此前若在 C 盘部署请全部清理或迁移到 F 盘对应已存在位置（只允许操作本项目自己部署的部分）**」。

- **本轮 4 个 commit**：

  | commit | 主题 | 关键内容 |
  |---|---|---|
  | `fd05bf6` | **M1.2 模块与 import 路径改名** | 根模块 `module reasonix` → `deep-tutor`；`desktop` 子模块 `module deep-tutor/desktop` + `replace deep-tutor => ../`；五类 import 前缀批量改写（`reasonix/internal|desktop|cmd|docs|tools/` 共 8445 处）；`cmd/reasonix` → `cmd/deep-tutor`；`reasonix.example.toml` → `deeptutor.example.toml`；`Makefile` 产物改 `bin/deep-tutor$(GOEXE)` 与 `dist/deep-tutor-<os>-<arch>`。**两个门禁工具必须同步改，否则静默空转**：`tools/repolint/layers.go` 的 `modulePrefix`（分层检查按 import 前缀匹配模块，不改则匹配不上、检查形同虚设）、`tools/covergate/main.go` 的 `TrimPrefix`（覆盖率门禁按同一前缀裁剪路径，不改则门禁失效）。**不改的形态**（属 M1.4 品牌化范围，机械改名会连带改协议标识与产品语义）：产品名 `Reasonix`、工作区目录 `.reasonix/`、ACP vendor method `_reasonix.io/`、git 分支 `reasonix/delivery-*`、HTTP User-Agent `Reasonix/`、上游仓库地址、运行时配置名 `reasonix/config.toml`；`sdk/go` 仍是独立 module |
  | `fd01574` | **修复 MCP transport 启动失败导致进程崩溃** | `newTransport` 以 `return newStdioTransport(ctx, s)` 把构造器的 `(*stdioTransport)(nil)` 装进 `transport` 接口——**接口值非 nil**，`reconnectingTransport.restart` 的 `next != nil` 判空失效 → 对该值 `close()` 即**空指针解引用**。触发路径：MCP stdio 服务器启动失败（命令缺失/可执行文件解析失败/沙箱拒绝）→ `Client.redial` → `restart` → **panic 终止整个进程**。长时间运行并对外暴露的服务里，任一插件启动失败即可打挂主进程。修法三层：`newTransport` 显式返回 `nil, err`；三种 transport 的 `close()` 增加 nil 接收者早退；新增 `transportIsNil`（按 `reflect.Kind` 识别接口内 nil 指针）并用于 `restart`/`dialOne`/`close`，`dialOne` 另加「dial 报成功但无 transport」的显式拒绝。回归 5 条（`internal/ext/plugin/transport_typednil_test.go`） |
  | `4ad367f` | **可丢弃产物落盘位置收敛到项目所在卷** | 新增 Go 侧 `internal/base/scratchroot`（解析顺序：调用方重定向过的临时目录（`TMPDIR`/`TEMP`/`TMP` 与系统默认不同）→ `$DEEPTUTOR_SCRATCH_DIR` → 项目所在卷的 `TEMP` 目录 → 系统临时目录；**重定向优先**，用 `TMPDIR` 隔离的夹具必须继续隔离）与 Python 侧 `backend/app/core/scratchroot.py`（同规则）。落盘点迁移：Go 测试临时目录、v1 测试库/数据/技能/插件目录、会话私有临时目录、作业 scratch 树、孤立输出、检查点事务目录、更新缓存、插件/快照/候选/游离 git 临时目录。**同时修掉两处真实缺陷**：① `internal/base/testenv/home.go` 的隔离 home 原先建在**调用者 home 目录**下且清理由单次 `os.RemoveAll` 完成——Windows 文件锁下静默失败，**每次 Go 测试在用户 profile 留一个目录**（本机累计 39 个），现改到 scratch 目录 + 可重试删除；② 写入白名单同步：`internal/contract/config` 的可写根与「不可重叠」集合由 `os.TempDir()` 改为 `tempRoots()`（系统临时目录 + scratch 目录），否则 scratch 目录会被拒写。**有意不改**：锁根（`%LOCALAPPDATA%\reasonix`、`~/.reasonix/locks`）按上游约定必须让同一 OS 用户的各实例收敛到同一处，改成可随环境变化会让互斥失效 |
  | `5ad89e5` | **配置编辑锁注册表回收机制** | 上游锁注册表按「每个被编辑过的配置路径一个锁文件」组织且**只增不减**（上游注释：删掉锁文件会破坏仍持有它的进程的互斥）。注册表按 OS 用户收敛在 `~/.reasonix/locks/config-edits-<uid 摘要>/`，而测试夹具每轮给一个独立 home、每轮一个新配置路径 → **无界增长，本机实测 52099 个 0 字节锁文件**。新增回收：仅回收**同时**满足「闲置超 24 小时」且「锁可被获取（无人持有）」的文件；每次进程内每注册表只清扫一次，单次上限 1000 文件 / 2 秒预算（积压分批排空）；加锁成功后刷新锁文件时间戳，使在用的锁文件不被误判为闲置。锁根位置（收敛点）未改。回归 3 条 |

- **C 盘清理（本项目自己的部署物，只清这些）**：
  - `%TEMP%\reasonix-test-*` **642 个**残留目录（Go 测试临时目录泄漏）→ 已清零；
  - `%TEMP%\reasonix-jobs-*` 68 个、`reasonix-snapshot-*` 2 个、`wt-reasonix-test-*` 2 个、`go-build*` 10 个 → 已清零；
  - `C:\Users\Z\.reasonix-test-home-*` **39 个**（`testenv` 隔离 home 泄漏，根因已修）→ 已清零；
  - `%TEMP%\dt_api_*`（v1 测试库/数据/技能/插件）、`dt_probe*`（自建探针脚本与库）→ 已清零并迁至 `F:\TEMP\deeptutor\v1`；
  - `C:\Users\Z\.reasonix\locks\config-edits-*` **52099 个**陈旧锁文件 → 已清零（根因已修，见上表）；
  - 内核 home/缓存（`%APPDATA%\reasonix`、`%LOCALAPPDATA%\reasonix\{environment,history-search,shell,updates,usage-catalog}`）→ **已迁移到 `F:\Deeptutor\reasonix`**（内容逐文件复制并核对大小），并设用户级环境变量 `REASONIX_HOME=F:\Deeptutor\reasonix`（`setx`）使后续 home/state/cache 落在 F 盘。**锁目录 `%LOCALAPPDATA%\reasonix\{repair-mutation-locks,workspace-leases}` 有意保留在 C 盘**（收敛点，改动会让互斥失效）；
  - **未动**：`F:\tmp`（他项目）、`C:\Users\Z\.cache`（其他工具的缓存，无本项目条目）、`C:\Users\Z\.mavis`/`.minimax`（运行时目录）、`HF_HOME=F:\TEMP\.cache\huggingface`（模型缓存，已在 F 盘）。
  - 工具限制记录：工作区外的删除只能走 `C:\Users\Z\.minimax\bin\mavis-trash.cmd -- <绝对路径>`（`rm --` 会拒收工作区外目标；`Remove-Item`/`.NET Directory.Delete` 被硬策略拒绝）；5 万+ 文件的目录 `mavis-trash` 也会失败，改用一次性 Go 程序（`.git/dt_tools/clean_locks.go`，非跟踪）清理。

- **实测基线（本轮）**：v1 侧 `ruff check app/ tests/` 全通过；`pytest tests/ -q` **1746 passed / 2 skipped**（588.34s，与 iter 397 基线一致，F 盘改造无回归）。Go 侧 `go build ./...`、`go vet ./...` 全绿；受影响包（`contract/config`、`platform/worktree`、`state/sessionstore`、`state/checkpoint`、`platform/update`、`platform/gitcmd`、`base/scratchroot`、`base/testenv`、`base/tempdir`、`base/scratch`、`state/sessiontemp`、`ext/plugin`）测试通过。`internal/base/testenv` 仅剩已登记的环境性失败（`TestWindowsCILegsBypassTheTestResultCache` 要求 CI 工作流含 Windows 上的根模块 `go test` 步骤 → 属 M1.5 待办）。

- **Go 全量 `go test ./internal/...` 的失败分类（2026-10-21 实测，与 AGENTS.md 记录对照）**：① **已登记的环境性失败**（`sandbox/TestIsWindowsWSLBash`、`testenv/TestWindowsCILegsBypassTheTestResultCache`、`runtime/agent/TestChildConstructionForksStayEnumerated`（`REF/` 副本）、`runtime/schedrun/TestKillingTheRunEndsItsWholeProcessTree`、`tools/builtin/TestPipeStatusProbePreservesExitStatusAndReports`）；② **负载/时序敏感**（`base/proc/TestWaitForTrackedCommandReturnsWhenWaitStallsAfterCancel` 1.83s 超时、`frontend/tui/TestWorkspaceGitThroughTheInProcessKernel` 30s 上下文超时、`platform/worktree/TestApplyPaths*` 报「Git is not installed」、`state/sessionstore/TestBranchMetaCrossProcessReadModifyWrite` 25 分钟超时、`state/workspacelease/TestACrossProcessWaitNamesTheHolder`、`tools/builtin/TestBash*`、两个包的 `TempDir RemoveAll cleanup: 目录非空`（Windows 文件锁））；③ **`internal/ext/plugin` panic**——本轮已修（`fd01574`），修复前该包因 nil 指针解引用**整个测试进程崩溃**。另：`internal/assembly/boot` 单包 1340s（超过 Go 默认 600s 上限），`go test ./...` 一次性执行必然 timeout panic——分批 + 超时上限属 M1.5 待定项。

- **下一步（按序）**：① **M1.4 品牌化 + `DEEPTUTOR.md` 建档**（`DEEPTUTOR.md` 至今不存在；`plan/reasonix/01` §五 要求保留上游工程章节并新增本仓规约；`tools/repolint/baseline.json` 仍是上游基线，含 23 条已删文件记录）；② **M1.5 门禁基线重建**（`make check -update` 收紧并 commit 基线）+ **CI 重建**（Go job：gofmt/vet/golangci（钉版本）/go test/repolint + 前端构建；Python job 保留至阶段④）；③ **M1.6**（`make build` 产出 `bin/deep-tutor.exe`，serve 可启动空壳）；④ **M1.3 多实例 spike**（矩阵回填 `plan/reasonix/04` §四）。M1.4 需一并处理 `REASONIX_HOME` → `DEEPTUTOR_HOME` 等环境变量改名（用户级 `REASONIX_HOME` 已设为 `F:\Deeptutor\reasonix`，改名时须同步迁移，否则回落到 `%APPDATA%\reasonix`）。

**iter 396（2026-10-21，v1 Python 后端未审面续审与数据面/暴露面加固轮，✅ 完成）**：承接 iter 392~395 的用户指令（长时间高负载、多用户高频率使用下的稳定性；作为网络服务暴露时的代码安全性；持续进行）。本轮审计 iter 395「下一步」列出的未审面：`api/sessions.py`、`api/notes.py`、`api/memory.py`、`core/backup.py` 的导入/还原链。

- **审查方法**：一个 explore 子 agent 审计四个目标文件（+ 依赖链），产出 **26 条发现**（notes 6 / sessions 4 / memory 6 / backup 8，另列 14 条「已核实干净」及依据行号）；主会话**逐条读原文复核**，只修「已坐实或明确成立」的问题，对最强论断写**实证探针**，并为每处修复补回归（含「修复前必红」的验证）。

- **本轮修复（8 个 commit）**：

  | commit | 主题 | 关键内容 |
  |---|---|---|
  | `6e620ef` | 核心事件载荷直归属 user_id | `plan_created` / `materials_completed` 此前**仅带 `session_id`**：SSE 端须经 `resolve_owner` 查库解析 `session.user_id`（iter 381 起直查、无缓存）——成本为每事件 × 每订阅者 × 一次主键读；且归属解析**依赖 `sessions` 行存活**，行已删（或 rowid 被复用）时解析不出归属 -> 事件按 safe-by-default 丢弃，正主收不到自己的「方案/材料已就绪」通知。现两事件直传 `user_id`（后端插件 API 1.21.0→**1.22.0**，载荷只增键；前端 1.18.0→**1.19.0**，TS 类型补 `user_id?`）：归属判定**零查库**、不依赖会话行存活。`resolve_owner` 行为零改动，插件自定义事件仅带 `session_id` 的回落路径保留。回归 8 条 |
  | `6349cc6` | 笔记导入路径加固 | ① **配额竞态闭合**：`POST /api/notes/import` 此前在函数开头数一次 `existing` 即判 500 条上限、不持 `quota_slot`、不在提交前复查 -> 两个并发 import（或 import 与并发 create_note）各看到同一 `existing`，实际落库可达 ~2 倍上限；现「计数 + 批量插入 + 提交」同在 `quota_slot("note:{uid}")` 内（与 create_note 同键）。② **zip 解压内存放大封死**：`zf.read(name)` 走 `ZipExtFile._read1(MAX_N=1<<31)`，zlib 把整个 deflate 成员一次性展开且**与中央目录声明的 `file_size` 无关**（声明值可谎报，原「声明值 >100KB 即跳过」检查可被绕过）-> 改 `zf.open()` + 64KiB 分块读（单块上限同时约束 zlib 单次解压输出），并按**实际解压字节**判单条上限、累计超总预算即停。③ 整段解析移入 `asyncio.to_thread`（此前在 async 端点内同步解压，大条目卡住全站请求与 SSE 心跳）。④ 导出 zip 重名条目去重后仍可能撞名（解压时后者覆盖前者 = 笔记静默丢失）-> 编号直到不重名。回归 6 条 |
  | `a8b985c` | 记忆系统删除/提升/读取/列表 | ① **删 thread 与在途 chat run 的写并发**：此前只 cancel 不等收敛就删行，而 agent 落库写（`_persist_message`/`_persist_run_step`，走 `asyncio.to_thread` **不可中断**）可能落在「子行已删、thread 行未删」之间 -> 删 thread 行触发 `FOREIGN KEY constraint failed` -> **500 且 thread 未删**；落在 thread 行删除之后 -> 写入自身 FK 失败。现先 cancel 再等收敛（`_wait_runs_settled`，同 sessions.py 范式）+ **有界重试**（回滚重载 ORM 对象），仍失败 409。② **chat run 异常不再外泄内部细节**：SSE `error` 此前直接回显 `str(exc)`（含表名/列名/SQL 片段），改固定文案 + 细节进日志。③ **提升为笔记复用 `/notes` 的上限与配额**（标题未截 256、正文未截 20000、不检查 500 条配额 -> 可把 notes 表撑到远超上限）。④ 产物读取按 20 万字符上限截断并置 `truncated`（此前全量读入内存并整份返回）。⑤ 会话列表去 N+1（逐条 2 次查询 -> limit=100 即 201 次；改两次批量查询）。回归 8 条 |
  | `17321f9` | 备份还原路径加固 | ① **`base_url` 补写时 SSRF 校验**：备份里的 base_url 是**服务端出站目标**且由备份内容控制，导入不校验即可写入 `file://` 或指向私网/云元数据（`169.254.169.254`）的地址（调用期只做字面量判定、不解析 DNS）-> 补与 `api/providers.py` 同口径的 `check_outbound_url`（解析 DNS 后判私网），非法即跳过并记入 `skipped`（`ALLOW_PRIVATE_LLM_BASE_URL=1` 放行）。② **还原侧 thread 上限 200 -> 500**（对齐在线 API——200~500 个 thread 的用户「导出→导入」往返原本静默丢会话）。③ **方案 `version` 按会话内顺序去重**（备份可能含重复 version，schema 无 `(session_id, version)` 唯一约束 -> 同会话重号版本，方案列表/版本选择器错乱；正常备份 version 原样保留，往返保真不变）。④ 行数预算 `_count_payload_rows` **计入知识点笔记**（每个带 `note` 的节点额外插一行 `NodeNote`，此前最多低估一倍）。回归 6 条 |
  | `0da20e0` | 会话删除端点异步化 | `DELETE /api/sessions/{id}` 此前是同步端点，等在途生成收敛（最多 15s）期间**独占一个 anyio 工作线程** -> 并发删除可占满线程池（默认 40 槽）使全站同步端点（含登录）排队。改 async 三段式：准备（写线程）-> 异步等待（新增 `plans.wait_generation_settled_async`，**不占线程**）-> 删除（写线程）+ 文件清理（线程池）；文件清理失败此前被 `except: pass` 静默吞掉（Windows 文件占用导致残留目录运维无从发现），改记 warning。回归 3 条 |
  | `0ae7429` | 备份覆盖面披露（B8） | 后端早已算出「未纳入备份的 9 张表」并随 export/preview/import 回传，UI 一律不显示 -> 设置页「导出全部学习数据」的表述与实现不符。新增 `GET /api/backup/coverage`（事实源 `core/backup.not_backed_up()`，前端不硬编码副本）+ 设置页折叠展示未备份清单。回归 2 条 |
  | `f1660cf` · 本条 | 进度记录 | stage.md 增量 |

- **被实证坐实/明确成立的关键点**：
  1. **zip 解压内存放大（N3）**：探针 `dt_probe_zip_amp2.py`——构造中央目录**谎报** `file_size` 的 zip（97KB 请求体，实际内容 100MB 零字节）：`zf.read(name)` **峰值分配 204.8 MiB**（zlib 单次展开整个 deflate 成员后才在 CRC 处报错，异常被捕获但内存峰值已产生）；改 `zf.open()` + 64KiB 分块读后 **峰值 0.3 MiB**。原「声明值 >100KB 即跳过」只挡得住**诚实**声明。
  2. **删 thread 的 FK 竞态（M1）**：用 SQLAlchemy `before_cursor_execute` 事件在 `DELETE FROM threads` 执行前**注入**一行引用该 thread 的 message（精确复现竞态窗口）-> 稳定触发 `FOREIGN KEY constraint failed`（修复前 500 且 thread 未删）；有界重试后 200 且残留行被级联清干净。
  3. **笔记导入配额竞态（N1）**：490 条预置 + 2 个并发各导入 10 篇 -> 修复后落库**恰好 500**（修复前两者都读到 `existing=490`，可达 510）。
  4. **事件归属依赖会话行存活**：带 `session_id` 的事件在会话行删除后 `resolve_owner` 返回 None -> 事件按 safe-by-default 丢弃（正主收不到）；带 `user_id` 后照常转发（新增回归用例锁定）。
  5. 子 agent 另核实「用户隔离无漏洞、导入不产生路径穿越、`read_file` 无穿越、全局请求体上限存在、重验/事务/补偿链闭合、配额互斥已正确落地」等 14 项**未坐实即不动**。

- **实测基线（本轮）**：全量套件 **1604 passed / 1 skipped**（391.32s，新增 33 条）；唯一 skip 为 `marker_single` 未安装（环境项，非回归）。`ruff check app/ tests/` 全通过；前端 `tsc --noEmit` 与 `vite build` 均通过（备份覆盖面 UI）；OpenAPI **120 paths / 146 operations**（+1 覆盖面端点）。

- **本轮发现但未修（后续 iter 候选，含不修理由）**：
  - **B1 备份导入单事务独占 SQLite 写锁（产品级待定夺）**：一次满额导入（`_MAX_TOTAL_ROWS`=10 万行）在**单个事务**内逐段 flush，且期间夹带大量文件 IO（解析文本/kb 原件/单元内容落盘）-> 写锁被按住数十秒到数分钟，其间全服写请求等满 `busy_timeout`(10s) -> 503/500。两条修法均涉产品取舍：① 下调单次导入行数上限（改变「一次备份能带多少数据」的承诺）；② 文件落盘移出事务（临时路径 -> commit -> rename，事务时长大减但需改补偿链）或拆批提交（引入「部分成功」语义）。**未自行决断，待用户定夺。**
  - **B2 导出无总量上限（产品级待定夺）**：`_MAX_EXPORT_BYTES`(256MB) 只约束 kb 原件内联；notes/messages/plans.content 等大字段不计入预算，重度用户单次导出可把 worker 撑爆。修法二选一同样是产品取舍：超预算 **413 拒绝**（备份拿不到，需另寻导出范围）或**逐字段截断 + 明确披露**（备份不完整，违背「备份」语义）。
  - **M4 `MemoryIn.scope` 被静默忽略**：`POST /threads/{id}/memory` 接受 `scope` 字段但恒强制 `scope="thread"`（用户级记忆另有端点）。改法（删除字段 / 非 thread 报 422）会改 v1 API 语义，而 note/11 记明「v1 契约冻结，只改勘误不改语义」-> 未动，待定。
  - **S1 删资料与在途解析的窄窗口**：`DELETE /sessions/{sid}/materials/{mid}` 与解析端各看各的锁（删除端读 `lock.locked()`，解析端 `setdefault` 后再 `async with`）-> 窗口内解析可能写出孤儿解析文件（DB 无对应行）。子 agent 标注为「推测」，无触发记录；根治需两端共用同一把锁（跨线程获取 asyncio 锁），改动面大于收益，未动。
  - **S4 删会话把绑定 thread 的 `session_id` 置空**：`Thread.session_id` 可空 FK 且 `Session.threads` 无 delete 级联 -> 删会话后 thread 留存但 plan-gen 桥接失效（后续 `approve` 提升为 plan 返回 400 `thread_has_no_session`）。属**优雅降级**而非数据损坏；子 agent 标注为「推测」，未动。
  - **N5 乐观并发秒级分辨率**（iter395 已列，沿用）：`updated_at` 为 SQLite `CURRENT_TIMESTAMP`（秒粒度）-> 同秒双写仍 LWW，波及笔记/节点笔记/偏好/计费表。彻底修法需 `rev` 整数列（跨 4 张表 + 既有基线格式），未动。

- **下一步**：① B1/B2 的产品级取舍已由用户定夺（见 iter 396-B）；② 继续 v1 Python 后端未审面（`api/kb.py`、`api/materials.py`、`api/exports.py`、`api/users.py`、`api/auth.py`、`app/tools/`、`app/agent/`）；③ 回到 Reasonix 阶段①（模块改名 M1.2 → 品牌化与 `DEEPTUTOR.md` M1.4 → 门禁基线重建与 CI M1.5 → `bin/deep-tutor.exe` 冒烟 M1.6）。

**iter 397（2026-10-21，v1 Python 后端剩余未审面续审与暴露面加固轮，🚧 进行中）**：承接 iter 396-B 的「下一步 ② 继续 v1 Python 后端未审面」。本轮审计面：`api/kb.py`、`api/materials.py`、`api/exports.py`、`api/users.py`、`api/auth.py`、`app/tools/`、`app/agent/`（前五项与后两项分派 4 个 explore 子 agent 并行审计）。

- **主会话自查发现并修复（1）**：
  - **启动期缺「开放本机模式」告警**（暴露面，运维侧唯一信号缺失）：开放模式（无 AUTH_TOKEN / 无人设口令 / 无 active PAT）下后端**完全免认证**——任何能连到本端口的客户端都拥有全部数据的读写权（含用户管理与数据导出）。此前该状态**只有前端横幅**（`Layout.tsx` 的 open_mode 横幅）提示，无浏览器的服务端部署看不到任何信号；启动期只对**弱** AUTH_TOKEN / 弱 JWT_SECRET 告警，**比弱 token 更严重的「完全没有认证」反而静默**。现新增 `app.core.security.open_mode_notice(db)`（可测助手）+ 启动期（`init_db` 之后）记 WARNING。回归 4 条（`tests/test_iter397_open_mode_notice.py`）。
    - 附注：测试环境 conftest 固定设 `AUTH_TOKEN=test-token`（否则全用例 401），故**测试环境根本进不了开放模式**——这正是该告警长期未被发现的原因。

- **iter 397-B1（开放模式信任面回环守卫，✅ 完成）**：4 个 explore 子 agent 报告已收 3 份（kb/materials 16 条、exports/users/auth 13 条、app/agent 19 条；app/tools 面报告在途）。主会话**逐条读原文复核**后确认 **E1 为最高危**并已修复。
  - **E1（已坐实，high）：开放模式（零配置免认证）下 `/api/users` 写端点无回环限制 = 远程管理员接管**。`require_admin` → `get_current_user` → `resolve_user` → `get_or_create_open_admin` 在开放模式下对**任何**客户端放行，而 `POST /api/users` 直接把 `is_admin` 取自请求体：远程未认证请求 `POST /api/users {"username":"pwn","password":"…","is_admin":true}` -> **201**，随即可用该账号登录取得管理员 JWT。iter 394 虽已为 `POST /setup` 加回环限制并称其为「开放模式下**唯一**的认领管理员入口」，但该前提被本条证伪（`note/06` 同一表述需同步修正）。`PUT /api/users/{id}`（重置他人口令）与 `DELETE /api/users/{id}`（删号）同理暴露。
    - **实证**：新增 `tests/test_iter397_open_mode_admin_takeover.py`（16 条，含「修复前必红」验证）——修复前远程 `POST /api/users` 实测返回 **201 + 完整用户体**。
    - **修法**：① 回环判定从 `api/setup.py` 上提到 `api/deps.py::is_loopback_client`（共用，避免两处实现漂移），`setup.py` 保留薄封装；② 新增 `deps.require_local_in_open_mode(db, request, message)` 统一守卫（判定置于 `resolve_user` 之前，避免远程调用触发「懒创建 admin」的写副作用）；③ `api/users.py` 三个写端点接入。
  - **同族升级路径一并封堵（E1 的持久后门变体）**：仅堵 `POST /api/users` 不足——**能在开放模式关闭后依然认证的凭据**都能留后门：
    - **PAT（`kind=auth_token`）**：`POST /api/credentials` / `PUT /api/credentials/{id}`（改写值）/ `POST /api/credentials/{id}/activate` -> 开放模式下限回环。改密路径（`users.update_user` / `POST /setup`）虽会吊销 active PAT，但「.env 配 AUTH_TOKEN」的收敛路径**不吊销**（`ensure_admin_user` 只改密码与 `tokens_valid_after`）-> 后门更长命。
    - **供应商档案 `base_url`**：`POST /api/providers` / `PUT /api/providers/{id}` / `POST /api/providers/{id}/activate` -> 开放模式下限回环。`base_url` 是**服务端出站目标**：一旦被改指向攻击者地址，此后本实例所有 LLM 请求（提问 + 注入的文档全文）都发往该地址，**且在运营方随后配 AUTH_TOKEN 关闭开放模式后依然生效**（持久数据外泄通道）。SSRF 策略只拦内网/环回目标，攻击者公网地址照过。
    - **未过度收紧**：`anthropic`/`bing` 类供应商 Key 不建立对本实例的认证能力 -> 不受限；`DELETE`/`deactivate` 属破坏而非「建立信任」，开放模式本就可远程删数据 -> 不额外收紧。
    - 口径统一为：**开放模式下「信任面建立」（认证凭据 + 出站目标）一律限回环**，与 iter 394「`POST /setup` 仅回环」一致；回环（本机）调用全程不受影响。

- **iter 397-B2（事件循环冻结与无界排队，✅ 完成）**：主会话逐条读原文复核 app/agent 与 exports/users/auth 两份报告，确认 **A4 / A5 / A6 / E2 / E4** 五条成立并已修复（均为「在事件循环线程里做重活」或「无界排队」）。
  - **A4（high）：run 创建临界段无界排队 + 锁跨事件循环复用**。`api/memory.py` 原用**模块级全局** `asyncio.Lock`，且**无上限等待**：临界段内含 SQLite 写（写锁争用时可等 busy_timeout 10s），一个慢事务把**所有用户**的 chat run 创建排成串行队列（第 N 个请求等 N×10s，尾延迟无界）。另：模块级 `asyncio.Lock` 只在**争用路径**绑定事件循环，换循环再争用会抛「bound to a different event loop」（测试逐用例 `asyncio.run` 起新循环）。修法：① `core/concurrency.py` 新增 `loop_lock(key)`（按事件循环缓存，同 `_loop_sem` 口径）与 `acquire_bounded(lock, timeout)`（有界等待 + 竞态归还，防丢锁/漏许可）；② 临界段改为 `acquire_bounded` + **10s 上限**，超时 -> **503 chat_run_busy**（快失败可重试，取代无界排队）。约束语义（每 thread 至多 1 个 running run + 全局上限）**不变**——仍由同一临界段保证「计数 + 插入」原子性。
  - **A5（high）：强制收敛 run 终态在事件循环线程同步写库**。`api/memory.py` 的 `_run_chat` 收口 finally 里 `_force_run_terminal(run_id, …)` **同步调用**（内部 `SessionLocal()` + `commit()`），写锁被占时**冻结全站**（连 SSE 心跳都停）；而同文件 `_finalize_chat` 的同类调用早已是 `await asyncio.to_thread(...)`——读原文确认口径不一致。修法：两处改 `await asyncio.shield(asyncio.to_thread(...))`（既不占事件循环，又不被本轮取消打断收口，与 iter 369「shield + commit 工作区」同口径）。
  - **A6（high）：agent loop 每 step 在事件循环线程同步查库**。`agent/agent_loop.py` 写路径早已按 iter 366 移出，**读路径漏了**：`db.get(Run, run_id)` + `load_thread_messages`（50 条含 JSON 大字段的 Message）每 step 都在事件循环线程执行。修法：两处改 `await asyncio.to_thread(...)`（Session 顺序访问，同 `record_metadata_usage` 既有口径）。
  - **E2（high）：登录是未认证的 CPU 放大器**。`POST /api/auth/login` 原为**同步端点** -> 跑在 Starlette anyio 工作线程池（默认 40 槽）；每次尝试一次 pbkdf2-hmac-sha256 **600k 轮**（子 agent 实测 ~0.19s/次 CPU），**40 个并发登录即可占满全部槽位**，使**所有同步端点**（用户管理/会话/导出/分享）排队——而 per-IP 桶允许同一分钟内 40 个并发，单 IP 即可触发。修法：① 端点改 `async def`（不再占 anyio 槽）；② 口令校验移到**独立小线程池**（`concurrency.password_pool()`，`pwd-verify` 前缀，4 workers）；③ 加**并发闸** `password_verify_slot`（按 `settings.max_concurrent_password_verifications`，默认 4；等待超时 -> **429** 快失败）。另修注释与实现不符处：原文写「~200k 轮」，实现是 600k 轮。
  - **E9（low）：登录用户名不归一化**。建号时 `strip`（`users.create_user`）而登录不 strip -> 带首尾空白的账号**永远登不上**（API 客户端常见），且两个限流桶键会因空白变体被分裂成多桶。修法：登录入口统一 `strip` 后再查库与分桶。
  - **E4（medium）：md 导出的 Markdown 渲染留在事件循环**。`tools/export_plan.py` 的 `render_markdown(plan)` 同步执行（溢出方案可达 MB 级），而**同仓 PDF 路径早已 `await asyncio.to_thread(render_markdown, content)`**——此前只把落盘移了、渲染没移。修法：渲染一并移入 `to_thread`。
  - 回归：新增 `tests/test_iter397_event_loop_offload.py`（11 条，逐条断言「不在事件循环线程执行」+ 有界等待/锁隔离语义）。相关面回归（auth/open_mode/users/setup/chat-lifecycle/export/memory/agent/concurrency）**129 passed**。

- **iter 397-B3（数据一致性：会话配额锁 + 上传先落盘后建行 + kind 归类，✅ 完成）**：复核 kb/materials 报告的 K2 / K3 / K13 三条，均已坐实并修复。
  - **K2（high）：会话数配额（每用户 200）实际可被并发突破**。`api/sessions.py` 的注释宣称「键与 materials/kb 的会话数配额同命名空间 -> 跨端点互斥」，但读原文确认：`POST /api/materials/clip`（materials.py:686）与 `POST /api/kb/generate`（kb.py:860）**只 count 不加锁**——并发请求可同时通过「未达上限」检查，实际会话数按并发数突破上限（DB 行 + 会话目录无界增长）。修法：① `api/sessions.py` 抽出 `check_session_quota(db, user_id)` 作为**唯一事实源**（上限与键 `session_quota:{uid}`）；② 三条建会话路径统一在**同一把锁**内做「计数 + 插入 + 提交」——`sessions`（同步端点，`quota_slot` 安全）、`materials.clip`（**async** 端点 -> 走 `guarded_commit_async`，锁在**写线程**内持有；不可在事件循环线程跨 `await` 持 `threading.Lock`，否则同 stripe 的其他协程会被阻塞住事件循环）、`kb.generate`（同步端点）。
  - **K3（high）：上传「先落库、后落盘」留下「DB 有行但文件缺失」的残局**。`POST /api/materials/upload` 此前先提交 pending 行、再写原件；行提交后、文件写完前进程被杀或落盘失败 -> 一条**永远没有原件**的 pending 行（用户点解析只得 400「该资料无原件」，且**无任何后台路径能修**）；且补偿路径（`db.delete` + `commit_async`）自身失败时会掩盖原异常。根因：原件最终名必须含**行 id**，而行 id 要等 INSERT 后才有。修法：改「**先暂存落盘 -> 建行 -> 事务内改名**」两段式（与备份还原 iter 396 B1 同款）：① 预暂存（**事务外、无写锁**）落到会话暂存目录 `data/sessions/<sid>/.staging/`（随机名，不依赖行 id）；② `after_flush` 回调里改名成正式名 `originals/<material_id>.<ext>`（纯元数据操作，~ms 级，不拖长写锁）。由此**行可见必然晚于文件就位**；反向的短暂孤儿（文件已落、行未建）由失败路径清理，对用户不可见。
  - **K13（low）：会话资料上传的 kind 恒为 "file"**。`materials.py` 建行时 `kind="file"` 硬编码，而同一张图片/音视频走**知识库**接口会被正确归为 image/audio/video -> 资料列表的类型展示与下游按 kind 分流均与知识库口径失真。修法：归类函数从 `kb.py` 上提为 `app.api.materials.kind_for_ext`（两处共用）。
  - **有意保留（已评估、非缺陷）**：`kb.generate` 的解析文本复制（≤20 篇 × 500KB ≈ 10MB）仍在写事务内落盘——该路径**有界**（最多 20 个文件、约 100ms），且**行可见前文件已就位**（无数据一致性问题），故不引入额外的暂存/改名复杂度；与 iter 396 B1 的差别在于 B1 面对的是「最多 10 万行」的还原，量级完全不同。
  - 回归：新增 `tests/test_iter397_data_consistency.py`（5 条：三条建会话路径同锁 + 并发不破上限 + 上传后「行与文件同时在位、暂存目录无残留」+ 落盘失败不留残局 + kind 归类）。**已验证「修复前必红」**：把 clip 的配额键临时改成非共享键 -> 白盒用例立即转红。相关面回归 **106 passed**。

- **iter 397-B4（资源耗尽面：导出限流/体积上限、URL 总时长、检索限流，✅ 完成）**：复核 kb/materials 报告的 K1 / K4 / K7 三条并修复。
  - **K1（high）：`GET /api/kb/export` 无限流、无体积上限 -> 系统盘可被写满**。该端点把用户全部 KB 原件 ZIP 化落进**系统临时目录**（`SpooledTemporaryFile` 超 64MB 即滚到磁盘），200 篇 × 50MB = **10GB/次**；无速率限制 -> 反复调用即可写满系统盘，且每次全量构建占满一个 anyio 工作线程。修法：① 加限流 `export:{uid}` **12/min**（与 `exports.py` / `plans.py` / `materials.py` 三个导出端点同桶同口径——此前 kb 导出是唯一漏限的导出面）；② 加**总字节上限 500MB**：打包前先累加待打包文件的实际大小，超限直接 **413**（不落临时文件、不占工作线程），提示用 `ids` 参数分批导出。
  - **K4（high）：服务端出站 URL 抓取无总时长上限 -> 请求可被无限挂起**。`httpx.Timeout(URL_TIMEOUT=30)` 只约束**两次读之间**的间隔，正文总量由 10MB 字节上限约束——攻击者控制的「慢滴」服务器（每 29s 吐 1 字节）可让请求无限期挂起；`kb.create_url_document` / `materials._store` 的 URL 分支**也没有**端点级 `wait_for` 兜底（`kb.parse_document` 的文件分支有）。修法：在 `app/tools/parse_material.py` 的 `_parse_url` 外层加**总时长预算** `URL_TOTAL_TIMEOUT = 60s`（含重定向的整段抓取），超时抛 `upstream_timeout`；一处收口即覆盖全部 URL 调用点。
  - **K7（medium）：知识库内容检索无限流 -> 磁盘 I/O 放大 + 工作线程占满**。`GET /api/kb/documents?q=...` 每次把最近 **50 篇已解析文档全文读进内存**（≤25MB）再逐篇 `find`（无索引，诚实边界见 note/09），脚本循环即可打满磁盘 I/O 并占满 anyio 线程池。修法：对**带 q 的检索路径**加限流 `kb_search:{uid}` 30/min；**普通列表（无 q）是廉价 DB 查询，不限流**（避免误伤正常浏览）。
  - 回归：新增 `tests/test_iter397_resource_limits.py`（7 条：导出限流/超限 413/正常导出不受影响、URL 总时长预算（含「挂起被中止」与「预算 > 单次读间隔」两条）、检索限流/普通列表不受影响）。相关面回归 **102 passed**。

- **iter 397-B5（契约一致性：删用户收敛、错误码登记、文本口径、路径越界、解析失败守卫，✅ 完成）**：复核 kb/materials 与 exports/users/auth 两份报告的契约类条目，确认 **E3 / K9 / K12 / K14 / K16 / E10** 成立并修复。
  - **E3（high）：`DELETE /api/users/{id}` 不等在途 chat run 收敛 -> 500 + 费用黑洞**。此前只「取消」不「等收敛」，且等的是**方案生成**（`wait_generations_settled` 轮询 `_STREAMS[session_id]`）而 chat run 在 `_RUN_STREAMS`（按 run_id）——**根本不在等待范围**。后果：① agent 循环随后写 `api_usage` -> `user_id` FK 违约被 `cost.py` 吞掉 -> 本轮 LLM 费用无法入账（日预算系统性少计）；② `messages`/`run_steps` 插入与删除事务交错 -> 删除事务提交失败 -> **HTTP 500 且用户根本没被删掉**（文件清理也随之跳过）。修法：按 `memory._wait_runs_settled`（与 `DELETE /threads/{id}` 同一范式）等待该用户 running 的 run id；删除提交加**有界重试**（回滚 -> 清 identity map -> 重载重试），仍失败 -> **409**（可重试语义，不以 500 暴露内部约束错误）。
  - **K16（medium，守卫缺陷确定）：解析失败守卫不完整 -> 用空文本覆盖既有解析文本**。守卫写成 `if out.status == "error" and out.errors:`——`status` 为 error 而 `errors` 为空（`ToolOutput.errors` 默认空列表）时**继续往下走**，`out.data.get("text", "")` 得空串 -> 写出**空解析文件并覆盖原有解析文本**、`status` 被置 `parsed`、`parsed_chars=0`（与端点承诺「解析失败行保留状态不变（可换档重试）」正相反：失败时反而把好数据清了）。修法：新增 `app/api/errors.py::raise_parse_failure(out)`（status 非 error 放行；error 且 errors 为空 -> 用兜底码报错），并在 kb/materials 的 **5 处**解析守卫统一改用；另在两个「重解析」端点加**空文本守卫**（空结果不落盘、不改状态）。
  - **K9（medium）：解析错误码未登记进统一错误码表**。`degraded` / `parse_failed` / `dependency_missing` 不在 `CODE_STATUS` -> 一律落到默认 **400**（此前未登记，落默认与显式登记 400 等价）。修法：`CODE_STATUS` **显式**登记这 3 条为 **400**——**HTTP 语义保持不变**。初版曾拟把 `dependency_missing` 升 503、`parse_failed`/`degraded` 升 422（全量套件当场打红：`tests/test_api.py::test_upload_image_png_passes_validation` 钉住 `dependency_missing -> 400`），按 note/11「v1 契约冻结，只改勘误不改语义」**撤回**：状态码升级属契约变更，须先改 API.md 与前端分支，另行裁决。
  - **K12（low，**承诺与实现不符**）：知识库文本入库的 docstring 写「解析文本即原文（**经 500k 截断护栏**）」，实现**根本没有截断**（`text` 上限 2MB，可整份入库）；且两条入库路径口径不同——`create_text_document` 哈希用原文、`add_text_document_sync`（生成产物自动沉淀）哈希用 strip+截断后的内容 -> 同一段文本经 API 提交与经自动沉淀入库得到**不同的 content_hash**，产生**两篇文档**（重复占配额、重复占磁盘）。修法：抽出 `kb._normalize_kb_text`（去首尾空白 + 500k 截断）作为**入库文本与 content_hash 的唯一口径**，两处统一调用（并按归一化后的文本解析入库）。
  - **K14（low）：4 处读库内路径缺 containment 校验**。`get_document_raw` 有 R20 防线（resolve 后须仍在本用户 kb 目录内），但 `get_document_parsed` / 导出打包 / `kb.generate` 读解析文本 / `_read_doc_text`（问答上下文）/ `materials.read_unit_content`（单元内容）**皆无** -> DB 路径一旦被篡改（备份还原 / 运维脚本 / 手工改库）即退化为任意文件读。修法：新增 `storage.read_unit_content_guarded(session_id, path)`，kb 侧 4 处统一加 `is_within_root(_kb_root(user.id), resolved)` 判定。
  - **K10（low）：`kb.upload_document` 落盘前未释放读快照**（同文件的 `create_text_document` / `create_url_document` 与 `materials._store` 都有 `db.rollback()`）-> 50MB 写盘窗口内连接持读事务，WAL 下挡 checkpoint、升级写会 BUSY_SNAPSHOT。修法：补 `db.rollback()`。
  - **K15（low）：知识库问答的限流排在上下文组装之后** -> 被限流的请求已经付了「20 篇 × ~500KB 同步文件读」的 I/O 成本。修法：限流与日预算检查前移到文档查询与上下文组装之前。
  - **E10（low）：导出格式名无长度上限** -> 几十 MB 的 `format` 串会被原样回显进 400 错误信息（请求体上限 55MB）。修法：`ExportIn.format` 补 `max_length=64`。
  - **回归中发现并修掉的自身缺陷**：`storage.py` 的 K14 补丁一度把 `read_unit_content` 的**函数体孤立**（新函数插在 docstring 与函数体之间 -> 原函数变成返回 None）——由 `test_api_materials_export.py` 三条用例当场抓住并修复；同时用 AST 扫了一遍 `storage.py` 全部顶层函数，确认无其他空函数体。
  - **全量套件打红后自查出的两处自身回归**（iter 397-B5 首轮全量：1668 passed / 2 failed）：① **K9 的状态码升级违反契约冻结**（`dependency_missing` 400 -> 503）——`tests/test_api.py::test_upload_image_png_passes_validation` 钉住 400，已撤回（见上）；② **K14 的 containment 守卫与隔离用例的人工路径冲突**——`tests/test_api_isolation.py::test_unit_content_cross_user_404` 用 `tmp_path` 造单元内容文件（生产路径恒为 `data/sessions/<sid>/materials/unit_<id>.md`），新守卫判定越界 -> **属主本人也拿 404**。处置：**改用例的构造口径**（断言一字未动：他人 404 / 属主 200），使文件落在真实会话目录内，与新不变量一致；另补一条白盒用例显式断言「路径被改到会话目录外 -> 404」。
  - 回归：新增 `tests/test_iter397_contract_consistency.py`（**9 条**：删用户等待在途 run、错误码登记且状态码不变、两条入库路径同内容同哈希、500k 截断、解析文本越界 404、原件越界 404 回归护栏、**单元内容越界 404**、解析失败不覆盖既有文本、守卫助手单测）。相关面回归 **118 passed**；**全量套件 1670 passed / 2 skipped**（首轮 1668 passed / 2 failed，两处自身回归已修，见上）。

- **iter 397-B6（app/tools 工具契约面：成本计量、配额互斥、专用写池、出站限流、落盘边界、SSRF 不变量，✅ 完成，`a5e8a87`）**：主会话读完 app/tools 面 16 条发现并逐条复核原文，确认 **T1 / T2** 为 high（成本黑洞 + 配额绕过），**T3~T8** 成立，**T9~T13 / T15** 为一致性与纵深防御项，**T14 / T16 评估后不修**（见下）。
  - **T1（high）：资源筛选的 LLM 用量只记最后一次尝试 -> 成本黑洞**。`_screen_resources` 原写 `last_usage = resp.usage`（每次**覆盖**）、异常分支再 `last_usage = None`（**清零**）——重试中已消耗的 token 完全不落 `api_usage`、不进会话预算；「第 1 次成功但输出不可解析（被 max_tokens 截断）+ 第 2 次超时」的组合下 `out.metadata` **整个不赋值**（`if screen_usage is not None` 不成立）-> 用户反复触发即可绕过 `check_daily_budget`（按 api_usage 的 **cost** 求和）。修法：改 `accumulate_usage` 累加（与单元补料路径 `_screen_unit_resources` 同款口径），异常分支**不清零**，失败路径也带回已计量 usage；另 `build_metadata` 补 `model` 与 `ctx.model_pricing`——此前两者皆缺，**非 Anthropic 模型的筛选成本记 0**（走族默认 miss）。
  - **T2（high）：Agent 记忆写入与 REST 路径对同一配额无互斥**。`agent_tools.memory_save` 的「计数 -> 插入」未持任何锁，而 REST `POST /memories`（`api/memory.py`）持 `quota_slot("memory:{uid}")`——两条路径并发时可同时通过 count 检查，**每用户记忆数按并发数突破 2000 条上限**。修法：Agent 路径在同一把锁内做「计数 + 插入 + 提交」（键与 REST 完全一致）。
  - **T4（medium）：工具层 DB 写跑在 asyncio 默认线程池**。`core/dbwrite.py` 的设计意图原文是「写提交放入**专用写线程池**（与 asyncio 默认线程池隔离：长锁等待不占用 to_thread 的文件 IO 槽位，避免大文件落盘被写锁等待饿死）」，但 `memory_save` / `memory_delete` / `promote_artifact` 三处仍用 `asyncio.to_thread`——SQLite 写锁等待（busy_timeout 10s）占住默认池线程，多用户并发下把同池的文件 IO 槽位一起饿死。修法：三处改走 `dbwrite.run_db_write`；`promote_artifact` 进一步把**文件复制**（to_thread）与**库写入**（run_db_write）拆成两段，flush+commit 保持同线程。
  - **T5（medium）：Agent 联网工具无任何出站速率限制**。`web_search` / `web_fetch` 此前不限流（材料 URL 抓取路径有限流，两条出站面口径不一致），单 run 最多 25 步可连打外呼；`web_fetch` 还会白下载 10MB（回灌给模型的正文最终被 `_MAX_TOOL_RESULT_CHARS` 2 万字符截断）。修法：① 新增 `agent_search:{uid}` / `agent_fetch:{uid}` 两个限流桶（30/min）；② `web_fetch` 的抓取体积上限降到 256KB（`_parse_url` 增 `max_bytes` 参数，默认值不变，不影响材料/KB 路径）。
  - **T6（medium）：`export_plan` 在 ctx.data_dir 缺失时回落到相对路径 "data" -> 向管辖目录之外落盘**。相对路径按**进程 CWD** 解析（服务从 `backend/` 启动时落到 `<repo>/backend/data/…`），而 `settings.data_dir` 锚定在仓库根 -> 该目录既不被 maintenance 清理、也不被 backup 收录（长期孤儿目录）；且 `root` 取的是同一个 "data"，containment 守卫**恒真、形同虚设**。**读原文发现生产路径同样中招**：编排器上下文（`api/plans.py` 的 `ExecContext(trace_id=stream.trace_id, …)`）**未带 data_dir**，而 `export_plan` 是 PIPELINE 末段成员 -> 方案生成每次都往目录外写 `exports/plan.md`（插件面 `plugins/api.py` 构造的 ctx 同样无 data_dir）。修法：① `ctx.data_dir` 缺失 -> 明确报错 `no_data_dir`，不再回落；② 编排器上下文补 `data_dir=settings.data_dir`；③ 写入改**原子写**（`storage.atomic_write_text`）——此前直接 `write_text`，崩溃/IO 错留下半截文件（会被 `commit_run_workspace` 当作正式产物登记）。
  - **T8（medium）：Bing 网页版检索用 `follow_redirects=True`，只校验终态 host** -> 违反本仓明确的「重定向逐跳复检 SSRF」不变量（`verify_url` / `parse_material` 均为 `follow_redirects=False` + 逐跳复检）。中间跳由外部可控，可指向内网/云元数据端点。修法：改逐跳手动跟随，每跳校验 host 属 `bing.com`/`*.bing.com`（精确域判定，iter 369 口径），跳数上限 5。
  - **T7（medium）：`verify_url` 的 `stage_level` 参数逐候选传入却从未被读取**，而对外描述承诺「校验 URL 可达性**与内容难度适配**」-> 表达与实现失真。修法：移除死参数（保留有序去重），描述改为「校验 URL 可达性（内容是否适配学习阶段由 AI 筛选阶段判定）」；另修 `export_plan` 的描述（原写「导出方案为 md/pdf/ics」，实际只支持 md、其余降级）。
  - **T9（low）：SSRF 判定有两份独立实现**。`verify_url._is_safe_url` 只共用 `urlguard` 的**常量**、不共用逻辑，而 `core/urlguard.py` 自称「单一事实源」-> 任何一侧新增规则都会静默漂移。修法：`_is_safe_url` 收敛为 `urlguard.check_outbound_url(url, resolve=True)` 的薄封装（判定语义与拒绝文案逐条等价，并额外获得 urlguard 的 DNS 判定缓存，同 host 5 分钟内不重复 getaddrinfo）。
  - **T10 / T11（low）：`schedule_plan` 的上游声明与 start_date 优先级**。① 实际读 `upstream.understand_intent`（start_date / daily_minutes）却**未声明** `upstream_reads` -> 「并行组同组工具互不读取对方输出」的 import 期强制断言对它**完全失效**（若日后把 understand_intent 移入同组，会并发读到快照里尚不存在的数据且无启动期报错）；② prompt 承诺「日期从 **args.start_date**（或 upstream…）开始」，实现**只读 upstream** -> 调用方把 start_date 放进 args 会被静默忽略。修法：补 `upstream_reads={"understand_intent"}`；`args.start_date` 优先于 upstream。
  - **T12（low）：`workspace.write_file` 写失败时 tmp 残留**（磁盘满/权限不足/杀软占用）——残留文件随后被 `list_files` 列出、被 `commit_run_workspace` 当作正式产物登记。修法：照 `storage._atomic_write_bytes` / `agent_tools` 的既有写法补 try/except 清理。
  - **T13（low）：`memory_save` 在 scope=thread 但无 thread_id 时写出 `thread_id=NULL + scope="thread"`** 的矛盾行（thread 视图按 thread_id 过滤查不到，却出现在 user 视图）。修法：明确报错。
  - **T15（low）：`build_plan_slides` 把 LLM 抽取的 `intent.level` / `intent.goal`（用户可控文本）未转义拼进 `content_html`**（同函数其余字段均走 `_esc`）。当前不可利用（`render_slides_html` 对 content_html 强制白名单净化），属纵深防御缺口。修法：补 `_esc`。
  - **有意不修（已评估、写明理由）**：① **T14**（`export_plan` 的 `plan_id` 在编排器路径恒为 "plan"）——HTTP 导出路径确实传了 `plan_id`（`exports.py` 传 `str(plan_id)`），编排器路径产出的是**无消费者的副产物**（备份走 `plan_{id}.json`），覆盖写同一文件有界（每会话 1 个）-> 不构成缺陷；② **T16**（`parse_material(kind="file")` 接受任意绝对路径）——该工具的契约就是「解析调用方给出的文件路径」，HTTP 面永远传受控落盘路径（`data/sessions/<sid>/originals/…` / `data/kb/<uid>/…`），插件面本就是进程内可信代码（可直接读文件），加 data_dir 限制会破坏工具语义并使 15+ 条既有单测失效。
  - 回归：新增 `tests/test_iter397_tools_hardening.py`（**24 条**：筛选用量累加/失败仍计量/metadata 带 model 与计价表、Agent 记忆同锁、thread 作用域守卫、专用写池、联网限流、抓取体积上限、export_plan 无 data_dir 拒写与落盘位置、编排器 ctx 带 data_dir（AST 断言）、material 拒绝 + schema 无 material、verify_url 描述与死参数、Bing 重定向拒外域 + 禁自动重定向、SSRF 收敛与私网护栏、schedule_plan 上游声明 + start_date 优先级、工作区 tmp 清理、幻灯片转义）。

- **iter 397-B7（users/auth 面：.env token 管理员映射、自重置口令返 token、删用户清工作区、列表分页限流、登录限流桶收敛，✅ 完成，`066d8ed`）**：复核 exports/users/auth 报告的 **E5~E8 / E11~E13**，逐条读原文复核后修复 6 处（E11 并入 E6 一并解决）。
  - **E6（medium，部署死路）**：仅配 `AUTH_TOKEN`、未配 `ADMIN_PASSWORD` 的全新部署——**库里没有任何管理员**，而 `.env` token 的回退路径建的是 `is_admin=False` 的默认用户 -> `POST /setup` 403「首次设置需要管理员权限」、`/api/users` 全 403 admin required、`POST /api/auth/login` 无任何可用凭据 -> **实例无法经 API 完成首次配置**（死路）；唯一自愈路径是重启进程时 `ensure_admin_user` 把那个 `username=None` 的默认用户提升为管理员，而该分支要求「库中恰有一个无 username 的用户」，**一旦出现第二个用户即失效**。修法：新增 `core/security.ensure_admin_for_env_token(db)`——`.env` token 命中且库中无管理员时，把当前解析到的身份（与旧路径同口径：**首个用户**，**既有数据归属不变**）提升为管理员并补登录名（口令留空：`.env` token 即其凭据，登录仍不可用，与「ADMIN_PASSWORD 空 -> 不创建登录账号」一致）；已有管理员时行为不变。`deps.resolve_user` 的 `.env` 分支改调该助手（`get_default_user` 随之移除）。**为何不在启动期建锚点**（评估结论）：启动期建会使**每个**部署都多出一个系统管理员行 -> `GET /api/users` 返回内容变化（契约面），且测试固件普遍自建 `username="admin"` 的用户（`uq_users_username` 唯一约束）易撞名；改为**按需惰性创建**（仅当 `.env` token 真被使用且库中无管理员时），把「是否多一个系统账号」的决定权交给实际使用方。
  - **E11（low，并入 E6 一并解决）**：此前 `GET /api/auth/me` 带回环库时会建出 `username=None` 的默认用户，该账号要到**下次重启**才被隐式提升为管理员（状态迁移藏在重启里，易被误判为「已配置」）；现 `.env` token 首次使用即落定为管理员，**不再有「待提升」的中间态**。
  - **E5（medium，用户交互逻辑错误）**：管理员重置**自己**的口令 -> `tokens_valid_after` 更新 -> 旧 JWT 立即失效 -> 前端下次请求 401 -> 清本地会话跳 `/login`（无任何解释）。此前「不能操作自己」的护栏只覆盖管理员开关，**未覆盖口令分支**。修法：按 `POST /setup` 同一范式在响应里返回**新 token**（前端就地换存）；重置**他人**口令不返回 token。另 `tokens_valid_after` 取整秒（与 `setup.py` 同口径，避免同秒签发的 token 被小数位误杀）。
  - **E7（medium，磁盘残留）**：删用户只清会话目录与知识库目录，其 **run 工作区**（`data/runs/<rid>/workspace/` 与 `data/workspaces/<tid>/<rid>/`）残留 24~48 小时（等每日维护任务回收：冷却窗 24h + 周期 24h），期间文件仍在磁盘上（可能含用户材料与草稿）；空的 `data/workspaces/<tid>/` 父目录**永不回收** -> 长期运行持续堆积。修法：新增 `workspace.delete_run_workspace(thread_id, run_id)`（scratch + committed + 空父目录回收；路径经 `_digit` + `is_within_root` 双重约束），`delete_user` 提交后按该用户全部 run 调用。
  - **E8（medium，暴露面）**：`GET /api/users` 无分页、无限流 -> `db.query(User).all()` 一次性把整张 users 表载入内存并全量返回（账号数千/数万级时单次响应可达 MB 级）。修法：加 `limit`（默认 100 / 上限 500）+ `offset` + `total`，并加管理员维度限流 `users_read:{admin_id}` 60/min；前端 `UserManager` 改 `useInfiniteQuery`（「共 N 位用户（已显示 M 位）」+「加载更多」）。
  - **E12（low，定向锁死 + 账号存在性侧信道）**：账号维度限流桶 `login:acct:{username}` 放在**校验之前**且**跨 IP 全局** -> 任何人对某用户名连打 20 次即把该账号**全局锁 1 分钟**（真实用户被挡在门外）；且该桶的 429 文案与 IP 级不同 -> 可用于确认某账号存在且正被攻击。修法：账号桶改为**只计失败**、**口令正确一律放行**（账号不会被锁死；分布式爆破的每分钟失败次数仍被全局桶封顶），429 文案与 IP 级统一。
  - **E13（low，限流器被攻击者选择性失效）**：登录限流键用攻击者可控的**用户名原文**（每次尝试新增 2 个键）-> 键数达 100k 时 `ratelimit` 触发 `self._hits.clear()` -> **进程内全部限流桶**（登录/导出/分享/setup/认证失败）被一次性清空，限流在**攻击者选定的时刻**整体失效。修法：① 限流键与日志改用用户名 sha256 前 12 位摘要（键长有界、明文用户名不留存于进程内存）；② 限流器触顶改为**淘汰最老的一批键**（dict 保序，一次 4096 个）而非全清，存量桶（含正常用户的）继续有效；③ 补键长上限 160。
  - 回归：新增 `tests/test_iter397_users_auth_hardening.py`（**14 条**：`.env` token 映射到管理员 / setup 不再 403 / 提升后身份不变（数据归属不变）/ 锚点幂等 / 已有管理员不被替换、自重置口令返新 token 且立即可用 / 他人口令不返 token、删用户清 run 工作区（含空父目录回收）、列表分页与越界 422 / 列表限流 429、账号桶只计失败且口令正确放行 + 429 文案统一、限流键用摘要（明文不落键）、限流器 FIFO 淘汰（存量桶不被全清）、键长封顶）。
  - **提交前自查（E6 的测试面副作用已逐条排除）**：`.env` token 身份变化会影响「谁拥有既有数据」与「用户名唯一约束」两类断言。已用脚本对 2173 个测试函数做**函数内顺序扫描**：同一函数内「先发 `.env` token 请求、后插入 `username="admin"` 用户」的**真实冲突 0 处**（唯一候选 `test_api_settings_prefs.py::test_prefs_llm_max_tokens_set_and_clear` 实为 `_seed_admin()` 在前、后者只是查库，非插入）；`get_default_user` 语义的 3 处引用（`test_api_search.py` / `test_api_sessions_list.py` / `test_iter397_contract_consistency.py`）均为「先经 API 物化默认用户再建 other」的**隔离用例构造口径**，身份解析口径未变（仍是首个用户）-> 断言仍成立。
  - **首轮全量套件打红 3 条（均为 E6 的预期连带，非回归）**：`tests/test_iter390_auth_matrix_incremental.py`（2 条）与 `tests/test_plugin_hotreload.py`（1 条）此前借 `auth_headers`（`.env` token）充当「非管理员」——E6 之后 `.env` token 映射到管理员，该前提不成立，实测 `403 != 200`。处置：**断言一字未改**，仅把「非管理员」身份从 `.env` token 换成**普通用户的 JWT**（`create_token` 直签）——被测不变量（非管理员 -> 403）完全不变。复跑 28 条全绿。
  - **门禁**：`ruff check app/ tests/` 全通过；全量套件 **1709 passed / 1 skipped**（唯一 skip 为 `marker_single` 未安装，环境项）；前端 `tsc --noEmit` 与 `npm run build` 均通过。

- **iter 397-B8（app/agent 面：错误对外可见、审批信号拆分、会话并发、工具白名单、SSE 节流、计量与契约对齐，✅ 完成，`aac0266`）**：复核 app/agent 报告的 A1~A19（A4/A5/A6 已在 B2 修完；A2/A10 属产品裁决，见 B9），本批落地 **A1 / A3 / A7 / A8 / A9 / A11 / A12 / A13 / A14 / A15 / A18 / A19**（A16/A17 见下）。
  - **A1（high，失败对用户完全不可见）**：provider 异常时**只发 `done{status:"error"}`**，而前端 `done` 分支不读 `d.status` -> 用户看到「回答正常结束」而内容实为失败截断。修法：`_finalize_chat` 在终态为 error 时补发 `error` 事件（固定文案，细节进日志；前端 `Chat.tsx` 已有 `error` 分支，无需改前端）。
  - **A9（medium，内部细节外泄）**：`AgentRunResult.error = str(exc)`（SDK 文本含 base_url / 请求体片段 / 模型名）经 `run.error` 由 `GET /api/runs/{id}` **原样回显**给浏览器（iter 396 只加固了 SSE 路径）。修法：改固定文案「对话执行失败，请稍后重试」，异常原文只进服务端日志。
  - **A3（medium，审批提议被记为「已取消」）**：`promote_artifact` 复用 `ctx.cancel_event` 置位 -> `agent_loop` 下一轮**先**命中取消检查 -> run 记为 `interrupted`（而非 `awaiting_approval`），第 371-373 行的 awaiting_approval 检查成**不可达死分支**，同批次其余工具被跳过并写成「已取消」占位。修法：`ExecContext` 新增 `approval_pending` 信号（与 `cancel_event` 分离）；`promote_artifact` 置 `approval_pending`；`agent_loop` 优先判定审批待确认 -> 正常收口；工具批次遇审批待确认即止，占位文案区分「已取消」与「待审批确认，未执行」。
  - **A7（high，同一 Session 被两线程并发 commit）**：`commit_run_workspace` 用调用方传入的 `db`，而调用点在 `asyncio.shield(asyncio.to_thread(...))` 内——被取消时**工作线程继续持有该 db** 跑 add/commit，主协程随即在事件循环线程改同一 db 的 run 行并 `commit_async(db)` -> 同一 Session 并发 commit（互相 rollback / 丢写 / `IllegalStateChangeError`）。修法：`commit_run_workspace(run_id, thread_id)` **自持会话**（内部开独立 `SessionLocal`），与主流程零共享。
  - **A8（medium，工具越权 + 成本漏记）**：对话路径只校验 `registry.has(name)`，而 registry 含**全部 pipeline 工具与插件工具** -> 模型（可被提示注入）可调用广告之外的 `parse_material` / `generate_quiz` / `kb_answer` / `export_plan`；且 `out.metadata` 被整个丢弃 -> 工具内 LLM 调用烧的 token 不进 `api_usage`，日预算（只信 api_usage 求和）形同虚设。修法：按**广告给模型的工具集**分派（非白名单一律「未知工具」），并把工具 metadata 落 `api_usage` + 计入会话预算（错误路径同样记账）。
  - **A11（medium，慢客户端被踢后只剩终态）**：`text_delta` 逐 chunk 下发 + `tool_progress` 逐 `input_json_delta` 下发 -> 单轮事件数轻易破千，而缓冲（500）与订阅队列（512）更小 -> 发布端快于消费端时**仍在线**的慢客户端被踢出订阅，之后只收到终态（流式正文与工具卡片全丢）。修法：增量事件按 **~50ms 窗口合并下发**（事件数与**产出速率**成正比而非 token 数；非增量事件前先冲刷以保序），缓冲与队列同步扩容到 2000 / 2048。
  - **A12（medium，文档承诺与实现不符）**：端点 docstring 与 API.md 承诺「从缓冲重放全部事件重建 UI」，而缓冲是 FIFO 有界 -> 单轮事件数超窗后早于窗口的事件**永久不可回放**，重连客户端重建的流缺失开头且无从察觉。修法：新增 `replay_truncated` 判定与事件（`Last-Event-ID` 早于缓冲起点时先发该事件），前端 `Chat.tsx` 收到后改为**拉全量消息重建**；API.md §11.3 如实写明重放窗口与该事件。
  - **A13（medium，工具调用数可超上限 70%）**：主循环每组前检查 `call_count >= max_tool_calls`，但反馈循环的重跑数是**事后**累加（`call_count += reran`），自身不做上限判定 -> 反馈循环最多 2 轮 × 最多 7 个工具，实际可发起 20+14=34 次调用。修法：`_rerun_from` 入口按「已用数 + 本组人数」判定硬闸，触顶即返回 interrupted（不再进 export）。
  - **A14（low，错误工具耗时恒 0）**：`duration_ms` 只在成功路径赋值 -> `tool_calls` / `run_steps` 中错误工具的耗时永远 0（用量页与步骤面板统计失真）。修法：`run_tool` 的取消/超时/异常路径统一回填耗时。
  - **A15（low，缓存读取成本漏记）**：`record_metadata_usage` 以 `tokens_in or tokens_out` 短路 -> Anthropic 完全命中提示缓存时（`input_tokens=0`，全部计入 `cache_read_input_tokens`）整轮不落 `api_usage` -> 缓存读取成本在日预算与用量页中不可见。修法：短路条件补 `cache_read` / `cache_write`（`agent_loop` 的轮次计量同口径）。
  - **A18（low，文档说返回 False、实现恒 True）**：`_wait_runs_settled` 的 docstring 写「超时返回 False 由调用方容忍」，实现只有一条 `return True` -> 调用方无从判断未收敛。修法：超时**如实返回 False**；`DELETE /threads/{id}` 未收敛即 **409 `thread_busy`**（可重试语义，不进入必然失败的删行流程）；无流残留（进程重启后的僵尸 running 行）仍视为已收敛 -> 不卡死删除。
  - **A19（low，工作区文件无上限）**：`workspace.write_file` / `read_file` 无字节上限（`read_file` 全文读入内存后又被 `json.dumps` 复制一份）-> 模型一次写 100MB 再读回，8 路并发即可占 GB 级内存；`list_files` 亦无条数上限。修法：写 5MB / 读 1MB / 列表 500 条上限（超限明确报错，不落 tmp）。
  - **A16（low，契约与实现不符）**：API.md §11.3 写 `message_start`「多步 run 每步一条」，而实现只在循环外发一次（`message_end` 却是每步一条）-> 起止事件不对称，第三方按文档做事件配对会错位。修法：**实现对齐文档**（`message_start` 移进循环，每步一条；契约冻结不改语义）。
  - **A17（low）与 A2 合并到 B9**：`awaiting_approval` 的 run 无取消入口（`cancel_run` 只受理注册表内的流，finalize 已 pop -> 409 `not_running`）-> 用户无法放弃该提议；且 `RunStatus.cancelled` 运行时永不写入。B9 与「前端审批卡片 + 过期收敛」一并落地。
  - 回归：新增 `tests/test_iter397_agent_hardening.py`（**16 条**：error 事件先于 done、provider 异常脱敏、审批待确认置位且不置取消信号、审批待确认时 agent_loop 正常收口、工作区搬移不收调用方会话、非广告工具被拒、白名单=广告集、增量合并（500 次 -> <=5 条且文本不丢）、终态前冲刷增量保序、重放截断判定、反馈重跑受上限硬闸、错误路径记耗时、纯 cache_read 落 api_usage、等待收敛超时返回 False、无流视为已收敛、工作区写/读体积上限）；同步修正 2 处既有测试（`test_workspace.py` 改走新会话断言——搬移在独立会话提交，原会话读快照看不到；`test_iter397_event_loop_offload.py` 的 `_FakeCtx` 补 `approval_pending` 字段）。
  - **门禁**：`ruff check app/ tests/` 全通过；**全量套件 1725 passed / 1 skipped / 0 failed**（基线 1709 + 本批新增 16 条；唯一 skip 仍为 `marker_single` 未安装的环境项），耗时 476.64s；前端 `tsc --noEmit` 与 `npm run build` 均通过（7.18s）。

- **产品级裁决（用户 2026-10-21 问卷定夺，三项均选「补齐实现」方向）**：① **A2 审批链路** -> **补前端审批卡片**（Chat 页渲染待审产物 + 批准/拒绝按钮 -> 调 `POST /api/runs/{id}/approve`），**后端同时补** `awaiting_approval` 的 `cancel_run` 受理入口与**过期收敛**（不再永久中间态）；② **A10 `runs.model` / `threads.model`** -> **贯通**：`get_provider()` 增 `model` 覆盖参数，客户端传的模型**真正生效**，并把实际模型回写 `runs.model`（消除「看起来可切、实际不切」与 `runs.model` / `api_usage.model` 不一致）；③ **T3 `target_kind="material"`** -> **从 schema enum 移除** `material`，工具收到时返回明确错误提示改提 `note`（消除「schema 承诺 -> approve 必 400 -> 状态回滚 -> 无限重试」死胡同）。

- **iter 397-B9（产品裁决落地：A10 模型贯通 + A2/A17 审批链路收敛 + A2 前端审批卡，✅ 完成，本条 commit）**：T3 已于 B6 落地（schema enum 移除 `material`），本批落地 A10 与 A2/A17。
  - **A10（high，「看起来可切、实际不切」）**：`get_provider(user_id, db)` **无 model 参数**——`RunIn.model` 只在 `start_run` 写进 `runs.model`，provider 仍按「档案 model > settings.anthropic_model」取模型 -> 客户端切模型**完全无效果**；且 `runs.model` 存的是请求里的名字（客户端未指定时为空串），与真实调用模型对不上（用量页/运行详情失真）。修法：① `get_provider(user_id, db=None, model=None)` 增显式覆盖参数，覆盖优先级 **显式 model > 档案 model > 插件 default_model > settings.anthropic_model**（凭证档案档与 .env 档同此优先级，空串不视为覆盖）；② `start_run` 传**生效模型**（请求 `model` > `thread.model`）而非仅请求值；③ `_run_chat` 构造 provider 后把**实际模型回写 `runs.model`**（回写失败仅告警，不阻断对话）；④ `run_start` 事件的 `model` 本就是实际模型（此前已对）。
  - **A2/A17（high，待审批状态无任何出口）**：`promote_artifact` 提议后 run 停在 `awaiting_approval`，而 `cancel_run` 只受理**仍在流注册表内**的 run（`_finalize_chat` 已 pop -> 恒 409 `not_running`）-> 用户既不能批准（前端此前根本没有 approve/cancel 调用）、也不能放弃、也不会过期（`RunStatus.cancelled` 运行时**从不写入**）：提议永久占位，run 行不落终态、产物目录永不清理。修法（三条出口）：① **取消**：`POST /api/runs/{id}/cancel` 受理 `awaiting_approval` -> `cancelled`，用 **CAS 抢占**（与 approve 同款「状态抢占」语义：并发 approve 已提交时本请求失配退出，**不会把已审批（done）的 run 改回 cancelled**）；② **过期**：维护轮新增 `expire_stale_approvals()`，按 `settings.awaiting_approval_ttl_seconds`（默认 86400 = 24h，0 = 禁用）把超时未处理的提议收敛为 `cancelled`，**判据取提议产生时刻**（最新 `approval` 步骤的 `created_at`）而非 run 起始时刻（长 run 的起始时刻远早于提议，用它会误杀刚提出的提议）；③ **批准**：前端新增审批卡片（见下）。收敛统计并入维护轮返回值 `awaiting_approval_expired`（运维可观测）。
  - **A2 前端（补齐实现）**：`api.ts` 新增 `listThreadRuns` / `getRun` / `listRunArtifacts` / `listRunSteps` / `approveRun` / `cancelRun` 与对应类型（此前前端**完全没有**这组调用）；`Chat.tsx` 增审批卡片：① `done` 事件读 `status`（此前 done 分支**不读** status）-> `awaiting_approval` 时拉步骤+产物渲染「待确认产出」卡片；② 卡片含**提升目标选择**（笔记/方案——后端两种都支持，此前 UI 无入口）、**批准**与**放弃**按钮；③ 切会话时经 `GET /threads/{id}/runs` **恢复**该会话的待审批提议（刷新页面/切走再切回后卡片仍应存在——提议是服务端状态，不是前端一次性提示）；④ 失败反馈按后端 detail 串映射为可读文案（`not_awaiting_approval` -> 「该提议已被处理或已过期」等）。
  - **契约同步**：`API.md` §11.2（cancel 受理待审批 + approve 的 target_kind）、§11.3（model 真正生效 + 实际模型回写）、§11.5 新增「审批提议的三个出口」小节；`.env.example` 增 `AWAITING_APPROVAL_TTL_SECONDS`。
  - 回归：新增 `tests/test_iter397_b9_approval_model.py`（**10 条**：覆盖档案模型、覆盖 .env 回退档、实际模型回写 `runs.model`、start_run 传生效模型、cancel 受理待审批、非待审批仍 409、cancel 后 approve 冲突、TTL 只收敛超时项、长 run 新提议不误杀、TTL=0 禁用、维护轮汇总计数）。
  - **门禁**：`ruff check app/ tests/` 全通过；**全量套件 1734 passed / 2 skipped / 0 failed**（基线 1725 + 本批 10 条；耗时 439.87s）；前端 `tsc --noEmit` 与 `npm run build` 均通过（3.04s）。

- **iter 397-B10（Go 侧 tutor 暴露面审计与首批加固，🚧 进行中）**：Reasonix 内核侧的 tutor 面（`internal/runtime/tutor/` + `internal/frontend/serve/tutor_*.go`）是 Deep-tutor 学习域在 Go 上的**第二实现**——Python v1 侧已逐轮加固的同类缺陷在 Go 侧大多仍在。两路只读子 agent 并行审计（HTTP 暴露面 T1~T17、数据操作面 K1~K20），主会话逐条复核原文并**实证**关键论断后落地首批 4 项。
  - **审计结论（两路共 37 条，去重后 24 类）**：Go 侧**无 SQL 注入**、落盘原子性合格、OOXML/笔记 zip 有解压炸弹防护、SSRF 拨号逐 IP 校验有效、重定向逐跳复检在材料路径成立、LLM 重试计量累加正确、幻灯片 XSS 清洗到位、锁顺序无死锁。缺陷集中在**文件存储替代数据库后丢失的两类保障**（计数型配额与事务隔离退化为「先查后写」）与**路径 containment / 输入上限整体缺失**。
  - **G1（已实证，high）：URL 路径参数路径穿越**。Go 1.22+ 的 `ServeMux` **逐段反转义**后再绑定通配符——`%2e%2e%2f` 以 `../` 到达处理器（探针 `dtgopath/main.go` 实测：`PathValue` = `../../plans/index`，`filepath.Join` 后逃出 store 根乃至盘符根）。而 tutor 的会话/方案/笔记/材料/知识库/分享 id **零校验**直入 `filepath.Join`：`DELETE /tutor/notes/%2e%2e%2f%2e%2e%2fplans%2findex` 可删 store 根外的 `plans/index.json`；`DELETE /tutor/kb/chats/{id}` 可删任意 `.json`；`GET` 侧成为「文件存在性预言机」。修法（两层）：① **store 层收敛**：新增 `safeSegment`（含分隔符 / 父级引用 / 盘符的标识符降级为不可越界的名字）+ `notePath`/`kbDocPath`/`sharePath` 三个「文件名唯一构造点」，会话/方案/材料/知识库/分享/用量路径全部经其构造；② **serve 层守卫**：`tutorPathGuard` 对 `/tutor/` 前缀请求拒绝含 `..` 段的路径（同时按 `\` 切分——Windows 下反斜杠亦是分隔符），挂在 `routes()` 与 Hub 挂载点（守卫属路由本身，不依赖某一种挂载方式）。
  - **G2（已实证，high）：备份导入无并发互斥 + 回滚不完整**。① Go 侧备份预检/导入**无任何互斥**（Python 侧有 `_RESTORE_LOCK`）-> 并发导入各自持一份全量文档（base64 信封 + 解码副本，峰值约 1GB/请求），且「会话数/笔记数」配额基于同一快照失效。修法：serve 层新增 `tutorBackupMu`（**非阻塞**获取，第二个请求 409 `tutor.backup_busy`），锁在**读取请求体之前**取（内存峰值在读+解析阶段，锁取得晚就约束不到）。② `restoreNotes` 写入的笔记**未登记进回滚清单** -> 后续步骤失败时笔记留在库里，而响应称「nothing was changed」——表达与实现不符。修法：笔记文件登记进 `created` 清单，失败时一并删除。
  - **G3（medium）：超限载荷在解码后才拒**。`backupReaderCap = 360MB` 的信封先整份读入内存再 base64 解码（解码副本另计 ~256MB）-> 超限载荷仍先吃满内存。修法：解码**前**按 `base64.StdEncoding.DecodedLen` 估算解码后体积，超 `tutor.BackupMaxBytes`（256MB，与导入路径同口径）即 413 `tutor.backup_too_large`；上限常量由 tutor 包导出，serve 层不另立口径。
  - **G4（low）：错误原文回显**（K8/T11）：`tutorRefuse` 的 default 分支落到 `writeErr` -> `http.Error(w, err.Error(), …)`，把**绝对路径**（含用户名与目录结构）回显给客户端；同包内 `badBody` 已定「不展示解析错误本身」，tutor 面未遵守。**已修**（见下「B11 批」的 T11/K8 条）。
  - **B11 批（同批落地，5 项）**：**T9 plan version 无上限** -> 导入侧钳制 `backupMaxPlanVersion=100`，读取侧 `GET /tutor/plans/{pid}/versions` 由「从当前版本号倒数回扫」改为**按目录枚举**（`Store.PlanVersions`）——原实现一次请求最多 2^31 次 syscall；**T7 导出未纳入互斥** -> `GET /tutor/backup/export` 与预检/导入共用同一把 `tutorBackupMu`（导出同样在内存里拼整份 store）；**T3 请求体无上限** -> `tutorPathGuard` 统一套 `http.MaxBytesReader`（默认 1MB；备份 360MB、notes/import 8MB、材料与 KB 上传 80MB、KB url 10MB）；**K13 逐跳 SSRF 复检是死代码** -> 在 `publicOnlyClient` 上设 `CheckRedirect`（逐跳 `requirePublicURL` + `verifyMaxHops` 上限），使「手写跳转循环」与注释承诺一致；**T11/K8 错误外泄** -> 新增 `tutorFail(w, err)`：内部错误只进 `slog.Error`（保留可观测性），对外统一 500 `tutor.internal_error`；`tutorRefuse` 的 default 分支与 7 处散落的 `writeErr` 全部改走它。
  - **B12 批（同批落地，配额与并发族）**：新增 `internal/runtime/tutor/quota.go`——`keyedLocks`（按键互斥 + 引用计数回收，避免锁表随资源数增长）、`Store.WithPlanLock`、`Store.SaveNoteGuarded`（**计数与写入同一临界区**，镜像 Python 侧 `quota_slot`）、`ErrNoteQuota`/`ErrSessionQuota`/`ErrBadLogInput`；上限常量收敛到一处（`maxSessions=200`、`maxMaterialsPerSession=20`、`maxPlansPerSession=50`、`maxLogMinutes=1440`、`maxLogNoteRunes=2000`、`noteQuota=500`）。此前**在线路径的配额形同虚设**：会话数上限只存在于备份导入路径，在线 `CreateSession` 无上限；笔记上限只存在于导入路径，在线 `SaveNote` 无上限。读改写丢更新（T8/K6）同时修掉：`AppendAttempt`（作答）、`AddLog`（打卡）、`RecordProgress`（进度）三处纳入方案级锁。
  - **已登记未修（下一批）**：T5 分享令牌无有效期、T12 GET 带副作用（导出触发 LLM 计费）、T13 列表无分页、T15 解析失败被吞、T16 无 panic 恢复、T17 CSRF 守卫只覆盖 POST、K3 KB 文档配额 TOCTOU、K9 PDF 无页数/累计解压预算、K10 progress nodeID 未校验、K12 删会话与在途 finalize 竞态致会话复活、K5/K6/K8/K11（KB/材料去重进锁、KB 命名统一与 `data/kb` 孤儿清理、解析并发槽与 whisper 模型单例、`_fresh_doc_id` TOCTOU）、K15 吞错、K19 KB 导出无总量上限。
  - **需产品裁决（尚未问用户）**：Go 侧 tutor 的**用户归属模型**（K1/T2：单 store 无 user 维度，暴露为网络服务时任一调用者可读写全部数据——与 Python v1 的 `get_owned_plan` 口径不同）、分享链接默认有效期与过期语义、GET 导出是否允许直接分享、在线配额取值、分页默认值、KB/备份导出体积上限。
  - **回归**：新增 `internal/runtime/tutor/store_pathsafety_test.go`（3 条：`safeSegment` 语义、路径构造器不出根、删笔记不触及目录外文件）、`internal/frontend/serve/tutor_path_guard_test.go`（4 条：三种穿越形态被拒且 store 外文件未被删、正常 id 不受影响、body cap 超限拒、超限前正常体不受影响）、`internal/runtime/tutor/backup_rollback_test.go`（2 条）、`internal/frontend/serve/tutor_backup_hardening_test.go`（3 条：第二个备份操作 409、锁在请求后释放、超限载荷 413）、`internal/runtime/tutor/quota_test.go`（6 条：笔记配额竞态只放行 1 个、会话配额竞态、标题/正文钳制、16 并发作答不丢、16 并发打卡不丢、minutes 非正被拒）。
- **iter 397-B13（Go 侧 KB/材料/进度/PDF 四个面，🚧 进行中）**：复核两路审计中归属 Go 侧的其余条目，逐条读原文后落地 6 项（另 2 项评估后不修、2 项转产品裁决）。
  - **K3 + K5/K6（high）：知识库的「去重 + 配额 + 写行」是三个分离步骤 -> 并发可破上限、可产生同哈希重复行**。`KbUpload`/`KbAddText`/`KbAddURL` 此前各自「先查后写」：两个并发上传同内容，**都**错过去重（库里两行同哈希，且各占一份配额）；两个并发上传不同内容，**都**通过文档数检查（上限按并发数被突破）。修法：新增 `kbCommitRow`——去重检查、文档数检查、写行收进**同一把键控锁**（`WithKbLock`）；去重检查排除**正在更新的文档自身**（`kbDedup` 增 `exceptID`——否则重解析时文档会找到自己，解析结果被当成重复丢弃，这是修法首版引入并由 `TestKbUploadConfirmParseFlow` 抓到的自伤）；重复或被拒时删除已写入的原件/解析文本，不留孤儿文件。
  - **材料配额（high，此前只有常量、无人执行）**：`maxMaterialsPerSession = 20` 与 `maxPlansPerSession = 50` 两个常量在 B10 之前**从未被任何代码引用**——在线 `AddMaterialText`/`AddMaterialFile`/`AddMaterialURL` 无任何数量上限，一个会话的资料目录可无限增长（材料数还会被方案生成整批读入，内存随之放大）。修法：新增 `AddMaterialGuarded`（**计数与写行同一临界区**）+ `ErrMaterialQuota`，三条材料写入路径统一走它；被拒时删除已写入的原件。
  - **K10（high）：进度行的 node_id 不校验 -> 无界增长**。`RecordProgress` 此前为**任意** node_id 建行，而总览（`ProgressOf`）只列方案自身的节点——写进去的行**没有任何视图会读**，但进度文件整份重写，于是每次请求都能给同一个文件加一行（节点 id 长度亦无上限）。修法：节点必须属于该方案（否则 404 `ErrNotFound`，与 Python 端「节点不存在则忽略」相比更严格——Go 侧无「忽略并返回 warning」的返回形状，硬造会与前端 `TutorProgressRow` 契约冲突）；node_id 长度上限 64（与 Python 端 `ProgressUpdate.node_id: max_length=64` 同口径）；进度行数上限 500。
  - **K9（high）：PDF 解析预算缺失**。① **页树嵌套深度无上限**——页树遍历是递归的，链式嵌套的 Pages 节点把 Go 的栈推到 1GB 上限后是**不可恢复的进程级致命错误**（一个请求即可带走整个服务）；② **页数无上限**——每页都要解释并保留文本；③ **解压预算只按单流计**（`zipMaxUncompressed` 512MB/流），文档的**流总和**不受限，一份带上千个流的文档是同一颗炸弹拆成上千份。修法：页数上限 2000、页树深度上限 64、**整份文档解压预算 256MB**（按流对象去重计费——字体的 ToUnicode 每页解码一次，逐次计费会把正常文档误杀）。
  - **T15（medium）：PDF 内容流解码失败被静默吞掉**。`pageText` 对解码失败的流 `continue`，页面文本因此**悄悄变短**；全部页面都失败时报的却是「文本层为空（扫描件？）」——把解码失败误报成另一种原因。修法：逐页统计失败流数并写 `slog`；无文本且有失败流时给出「内容流解码失败（N 个流）」的明确错误。
  - **serve 层拒绝映射补齐（medium）**：`tutor.note_quota` / `tutor.session_quota` / `tutor.material_quota`（429）、`tutor.bad_log_input` / `tutor.bad_progress_input`（400）。此前 B10 新增的**笔记/会话配额错误没有拒绝映射**，落进 default 分支被当成内部错误报 **500**——配额是「稍后再试」而非「服务坏了」。
  - **评估后不修**：**T17（CSRF 守卫只覆盖 POST）**——POST 是唯一会触发「简单请求」的变更方法，PUT/PATCH/DELETE 一律触发 CORS 预检，而该服务不答预检，跨站请求本就被浏览器拦下；把守卫扩到非 POST 只会误伤同源的 DELETE/PUT 调用。**T16（无 panic 恢复）**——`net/http` 的服务端已在**每条连接**上 recover 处理器 panic（记日志并关闭该连接），进程不会因此退出；真正的进程级风险是**goroutine 内的 panic**（不可 recover），已逐处核对 tutor 包的 `go func`（`parallel.go` / `pipeline.go` / `websearch.go`）——其函数体无 panic 源（索引写各自槽位，无共享状态竞争），且 `parallelFor` 的 worker 用 `wg.Go`（panic 即进程崩溃，但同样无 panic 源）。
  - **转产品裁决**：T5 分享令牌有效期、T13 列表分页默认值（涉及对外契约形状）。
  - **回归**：新增 `internal/runtime/tutor/b13_guards_test.go`（6 条：并发同内容上传只留一行、并发不同内容上传不破文档上限、并发材料上传不破每会话上限、被拒材料不留孤儿原件、未知节点不建行、超长 node_id 被拒）与 `internal/runtime/tutor/pdf_budget_test.go`（3 条：解压预算按流去重计费、页树深度超限被拒、页数超限被拒）。另统一测试用 store 构造入口（`openTestStore`）并加长 Windows「目录非空」清理重试（该 flake 源于原子写的 rename 与杀软句柄竞争，非代码回归）。
  - **门禁**：`go build ./internal/...` 通过；`go vet ./internal/runtime/tutor/ ./internal/frontend/serve/` 通过；`go test ./internal/runtime/tutor/ -count=1` **连跑 3 次全绿**（34.5s / 35.0s / 34.9s）；`go test ./internal/frontend/serve/ -count=1` **全量通过**（414.5s，含 `TestEveryRefusalCodeIsTranslated` 拒绝码一一对应门禁）。
- **iter 397-B14（用户四项裁决落地之一：配额取值 / 知识库导出上限 / 分享链接期限，✅ 完成）**：2026-10-21 用户经问卷定夺四项 Go 侧产品取舍，本批落地其中三项（第四项「引入 user 维度」见 B15）。
  - **配额放宽（取值由用户定）**：会话 200 -> **500**、笔记 500 -> **2000**、每会话材料 20 -> **50**、每会话方案 50 -> **100**、知识库文档 200 -> **500**。口径说明：tutor store 是**本地单用户空间**，这些上限的作用是约束「失控增长」而非分配多租户资源池，故取值高于 v1 的多用户配额。**还原侧上限同步放宽**（`backupMaxSessions` / `backupMaxMaterialsPerSession` / `backupMaxPlansPerSession`）——两侧口径必须一致，否则「导出 -> 导入」往返会静默丢数据。
  - **知识库导出总量上限（此前无上限）**：`KbExportZip` 此前无任何体积上限——库里有几百篇原件（每篇上限 50MB），一次全量导出可写出 GB 级 zip 落进**系统临时目录**（磁盘写满风险）。修法：新增 `KbExportMaxBytes = 256MB`（与备份导出的内联预算同口径）+ `ErrKbExportTooLarge`；体积**在建临时文件之前**按源字节结算（原件 + 解析文本），超限即拒 413 `tutor.kb_export_too_large`，文案提示用 `ids` 参数分批导出。
  - **分享链接期限（此前永久有效、无法撤销）**：新增 `Share.ExpiresAt` 与 `ShareDefaultTTL = 7 天`。① 新建链接默认 7 天；② 访问时校验，过期即失效**并删除该分享**（泄露的链接与从未存在不可区分）；③ 过期链接不再由创建接口原样返回（改发新链接），管理列表也不再列出；④ 新增 `ShareMakePermanent` 与 `POST /tutor/plans/{id}/share/permanent` 作为**显式**的「设为永久」；⑤ 分享响应与公开页携带到期时间（`expires_at`，永久态为 null），公开页显示「有效期至 X」/「长期有效」。
  - **回归**：新增 `internal/runtime/tutor/b14_policy_test.go`（6 条：默认期限到期即失效且被删除、永久态长期有效、过期后创建接口发新链接、导出超限被拒且未建临时文件、上限取值被钉住、导出上限不低于可用下限）与 `internal/frontend/serve/tutor_share_term_test.go`（1 条：响应带到期时间、设为永久后为 null）。
- **iter 397 文档收尾（✅ 完成）**：把 iter 396~397 的代码事实回写进领域笔记与契约文档，**逐条以实测为准**（不采信旧笔记里的数字）。
  - **规模计数全面重算**（此前 8 处过期）：OpenAPI **120 paths / 146 operations**（旧记 119/145）；pytest **188 个 `test_*.py`**、**收集 1736 用例**、`def test_` **1729** 个（旧记 177/178 个文件、1458/1462/1620 用例）；`backend/app` **31,311 行**（旧记 26,583）、`backend/tests` **201 个 .py / 45,055 行**、`frontend/src` **20,569 行**（.ts/.tsx 口径）。核对方式：`pytest --collect-only -q` 实测 1736、`create_app().openapi()` 实测 120/146、目录枚举逐文件累加行数。
  - **`AGENTS.md`**：仓库状态速览的测试计数改为「188 个 `test_*.py`（pytest 收集 1736 用例，iter 397-B9 实测 1734 passed / 2 skipped）」。
  - **`note/08-核心基础设施.md` §3 文件存储**：章节规模 331 → **518 行**，并补入三块此前未记载的内容——**上传原件的「先暂存、后改名」两段式助手**（`staging_dir` / `stage_upload` / `stage_text` / `promote_staged` / `promote_upload`，iter 397 K3：原件正式名需含行 id，而行 id 要等 INSERT 才有，「先建行再落盘」会留下「DB 有行但文件缺失」的持久状态）、**containment 助手族**（`data_root` / `strip_ext_prefix` / `is_within_root` / `relative_to_root` / `resolve_under_root`）、**`read_unit_content_guarded`**（iter 397 K14：单元内容路径存在 DB 里，DB 被篡改即退化为任意文件读）；同时把全文的行号引用刷新到当前实现。头部「模块行数实测」一并重算（`core/` 12 → 15 个文件）。
  - **`note/06-API全览.md`**：§4 横切守卫矩阵补入 **iter 397 的八项**（启动期开放模式告警 / 开放模式信任面回环守卫 / `.env` token 映射到管理员 / 用户列表分页 + 读限流 / 自重置口令返新 token / 删用户清工作区 / 登录限流桶收敛 / 审批链路与模型贯通）；§6.2 的 Go 侧差距表按 B10/B13 实况改写（资源配额从「无」改为「笔记 500 / 会话 200 / 每会话材料 20 / 每会话方案 50 / KB 200，计数与写入同临界区」；body 限额从「逐端点」改为「`tutorPathGuard` 通用中间件」；错误信封补 27 个哨兵分支与「内部错误不再回显原文」）。
  - **`API.md` §五 REST 代理**：补三条对插件作者有约束的勘误——开放模式下**信任面建立类端点仅限回环**（含具体端点清单）、`GET /api/users` 现分页（`limit` 默认 100 / 上限 500 / `offset`，插件遍历全部用户须按 `total` 分页）、自重置口令**返回新 token**（重置他人不返）。
  - **已核实无需改**：`note/06` 的「唯一认领管理员入口已被 E1 证伪」条目与 `API.md` §11.2/§11.3/§11.5 的审批端点、SSE 重放窗口勘误，B9 提交时已写入且与代码一致。
- **iter 397-B15（Go 侧 tutor 引入 user 维度，🚧 进行中）**：用户定夺「引入 user 维度：store 按用户分目录，鉴权后按用户过滤」。落点遵循 `plan/reasonix/04` 的分层铁律（**`internal/web` 之外任何内核包不出现 user 概念**，用户差异全部体现在装配参数——路径 root）：tutor 包**保持用户无感知**（它只认识一个 root），由 serve 层按请求解析出**状态空间（space）**并据此选 root 与 Service。
  - **落点与理由**：`plan/reasonix/04` §一/§三已把多用户感知点定在 `internal/web`（`auth` 持 JWT/PAT/首管理员，`pool` 持 per-user 的 Controller/tutor.Service），且明确 per-user 状态根为 `data/users/<uid>/kernel/…`。据此**不在 tutor 包里塞 user 概念**（否则违反铁律、且与 M2/M3 的装配层重复），而是把「用户」表达为**状态空间**：一个 space = 一个 store 根 = 一整套会话/方案/笔记/知识库/分享/用量。
  - **实现**：新增 `internal/frontend/serve/tutor_space.go`——`SetTutorSpaceResolver`（多用户前端按**已认证主体**命名空间；**明令不得取自请求头/参数**，否则等于让客户端选目录）、`SetTutorSpaceService`（宿主自行装配每个空间的 Service）、`tutorSpaceKey` / `tutorSpaceRoot` / `tutorSpaceSegment`（空间键收敛成不可越界的目录名，并带原键摘要——两个键不会归并到同一目录）。serve 层 `tutorService()` 改为 `tutorServiceFor(r)`（**79 处调用点**同步，6 个此前忽略请求参数的处理器改收 `r`），解析顺序：空间注册表 -> **Server 级覆盖（仅默认空间）** -> 按空间 root 共享的池。
  - **复核补正（自审发现）**：首版把 Server 级覆盖 `SetTutorService` 对**所有空间**一律返回——一个 Service 只对应一个 store，把它交给命名空间等于用**默认空间的数据**回答该调用方，隔离会「名义成立、实质失效」。已改为**只对默认空间生效**；命名空间一律按自身 root 自建（或用宿主注册的 per-space Service）。这是本批唯一的逻辑补正，已加回归断言。
  - **默认空间保持 store 原位**：默认空间直接用 `config.TutorDir()`，**既有安装的数据原地不动、无需迁移**；命名空间落在 `<root>/spaces/<segment>/`。
  - **当前仍无多用户身份源（如实登记）**：内核自身的 auth gate 只有**单一操作者**（token 或密码），**没有**多用户身份；因此本批交付的是**隔离机制与装配缝**（可测：两个空间互不可见），而「谁是哪个用户」这一身份源按 `plan/reasonix/04` 落在阶段① M2/M3 的 `internal/web/auth`。**在本批落地后，Go 侧 tutor 面仍是单用户本地空间**——把它当作多用户服务暴露之前必须先有 `internal/web/auth`。
  - **回归**：新增 `internal/frontend/serve/tutor_space_test.go`（**5 条**：alice 写入的数据 bob 看不见且**以 id 越权读取被拒**（404）、无空间键归默认空间、空间键收敛不可越界且不同键不归并、默认空间保持原位、**Server 级覆盖不外溢到命名空间**）。
- **iter 397-B16（Go 侧 tutor 会话生命周期锁，🚧 进行中）**：**K12「删会话与在途 finalize 竞态」**——自审发现并坐实的**数据操作缺陷**：删掉的会话会被仍在收尾的运行**写回来**。
  - **缺陷坐实**：tutor store 是**文件树**（无事务、无级联约束）。`Service.DeleteSession` 的注释写着「先取消在跑的运行再删文件，这样收尾器就写不回已删会话」——但**收尾器（`finalize`）的写盘发生在它自己那次「这个运行还是我的吗」检查之后**：删除若落在「检查通过」与「写盘」之间（两者之间隔着 2~4 次文件读写，Windows 上约 5~10ms），`finalize` 会把 `run.json` / `session.json` / 方案重新写进刚被删掉的目录——**用户删掉的会话连同方案一起回来**。同一类窗口还有第二条：运行登记（`occupyLocked` 写 `session.json`）与删除并发时，登记会把会话**重建**。两条窗口的根因相同：文件存储缺事务，而「删除」与「落盘」之间没有互斥。
  - **修法**：引入**会话生命周期锁**（`internal/runtime/tutor/quota.go` 的 `sessionKey` / `Store.LockSession`，复用既有 `keyedLocks` 机制）。删会话、登记运行、收尾落盘三条路径**共用一把锁**：`DeleteSession` 持锁期间移除运行登记、取消运行、删除文件；`finalize` 持锁后复查登记，查不到即**直接返回、不写任何盘**；`StartGenerate` / `Clarify` / `Retry` / `Regenerate` **先读会话再登记**的旧序改为**先取锁再读会话**（否则删除仍可落在读与写之间）。
  - **锁序铁律**：会话锁**一律先于服务互斥量 `s.mu`** 获取（`finalize` / `occupyLocked` 两条路径都按此序），反序即死锁——已在代码注释中写明。
  - **顺带闭合的并发缺口**：`StartGenerate` 的「已在跑就附着」判定与登记原本**不原子**（两个并发请求可同时通过检查、双双占位、重复计费）；纳入同一把会话锁后，检查与登记成为临界区内的整体。
  - **回归**：新增 `internal/runtime/tutor/sessionlock_test.go`（**4 条**：收尾落盘须等待会话锁（**锁在持有期间不得出终态事件**）、删除等待会话锁、运行登记等待会话锁、删掉的会话不会回来——会话/运行态/方案三者皆无残留）。**负向校验已做**：临时移除 `finalize` 的会话锁后，第一条立即失败（`the finalizer wrote while the session's lock was held: done`），确认是真回归钉子而非同义改写。
  - **过程登记（一处测试自身缺陷，非产品缺陷）**：首版「删掉的会话不会回来」用例里，轮询的截止时间在每次迭代被重置 → **死循环 + 高频 `CreateFile` 打死文件系统**（Windows 上表现为 `CreateFile` 长时间挂起）。已改为固定截止时间 + 20ms 间隔；诊断确认**产品行为本就正确**（删除后目录不再出现、收尾不写回）。
- **iter 397-B17（Go 侧 tutor 吞错显性化 + 可重试删除，🚧 进行中）**：回归连带坐实两件事——**B14 遗留的一条失效测试**，以及**一个真实的产品级脆弱点**。
  - **B14 遗留（`TestImportNotesQuota` 已测不到任何东西）**：B14 把笔记配额从 500 抬到 2000，但用例仍硬编码「18 批 × 30 条 = 540 条」——540 < 2000，**配额分支不再触发**，该用例自 B14 起一直是「假绿」（断言 `QuotaFull` 为真，实际拿到 `QuotaFull:false` 却因 `result.Imported != noteQuota` 之外的原因没炸——实测是 `imported=540 / quotaFull=false` 直接失败）。已改为**由 `noteQuota` 推导批次数**，配额再变也不会再次失效。**教训登记**：配额类常量的用例不得硬编码样本量，必须由常量推导。
  - **真实脆弱点（Windows 上删会话/删方案会直接失败）**：`os.RemoveAll` 在 Windows 上**不重试**——Go 的实现（`removeall_at.go`）遇到树内任一文件被**杀软/索引器/未关闭的读句柄**占住，立刻以「目录非空」失败，调用方拿到一棵删了一半的树和一个无法处理的错误。仓库里 `fileutil.Remove`（单文件）早就有这个退避重试，**树删除没有对等物**；tutor store 的 `DeleteSession`（删会话目录 + 方案目录）、`DeleteKbDoc`、`DeleteMaterial`、`DeleteNote`、`deleteShare`、备份临时目录清理全部是裸 `os.RemoveAll` / `os.Remove`。**这不是测试环境问题，是真实安装上会遇到的**（用户点删除 → 报「目录非空」）。
  - **修法**：新增 `internal/base/fileutil/removeall.go` 的 `RemoveAll`——与 `Remove` 同一套退避（12 次 / 约 1.5 秒），树删不掉就重试；tutor store 的全部删除路径改走 `fileutil.RemoveAll` / `fileutil.Remove`。新增 `removeall_test.go` 3 条（重试后成功 / 永久性错误照实上报 / 路径本就不存在算成功）。
  - **K15「吞错」显性化（20 处 `_ =`）**：逐处核对后分成两类——**清理类**（失败后主错误已返回，删不掉的只是孤儿文件）与**「成功但少做了事」类**（失败被完全吞掉，调用方与运维都看不见）。后者逐条改为 `slog.Warn`：`ClearAttempts`（旧答题记录没清掉会被算到新题上）、运行登记时的会话落盘、解析失败回写到行（材料/知识库）、用量记账三处（**没记上会让日预算少算**）、过期与孤儿分享清理。
  - **知识库导出改为自述缺档**：`kbio.go` 读不出的原件/解析文本此前**静默跳过**（原文注释写着「log-and-skip」，但代码里并没有 log）——压缩包看起来完整，据此还原会**静默丢文件**。现改为逐条记日志，并在压缩包内写入 `missing.md` 列明缺了什么。
  - **回归**：`internal/base/fileutil` 3 条新增；tutor 包全量回归中。
- **iter 397-B18（Go 侧笔记列表封顶，🚧 进行中）**：**列表响应体量未设上限**——稳定性缺口。
  - **缺口坐实**：笔记上限 2000 条、每条正文上限 20000 字符，而 `GET /tutor/notes` 的列表项**携带全部正文**——一次响应要序列化上百 MB（峰值内存约为数据量的两倍：store 解码一份 + JSON 编码一份）。在网络暴露面上这是**可被反复触发的内存耗尽杠杆**（认证通过即可打），在桌面端则是每次进笔记页都要搬一次全量。
  - **与 v1 对齐**：v1 的 `GET /api/notes` 早已在列表里把正文截断为 **300 字符预览**并带 `truncated` 标志（`_out(n, preview=True)`），全文只走 `GET /api/notes/{id}`。**Go 侧此前没有这条**——两套实现语义不一致，且 Go 侧是更重的那一套。
  - **修法**：Go 侧列表改为**预览投影**（`noteListItem`，正文截 300 字符 + `truncated` 标志），详情端点仍给全文；**数组形状不变**，桌面端只需在打开笔记时改从详情端点取全文。
  - **桌面端同步改**（`desktop/frontend-next/src/ui/tutor/Notes.tsx`）：此前 `open(n)` 直接用**列表项的正文**填编辑器——列表改为预览后若照旧，保存会把**截断的正文写回去、丢掉其余内容**（一次静默的数据损坏）。改为打开时 `await port.tutorNote(n.id)` 取全文。
  - **回归**：新增 `internal/frontend/serve/tutor_notes_preview_test.go` 2 条（长正文在列表里被截到 300 字符且 `truncated` 为真、详情仍是全文；短正文原样且不报截断）。
- **iter 397-B19（Go 侧笔记导出流式化，🚧 进行中）**：**v1 侧 iter 396-B2 的既定取舍未跟进到 Go 侧**。
  - **缺口坐实**：v1 的备份/笔记导出在 iter 396-B2 已按用户定夺改为**流式写出**（「峰值内存从整份数据集降到单个会话量级」），但 **Go 侧的 `GET /tutor/notes/export.zip` 仍是「整包 zip 在堆里成型 -> 一次性写出」**：2000 条笔记（每条正文上限 20000 字符）意味着上百 MB 的正文集，归档再叠一份缓冲——每个并发导出请求都按这个量级吃内存。
  - **修法**：新增 `tutor.ExportNotesZipTo(w io.Writer, notes, now)`（同源实现，`ExportNotesZip` 保留为内存入口以兼容既有调用与测试）；serve 层改为**落临时文件再流式回传**（`http.ServeContent`，自带 Content-Length 与 Range 支持），临时文件用 `fileutil.Remove` 清理。
  - **与 B18 的分工**：B18 封掉的是**列表响应**的每请求体量（JSON 编码缓冲，约数据量的一倍）；B19 封掉的是**导出**的每请求体量（zip 缓冲）。store 侧的常驻缓存（笔记集本身）不在本轮范围——它是数据本身，不是每请求的放大。
- **iter 397 门禁结果（B15~B19 合并后的最终树）**：`go build ./internal/...` 通过；`go vet ./internal/runtime/tutor/ ./internal/base/fileutil/ ./internal/frontend/serve/` 通过；`go test ./internal/base/fileutil/` **ok 2.138s**；`go test ./internal/runtime/tutor/` **ok 202.515s**；`go test ./internal/frontend/serve/` **ok 445.952s**。**分批提交**（B15 `bb31bca` / B16 `a519380` / B17 `d55a534` / B18+B19 `5bd06bf` / 文档收尾 `141f62c`）——每批的树按依赖顺序自洽（文件按批次切分，混合文件在对应批次以该批的状态入账）。
- **iter 397-B20（Go 侧 tutor 全面禁缓存，🚧 进行中）**：**T12「GET 导出副作用」与缓存面**。
  - **缺口坐实**：`/tutor/` 下面全是**单个用户的私密学习数据**（笔记、方案、材料、知识库、分享、用量），但此前只有**备份导出**与**分享端点**自带 `Cache-Control: no-store`；笔记列表、笔记 ZIP、知识库导出、方案导出等一律没有。中间缓存（反向代理、共享浏览器缓存）若存下这些响应并转手给下一个读取者，即为**数据泄露**——在「要暴露为网络服务」的目标下这是硬伤。
  - **修法**：`tutorPathGuard`（守卫挂在 `routes()` 与 Hub 挂载点，覆盖整个 `/tutor/` 命名空间）统一加 `Cache-Control: no-store`。处理器若要别的策略仍可自行覆盖（SSE 的 `no-cache` 在其后设置，照旧生效）。
  - **回归**：新增 `internal/frontend/serve/tutor_cache_test.go` 2 条（会话/笔记/方案/用量/知识库/分享六个列表端点与笔记 ZIP 导出均带 `no-store`）。
  - **过程登记（测试自身缺陷）**：首版导出用例直接 GET 一个空笔记集——导出对空集按设计拒绝（404），断言写的是状态码 404 时读 body 报错；已改为先建一条笔记再导出。
- **iter 397-K11（v1 侧文档解析并发闸 + faster-whisper 模型单例，✅ 完成）**：解析是全站最重的本地计算——marker（PDF + OCR，单次可达 600s）、faster-whisper（音视频转写，模型驻留内存）、tesseract（OCR）都吃满 CPU 与内存。两处缺口：**并发无上界**（N 个用户的 N 份解析互相拖慢到全部超时，且每路都驻留一份模型与中间产物，内存放大 N 倍）与 **whisper 模型每次新建**（`WhisperModel(...)` 每次解析都把权重重新载入内存，base 档数百 MB；并发解析则是 N 份模型同时驻留）。
  - **并发闸**：新增 `settings.max_concurrent_parses`（默认 2，0=不限）与 `concurrency.parse_slot()`（按事件循环缓存的进程级信号量），包住 `tools/parse_material._parse_file_dispatch` 的**内置与插件两条分支**。纯文本解析无成本、URL 抓取另有专门限流桶（`api/materials.py`），故不包。
  - **重入保护（自查补入，本批最关键的一条）**：信号量不可重入，而插件注册的解析器可经 `api.tools.call("parse_material")` **回调宿主解析**（API.md §8.4 明确允许插件组合核心工具）——重入同一信号量时，上界为 1 即**永久挂起**（外层持槽等内层、内层等外层）；上界为 N 时，N 路重入请求即可占满全部槽位，此后**全站解析请求排队卡死**（不是单请求慢，是整站解析面死）。修法：已持槽的**任务**再次取槽时不再排队（放行）。**判定用任务身份而非 ContextVar 深度**——ContextVar 会被子任务继承（`asyncio.gather` 拷贝父上下文），会把「父任务持槽时 gather 出来的兄弟解析」误判为重入而**绕过并发上界**（上界形同虚设）；任务身份不继承，判定精确，跨请求/跨用户的进程级上界不受影响。
  - **whisper 单例**：`_whisper_model()` 按（模型名 + **实现类 identity**）缓存。缓存键含实现类 identity 是必需的——依赖被替换（替身/热补丁）时旧实例不再复用，否则新实现**永远不生效**（本次实测踩到：注入的假模型从未被调用，缓存的旧模型直接返回非空转写）。转写另加**串行锁**（CTranslate2 模型非线程安全；并发转写只会互相拖慢，串行反而更快），惰性生成器的**迭代也在锁内**（锁外迭代会与另一路转写并发触碰同一模型实例）。依赖缺失优先于时长守卫报错的次序保持不变。
  - **回归**：新增 `tests/test_iter397_k11_parse_concurrency.py`（**8 条**：模型只构造一次 / 改模型名重建 / 改实现类重建 / 转写不重叠 / 并发上界生效 / 上界跨调用共享 / **嵌套解析不死锁**（5s 超时兜底）/ **兄弟解析不被误判为重入**）。另给 `tests/test_parse_material_media.py` 加 autouse 夹具（每例前后丢弃单例）。v1 全量 **1746 passed / 2 skipped**（iter397 门禁基线由 1734 抬至 1746）。
- **iter 397-K8（v1 侧清扫 data/kb 孤儿文件，✅ 完成）**：删文档的顺序是「先删行提交、再尽力清文件」（`api/kb.py` `delete_document`）——进程在两者之间被杀、或 Windows 文件锁让 `unlink` 失败（只记 WARNING），文件就永远留在磁盘上；原件可达 50MB，而 **`data/kb` 目录此前没有任何清扫**（运维只扫 `data/sessions/` 与 `data/runs/`），孤儿文件无上限地长。
  - **修法**：新增 `core.maintenance.cleanup_orphan_kb_files()`，纳入 `run_maintenance`（返回 dict 增 `orphan_kb_files_removed`；该 dict **未被任何 HTTP 端点暴露**，不构成契约变更）。
  - **判据按「宁可漏删、绝不误删」取值**：① **引用判据用尾部结构键** `<uid>/<originals|parsed>/<文件名>`，**不用绝对路径**——DB 里的路径来自 `storage._kb_root` 的 `Path.resolve()`（可能带 `\\?\` 前缀或 junction 解析后的形态），而运维扫描用的是 `settings.data_dir` 的原始形态，两者是同一文件的**不同字符串形态**；按绝对路径比对会把「仍被引用的原件」误判为孤儿删掉（**不可逆数据丢失**）。② **mtime 冷却窗**（`orphan_grace_seconds`，默认 24h）：KB 上传是「先落盘后建行」，50MB 写入窗口内文件没有行；备份还原同理。③ **删除前重查引用集**（同 `cleanup_orphan_session_dirs` 的即时重查），把误删窗口从「整轮扫描」缩到「一次查询往返」。
  - **回归**：新增 `tests/test_iter397_k8_kb_orphan.py`（**4 条**：行在则不删 / 行没则删 / 冷却窗内不删 / 无 kb 目录时 no-op / **路径形态不一致时仍被引用的文件不得被删** / 运维统计带 `orphan_kb_files_removed`）。
- **iter 397 门禁结果（K11/K8 后的最终树）**：v1 侧 `ruff check app/ tests/` **All checks passed**；`pytest tests/ -q` **1746 passed / 2 skipped**（591s）。**注**：`tests/test_api_stats_calendar.py::test_stats_calendar_requires_auth` 在上一轮报 error，本轮**未复现**——确认为「并发 pytest 撞共享测试库」的假阳性（同一时刻只跑一个 pytest 进程），非产品缺陷。**分批提交**：K11 `1c6b310` / K8 `c49c856` / 文档收尾 `9153fe5`。


- **下一步**：① 回到 Reasonix 阶段① M1 余项（M1.2 模块改名 / M1.3 多实例 spike / M1.4 品牌化与 `DEEPTUTOR.md` / M1.5 门禁基线重建 + CI / M1.6 `bin/deep-tutor.exe` 冒烟）；② Go 侧未修登记项复核（**T13 分页默认值**——已复核：KB 500 / 会话 500 / 方案 100 每会话 / 笔记 2000 / 搜索 10 / 复习队列 100 / 错题队列 100 / 用量 90 天，各列表均**已封顶**，仅 `tutorPlans` 与 `tutorUsage` 无显式 limit 参数但受配额与天数上限约束，**判定不修**；**K8 的「KB 命名统一」部分**——文件名的唯一事实源是 DB 行内的 `original_path`/`parsed_text_path`，改为行 id 命名需引入「暂存+改名」两段式（工程量与回归风险大于收益，现有命名不产生正确性缺陷，孤儿由本批清扫兜住），**判定不修**；**v1 侧分享链接是否跟进 7 天期限**——待另行裁决）；③ Go 侧 KbParse 缺**全局解析并发闸**（仅 per-doc 锁）——与 v1 侧 K11 同型问题，但 Go 侧解析是纯本地文本抽取（无 marker/whisper/tesseract 外部依赖，CPU 成本低一个量级），且 Go 侧 tutor 目前仍是**单用户本地空间**（无多用户身份源），暂按低优先级登记待 M2/M3 身份源落地后再评估。

**iter 396-B（2026-10-21，备份导入写锁时长与导出内存峰值改造轮，✅ 完成）**：承接 iter 396 的「B1/B2 产品级取舍待用户定夺」。用户经问卷定夺：**B1 = 文件落盘移出事务**（先写暂存区、事务内只改名；**不改「一次备份能带多少数据」的承诺**）、**B2 = 导出改流式写出**（峰值内存从整份数据集降到单个会话量级；**语义不变、不截断、不拒绝**）。

- **B1 备份导入：文件落盘移出写事务（本条 commit）**：还原全程持 SQLite 写锁，而单文件「建+写」成本（Windows ~2.7ms）是对应 DB 插入（~0.2ms）的十余倍——文件 IO 落在事务内会把写锁持有时长放大一个数量级（还原上限 10 万行 -> 可产生十万级文件），期间全服务写请求等满 busy_timeout(10s) 后 500。
  - **两段式**：① **预暂存（事务外，写锁未持有）**：按「载荷位置键」（`mat`/`plan`/`unit`/`kbparsed`/`kborig`）把内容写到 `data/.restore-staging/<uuid>/`（与目标同卷，rename 才可用）；② **改名（事务内）**：拿到新行 id 后 `os.replace` 到最终路径（纯元数据操作）。改名放在**事务内**而非 commit 之后——避免「DB 已提交但文件缺失」窗口；回滚仍由既有 `created_files` 补偿链兜底。
  - **对不上即回退**：暂存缺失/数量不符时**回退为事务内直写**（宁可多持锁片刻，绝不丢数据）；预暂存与还原两遍按同一套载荷位置键配对，配对不上不会静默错配。
  - **配套优化**：① 目录解析加缓存（`Path.resolve()` 在 Windows 上 ~0.46ms/次；实测 1250 个文件触发 2505 次 resolve、耗时 1.57s，**全部落在事务内**）；② 单元行改为**一次 flush 取全部 unit_id**（此前逐单元 flush = N 次数据库往返）。
  - **实测（探针 `dt_probe_b1_lock.py`，50 会话 × (5 资料 + 20 单元) + 1 知识库原件 = 1252 个落盘文件）**：写锁持有时长 **9.248s → 3.29s（-65%）**；剖析（`dt_probe_b1_prof.py`）显示事务内剩余最大项已转为 DB 插入本身。
  - **反直觉实测（`dt_probe_rename_bench.py`）**：并发 `os.replace` 在 NTFS 上**远慢于串行**（1252 次改名：1 线程 0.52s / 2 线程 1.67s / 4 线程 7.32s / 8 线程 8.31s——8 线程比串行慢 16 倍，元数据锁/过滤驱动争抢）-> 改名**保持串行**；并发只用于「新建 + 写内容」的预暂存阶段（实测并行有效）。
  - **回归**：新增 `tests/test_iter396_backup_b1_staging.py` 9 条（内容落盘全部早于首条 INSERT / 暂存同卷 / 改名后内容正确 / >500 行 .pN 拆分 / 回退直写不丢数据 / 失败导入补偿删除已改名文件 / 暂存目录不残留）。全量 **1613 passed / 1 skipped**。

- **B2 备份导出：改流式写出（本条 commit）**：`_MAX_EXPORT_BYTES`(256MB) 只约束 kb 原件内联，notes/messages/plans.content 等大字段不计入预算——重度用户单次导出可把 worker 撑爆。用户定夺：**流式写出**（峰值内存降到单个会话量级；**语义不变、不截断、不拒绝**）。
  - **实现**：`core/backup.write_export` 逐项写出（会话 / 对话 / 笔记 / 记忆 / 知识库文档逐个处理，处理完即 `expunge`——避免 SQLAlchemy identity map 累积整份数据集）；知识库原件**分块 base64**（块大小须为 3 的倍数，否则块间拼接会在中间产生填充位 -> 解码字节错位）；原件先分块读入内存缓冲（超 8MB 溢出到临时文件），**完整读成功后才写进导出**——中途读失败若直接写入会留下截断 base64，导入侧将还原出**损坏**原件（静默数据损坏），比「该原件没被备份」更糟。`build_export` 保留为兼容入口（同源实现，走内存缓冲）。
  - **实测（探针 `dt_probe_b2_mem.py`，A/B 对照：旧实现从 `git show HEAD` 载入独立模块）**：数据规模 40 会话 × 5 资料 × 50 万字 + 2 × 8MB 原件（导出 JSON 308.4MB）→ **峰值内存 232.9MB → 19.2MB（-92%）**，导出文件体积一致（308.4MB），且两份输出**逐键语义完全一致**（仅 `exported_at` 因导出时刻不同而不同）。
  - **探针抓到的真漏项**：首版流式实现漏写知识库文档的 `created_at` / `updated_at`（A/B 逐键比对暴露）——已修复并固化为回归断言。
  - **回归**：新增 `tests/test_iter396_backup_b2_streaming.py` 6 条（顶层键完整 / 知识库文档字段完整 / 两条路径语义一致 / 原件分块 base64 跨块边界精确还原 / 峰值内存与数据集规模解耦的自校准对照）。全量 **1619 passed / 1 skipped**（1620 收集）。
  - **配套健壮性**：改名改走 `storage.replace_with_retry`（Windows 杀软/索引器瞬时锁目标文件会让裸 `os.replace` 抛 PermissionError，一次瞬时锁原本会让整次导入失败）；导出端点在流式导出前先取好 `user.id`（导出逐项 `expunge` 后 user 已 detached）。

**iter 395（2026-10-21，v1 Python 后端未审面续审与并发/暴露面加固轮，✅ 完成）**：承接 iter 392~394 的用户指令（长时间高负载、多用户高频率使用下的稳定性；作为网络服务暴露时的代码安全性；持续进行）。本轮审计 iter 393/394「下一步」列出的未审面：`api/knowledge.py`、`api/settings.py`、`api/events.py`、`api/share.py`、`api/quizzes.py`（+ `api/search.py` 复核）。

- **审查方法**：一个 explore 子 agent 并行审计五模块（结构化输出：每条发现附位置/等级/证据强度，并列出「已核实干净」的点与依据行号），主会话逐条读原文复核，对关键结论写**实证探针**（并发 500、SSE 槽位泄漏、错题族查询成本），只修坐实或明确成立的问题；前端相关项由一个 worker 实施并回报，主会话复核 diff 后提交。

- **本轮修复（11 个 commit）**：

  | commit | 主题 | 关键内容 |
  |---|---|---|
  | `dc5e25b` | 用户偏好原子 upsert | `PUT /api/settings/prefs` 与 `/pricing` 此前「先查后插」：并发首写同一格时双方都读到「无行」-> 都 INSERT -> 后者撞 `uq_user_prefs_user_key` -> `IntegrityError` 未捕获 -> **500**（实测 8 并发：prefs 4 个 500、pricing 7 个 500）。改用数据库原生 upsert（单条 DML，冲突交数据库解决）；偏好写补 30/min 限流 |
  | `b61f2ae` | 事件流连接数上限 | `GET /api/events/stream` 此前**无上限**：每条连接持 100 槽队列 + 一个 bus sink（`_sinks` 无界列表），且每次 emit 遍历全部 sink（O(连接数)）-> 单客户端可开满连接耗内存/放大 CPU。补单用户 16 / 全局 256 条上限（超限 429） |
  | `0f38b37` | 错题族读限流 | `/quizzes/wrong-overview` 与 `/wrong-queue` 每次对本用户**全部**作答历史跑窗口函数，此前无限流 -> 补 30/min |
  | `1d9aadd` | iter395 回归 8 条 | 并发写面 500、事件流槽位、错题族限流 |
  | `09ff6a3` | 事件流加固五处 | ① 清理兜底改挂 `BackgroundTask`（客户端在响应体开始前断开时 body_iterator 从未被迭代，未启动的 async generator 的 finally 不执行 -> 槽位与 sink 永久泄漏）；② 归属解析与身份重验的同步 DB 读移出事件循环（池耗尽时 `pool.connect()` 阻塞最多 30s -> 全站冻结，同 iter 364/393 写路径失效模式）；③ 身份重验不再依赖队列空闲（此前只在 `queue.get()` 超时分支 -> 事件连发时已吊销用户的流持续收事件）；④ `bool` 不算 int（`user_id=True` 此前被判给 user 1）；⑤ payload 不可 JSON 序列化时降级 `default=str`（此前直接抛 TypeError 打断订阅者的流） |
  | `dd6f699` | 事件流重验时机修正 | 上一提交把重验改为纯截止时间驱动，导致 iter381 契约「吊销 -> 下个心跳断流」在首个心跳失效；现改为**队列空闲即重验 或 截止时间到期重验**（两者取一）。另修 `format_frame(owner=None)` 被误当「未解析」-> 退回同步查库（改用独立哨兵 `_OWNER_UNRESOLVED`） |
  | `cfc6346` | 知识节点状态筛选下推 | `GET /api/knowledge?status=...` 此前 status 不进 SQL（SQL 只按 q/plan_id 过滤并 `LIMIT`，取回后才在 Python 侧过滤）-> 节点数 > limit 的重度用户按状态筛选只见「最新 limit 个节点」里的匹配项（结果严重偏少甚至为空，chip 计数同样失真）。现 status 下推 SQL（limit 之前），`stats` 改为按全量匹配节点分组统计；顺带修节点笔记提交后 `db.refresh` 遇级联删除抛 `ObjectDeletedError` -> 500 |
  | `01ab5e1` | 作答记录保留策略（用户已决） | `quiz_attempts` 是全库增速最快的表（每次提交一行、此前永不清理）。运维每日轮**每题**保留最新 N 条（`quiz_attempt_keep_per_quiz`，默认 10），**分批删除**（每批 2000 行一事务，避免长时间持写锁把并发写打成 503）；**最新一条始终保留** -> 错题本判定逐字不变；受影响的只有「总作答次数/正确率」统计与备份体积。顺带 `/wrong-queue` 的 `limit` 改 `Query(ge=1, le=200)` |
  | `bf6ff88` | 计费表误清空拒绝 | `PUT /api/settings/pricing` 输入非空但清洗后为空（字段名写错/全为非法值）此前走「删除整张表」分支（与端点 docstring「空对象 -> 删除；否则清洗后 upsert」矛盾）-> **400 拒绝**并列出无效条目；显式空对象仍是「清空」；显式空条目 = 四项全 0。另：偏好 upsert 按 `engine.dialect.name` 分派 SQLite/PostgreSQL（此前绑定 SQLite 方言，与 `DATABASE_URL` 可切 Postgres 的承诺不符） |
  | `f0aa894` | 分享页与分享链接五处 | ① 分享页标题恒为「学习方案」（方案内容无顶层 title/topic，主题在 `content.intent.topic`）-> 改走 `_plan_topic(plan)`；② 分享页从不显示配套资源（前端渲染 stage 级 `resources`，真实资源在顶层 `content.resources`）-> 改渲染顶层；③ 过期链接并发轮换 -> 后到者拿到已失效 token（无条件覆盖）-> 改 CAS 条件更新 + 重读；④ 公开读限流此前只按 IP 分桶（反代后全站共用一个桶，30/min 即全站 429）-> 改「按链接 token 30/min + 按 IP 600/min」双桶；⑤ `GET /api/shares` 全量返回且无限流 -> 补 limit（100/500）与 30/min |
  | `c8830f0` | iter395 回归补充 19 条 | 状态筛选下推、stats 全量、事件流三项健壮性、计费表误清空、作答保留（含「最新一条判错必须留存」）、错题队列 limit 边界、分享专项 7 条 |
  | `3340109` | 文档同步 | note/04（事件流章节重写）、note/06（iter395 四处加固 + 状态筛选 + 保留策略）、note/08（维护轮次补 `prune_quiz_attempts` + 设置项） |

- **一项产品级决策（用户确认）**：作答记录（`quiz_attempts`）保留策略——**按题保留最近 N 次（默认 10）**，错题本判定（每题最新作答 `correct IS False`）逐字不变；「总作答次数 / 正确率」统计与备份体积只覆盖保留窗口。

- **被实证坐实/明确成立的关键点**：
  1. **并发写偏好的 500**：探针 `dt_probe_prefs.py`（8 并发）-> 修复前 prefs 4/8、pricing 7/8 返回 500（`UNIQUE constraint failed: user_prefs.user_id, user_prefs.key`）；修复后 8/8 200。
  2. **错题族查询成本随历史线性劣化**：探针 `dt_probe_quiz_cost.py`（1 用户 / 200 题 / **5 万条作答**）-> wrong-queue 93ms、wrong-overview 70ms、`quiz/stats` 120ms；`quiz_attempts` 只有 `quiz_id` 单列索引（窗口函数需额外排序）。保留策略把表体量封顶在「题目数 × N」，从根上缓解；复合索引 `(quiz_id, created_at, id)` 收益实测约 35%（wrong-queue 93ms -> 61ms），本轮未加（需 alembic 迁移，留作候选）。
  3. **事件流槽位泄漏属真实风险类**：探针 `dt_probe_sse_leak.py`（最小 ASGI harness，客户端提前断开）在我方实测**未复现**泄漏（Starlette 1.3 的 task-group 路径下生成器通常已启动，finally 执行）；但「未启动的 async generator 的 finally 不执行」是 Python 语义，泄漏窗口取决于取消时机（服务关停/中间件超时）。故按**防御性修复**落地 BackgroundTask 兜底 + 幂等清理，而非声称已复现。
  4. **知识节点状态筛选确实返回错误数据**：子 agent 读原文坐实（status 不在 SQL 内），并由本轮回归（600 节点 / 300 done -> 应返回 300）实测覆盖。

- **实测基线（本轮）**：全量套件 **1571 passed / 1 skipped**（381.12s，含 iter395 新增 27 条）；唯一 skip 为 `marker_single` 未安装（环境项，非回归）。`ruff check app/ tests/` 全通过；`go build ./...` 退出码 0（Go 侧未改动）；前端 `tsc --noEmit` 与 `vite build` 均通过（分享页改动）。

- **本轮发现但未修（后续 iter 候选，含不修理由）**：
  - **乐观并发只有秒级分辨率**（`updated_at` 用 SQLite `CURRENT_TIMESTAMP`，秒粒度）：同一秒内的双标签页保存会静默覆盖；波及笔记 / 节点笔记 / 用户偏好 / 计费表的 `expected_updated_at` 基线。彻底修法需加 `rev` 整数列或改用 Python 侧微秒时间戳（涉及 4 张表的写路径与既有基线格式），本轮未做（改动面大且属「已知限制」，notes.py 早已如实标注）。
  - **`quiz_attempts` 复合索引**（`(quiz_id, created_at, id)`）：实测约 35% 收益，需 alembic 迁移，暂缓。
  - **知识列表/导出每请求最多 500~2000 次磁盘读**（逐 plan_id `db.get` + `_load_plan_content` 回读大方案文件）：可改批量 `IN` 查询 + 文件读缓存，本轮未做。
  - **搜索的 LIKE 未转义**（`contains` 默认 `autoescape=False`）：`%`/`_` 会被当通配符，且前导 `%` 无法走索引；影响精确度与扫描成本，本轮未做。
  - **`has_note` 与「我的笔记」列表口径不一**（前者不排除空内容行）：正常写入路径会删空行，仅历史/导入数据可见，未做。
  - **`share` 公开读的 IP 桶在反代后仍是粗粒度**：本轮已按链接分桶解决主要误伤，但「可信代理头取真实 IP」需引入 trusted-proxies 配置，未做。
  - **正解除吊销后的既有 SSE 流**：重验周期仍为 15s（心跳口径），吊销到断流最多 15s 窗口。

- **下一步**：① 继续 v1 Python 后端未审面（`api/sessions.py`、`api/notes.py`、`api/memory.py`、`api/plans.py` 的生成/取消/落库链、`core/backup.py` 的导入链）；② 评估「乐观并发基线改微秒/rev」的迁移方案（跨 4 表，先出方案再实施）；③ 回到 Reasonix 阶段①（模块改名 M1.2 → 品牌化与 `DEEPTUTOR.md` M1.4 → 门禁基线重建与 CI M1.5 → `bin/deep-tutor.exe` 冒烟 M1.6）。

**iter 394（2026-10-21，v1 Python 后端配额竞态与网络暴露面收紧轮，✅ 完成）**：承接 iter 392/393 的用户指令（长时间高负载、多用户高频率使用下的稳定性；作为网络服务暴露时的代码安全性；持续进行）。本轮审计 iter 393「下一步」列出的首批未审面：`api/progress.py`、`api/credentials.py`、`api/providers.py`、`api/setup.py`。

- **审查方法**：一个 explore 子 agent 并行审计四模块，主会话逐条读原文复核并写实证探针；沿用 iter 392/393 口径——**先坐实再修**，修复后验证「回归可捕获性」（移除配额锁后并发用例 rc=1，确认测试真能捕获回归而非空断言）。

- **本轮修复（11 个 commit）**：

  | commit | 主题 | 关键内容 |
  |---|---|---|
  | `cea9c25` | progress 行删除竞态与 CAS 谓词 | 主键先取快照（行被并发删后访问过期 ORM 实例触发 `ObjectDeletedError`，实测复现 500）；CAS 谓词补 `status`（原只比 mastery，「status 变、mastery 不变」的并发更新不被察觉——乐观锁只锁半行；**当前最终状态不变**，mastery 是 `compute_mastery` 唯一依赖量，属完整性加固）；同 `(plan_id, sid)` 重复节点改取 id 最小一条（原 `.one_or_none()` 抛 `MultipleResultsFound` -> 500） |
  | `b189456` | providers 语义与计量 | `ProviderUpdate` 复用创建用 `_normalize_kind`（None→""）致显式 `kind:null` 命中判据 `if body.kind is not None` -> 静默重置协议；拆出 `_normalize_kind_update`（None→None）；掩码阈值 4→8（4~7 位密钥此前被完整回显）；连通性测试补日预算守卫（402，调用前判定）+ `api_usage` 计量 + `llm_task_slot` 背压（此前三者皆无：预算对真实计费调用失明、用量页不显示、可绕开信号量） |
  | `c26445c` | iter394 回归 9 条 | 连通性测试计量/预算/背压、kind 语义、掩码、progress CAS 三态 |
  | `74cab13` | 配额「先查后写」竞态闭合 | 新增 `app/core/quota.py`（`quota_slot` 分段线程锁 64 条 + `guarded_insert_async`/`guarded_commit_async`）；落地 8 个写入面（sessions/credentials/providers/notes/threads×2/学习日志/materials×2/kb×4）；**守卫必须含提交**（pysqlite 传统事务模式下未提交的 INSERT 其他连接不可见，只锁检查防不住） |
  | `d30109b` | `/setup/status` 按用户解析 | `llm_configured` 此前按实例级判断（甲配了 key，乙看到「已配置」）；`deps.resolve_user` 新增 `create_missing=False`（公开 GET 不建用户） |
  | `d3201de` | iter394 回归补充 | setup/status 按用户解析 + 配额并发护栏 |
  | `02fc9ca` | PAT 摘要存储 + 最小长度 8→16 | 明文入库 -> 库/备份泄露即令牌泄露；`sha256:` 摘要存储、读路径兼容旧明文行（双格式比较）、启动期一次性升级（**不在认证路径惰性升级**：认证在事件循环线程内，写库会触发 iter 393 的写锁冻结）；per-IP 30/min 失败限流挡不住多 IP 分布式爆破 -> 最小长度 16 |
  | `e03c491` | 开放模式 setup 端点仅回环可调用 | **决策一**（用户已决）：`_is_loopback_client` 只认直连对端 `request.client.host`，不信任 `X-Forwarded-For`（该头可伪造）；判定前置到 `resolve_user` 之前（避免远程调用触发「懒创建 admin」写副作用）；非回环 -> 403 |
  | `227369f` | 供应商 base_url SSRF 守卫 | **决策二**（用户已决）：新增 `app/core/urlguard.py`（私网/环回/链路本地/云元数据判定的**单一事实源**，verify_url.py 旧规则迁入、旧名保留以兼容既有测试与 monkeypatch）；写时校验 422 + 调用前复检（`test_provider` 全量、`get_provider` 仅字面量不触网）；存量档案调用期 RuntimeError + 启动期点名告警（日志剔除 URL userinfo）；开关 `ALLOW_PRIVATE_LLM_BASE_URL=1` 放行 |
  | `2ebbe1a` | iter394 决策落地回归 | 新增 4 条（存量私网档案调用期拒绝 / 开关放行 / 启动告警不误报公网 / 日志脱敏）；修正 iter393 `test_material_commit_failure_cleans_parsed_text`——材料落库改走 `guarded_commit_async` 后原 monkeypatch 已失效，**该回归曾静默失去护栏**，改为包装真实守卫函数、仅让 commit 阶段失败，覆盖「已落盘 -> 提交失败 -> 清理」全链 |
  | `2965629` | 配置与文档同步 | `.env.example`（新增 `ALLOW_PRIVATE_LLM_BASE_URL=0` + 开放模式 setup 回环说明）、`scripts/start.sh`/`start.ps1` 注释、`note/01` 规模订正、`note/06`（PAT 摘要契约 / 配额竞态闭合 / 两项暴露面收紧）、`note/08`（quota.py 与 urlguard.py 条目） |
  | （本条） | 进度记录 | 本条目 |

- **两项产品级决策（用户问卷确认，超时自动采纳推荐项）**：① 开放模式下 `POST /api/setup` 与 `POST /api/setup/dismiss` **仅回环客户端**可调用；② 供应商 `base_url` **默认拦截私网/环回/元数据**，`ALLOW_PRIVATE_LLM_BASE_URL=1` 放行。两项均已落地且有回归覆盖；若后续改口，调整面各仅一处（`setup.py::_setup_caller` / `settings.allow_private_llm_base_url`）。

- **被实证坐实的三条**：
  1. **配额 check-then-act 竞态**：25 个并发创建凭证请求 -> 实际写入 **25** 行（上限 20）。根因不止于「检查与写入之间无锁」：pysqlite 传统事务模式下**未提交的 INSERT 对其他连接不可见**，故只锁「检查」或只锁到 flush 都防不住——守卫必须含提交（实测：加锁后恰好 20 个 201 + 5 个 429）。
  2. **progress 行在并发窗口被级联删除 -> 500**：主键快照缺失，访问已删行的过期 ORM 实例触发 `ObjectDeletedError`（实测复现于 `progress.py:100`）。
  3. **开放模式 `/api/setup` 是「认领管理员」入口**：口令由调用方自填，局域网任意未认证客户端可抢先写入管理员口令（等于接管账号）；`/api/setup/dismiss` 可翻转全局 dismiss 状态。

- **实测基线（本轮）**：全量套件 **1544 passed / 1 skipped**（387.14s，含 iter394 新增 24 条）；唯一 skip 为 `marker_single` 未安装（环境项，非回归）。`ruff check app/ tests/` 全通过；`go build ./...` 退出码 0（Go 侧未改动）。

- **本轮发现但未修（后续 iter 候选，含不修理由）**：
  - **DNS rebinding（TOCTOU）**：出站校验与实际连接是两次独立 DNS 解析，TTL=0 的二次应答理论上可绕过；彻底修法需传输层 IP 钉扎（httpx 无 resolver 钩子，HTTPS SNI 下需自研 HTTP 栈），与本地优先威胁模型不成比例——与材料抓取路径同口径，iter 242 已评估接受。
  - **反向代理部署下开放模式 setup 不可达**：判定只认直连对端（不信任可伪造的 `X-Forwarded-For`），经代理时直连对端非回环 -> 开放模式 setup 403（须在 `.env` 配 `ADMIN_PASSWORD`/`AUTH_TOKEN` 走认证路径）。属既定取舍，已在 `.env.example` 与 `scripts/start.*` 说明。
  - **单进程部署前提**：配额守卫为进程内互斥（`scripts/start.*` 明确不加 `--workers`）；跨进程部署需 DB 级约束，当前不支持。
  - **CAS 谓词补 status 的实际影响**：当前最终状态不变（mastery 是 `compute_mastery` 唯一依赖量），属完整性加固，不宣称行为变化。
  - 承接 iter 393 候选：`_STREAMS` 终态 300s 保留窗口内存足迹（F10）、LLM 信号量在无运行 loop 时被替换（F12）、`api/deps.py` 与 `core/security.py` 的一次性引导写仍在事件循环内（仅首请求触发）。

- **下一步**：① 继续 v1 Python 后端未审面——`api/knowledge.py`（332 行，含 `node_notes` 写路径与知识手册导出）、`api/settings.py`、`api/events.py`、`api/share.py`、`api/quizzes.py`；重点看「表达与实现是否一致」、导出/文件写路径与配额面；② 复查 iter 394 新引入机制的边界：`urlguard` 的 DNS 缓存（TTL 5min）与写时校验的交互、`quota_slot` 分段锁的碰撞率、PAT 摘要升级的幂等性与并发启动；③ 回到 Reasonix 阶段①（模块改名 M1.2 → 品牌化与 `DEEPTUTOR.md` M1.4 → 门禁基线重建与 CI M1.5 → `bin/deep-tutor.exe` 冒烟 M1.6）。

**iter 393（2026-10-21，v1 Python 后端事件循环与网络暴露面硬化轮，✅ 完成）**：承接 iter 392 的用户指令（长时间高负载、多用户高频率使用下的稳定性；暴露为网络服务时的代码安全性；持续进行）。本轮仍在 v1 Python 后端面（当前可运行、即将以网络服务暴露的多用户服务）。

- **审查方法**：① 新写 AST 扫描器枚举「async 函数体内的同步 DB 写 / 阻塞文件 IO / subprocess」全量候选（临时工具，不入库），逐条读原文核实调用链；② 一个 explore 子 agent 并行审计 `api/exports.py`、`api/usage.py`、`plugins/api.py` 三个未审模块；③ 沿用 iter 392 口径——**先写实证探针复核再决定是否修复**（本轮新增坐实 2 条、复核后确认为「不需修」若干条）。

- **本轮修复（5 个 commit）**：

  | commit | 主题 | 关键内容 |
  |---|---|---|
  | `501620d` | async 端点内的同步 DB 写与文件 IO 移出事件循环 | 新增 `app/core/dbwrite.py`（`commit_async`/`flush_async`，专用写线程池 + `copy_context`）；materials/kb/notes/plans/memory 的 flush/commit 与解析文本落盘、会话目录递归删除全部移出事件循环；kb `_persist_parsed` 拆同步版/async 版；启动期 stale 收敛提交移出；关停回收写线程池 |
  | `9a8ff55` | 备份端点请求体无上限 → 有界 + 全局在途请求体预算 | 备份导入/预检不再豁免 body 上限（按路径 256MB+余量，解析前 413）；新增全局在途体预算 1GB（超限 503 + Retry-After，请求结束归还）；测试夹具每用例归零 |
  | `e80e56a` | 导出 `wrong_only` 显式拒绝 + 插件面一致性与 LLM 背压补齐 | `wrong_only` 守卫上移到格式分发之前（slides/ics 不再静默忽略）；插件 `delete_thread` 先取消在途 run；新增 `concurrency.plugin_llm_task_slot`（独立预算 + 有界等待，规避与宿主信号量嵌套死锁）；`recall_memories` 的 thread scope 契约对齐 `save_memory`；插件配置存储落盘失败回滚内存 |
  | `73fbbfc` | iter393 回归 21 条 | 事件循环可调度性（端点级 + helper 级）、写/落盘不在事件循环线程、body 上限三态、`wrong_only` 两态、插件面三态 |
  | （本条） | 进度记录 | 本条目 |

- **被实证坐实的两条（本轮核心）**：
  1. **写锁争用把整个事件循环冻结 11.0s**——探针 `dt_probe_busy4.py`：另开连接 `BEGIN IMMEDIATE` 持写锁后，SQLAlchemy 连接的 `db.commit()`（deferred BEGIN + INSERT）与裸 sqlite3 autocommit INSERT **均等待 11.0s** 才报 "database is locked"（`busy_timeout=10000ms` 确认生效，`PRAGMA busy_timeout` 读回 10000）。此前只移 commit 无效——**争用点在 flush/commit 触发的 DML 获取 WAL 写锁**，故 flush 与 commit 都需移出。修复前实测：持锁 0.8s 期间事件循环心跳仅 **2** 次（冻结）；修复后约 **40** 次（`git stash` 回到修复前代码复验，确认测试能捕获回归）。
  2. **未认证请求可把任意大的体写进系统临时目录**——探针 `dt_probe_backup_body.py`：未带 Authorization 的 64MB multipart POST `/api/backup/import`，响应 401，但 multipart **已落盘 64.0MB**。成因：FastAPI 先 `body = await request.form()`（`fastapi/routing.py:423`）再 `solve_dependencies` 跑鉴权（`:474`），而 Starlette 1.3 的 multipart 对**文件 part 无大小上限**（`max_part_size` 只管非文件字段），叠加 iter 371 对备份端点的**完全豁免**。修复后探针 `dt_probe_backup_body2.py`：**413 + 落盘峰值 0.00MB**。

- **实测基线（本轮）**：全量套件 **1520 passed / 1 skipped**（363.92s，含 iter393 新增 21 条）；唯一 skip 为 `marker_single` 未安装（环境项，非回归）。`ruff check app/ tests/` 全通过；`go build ./...` 退出码 0（Go 侧未改动）。

- **本轮发现但未修（后续 iter 候选，含不修理由）**：
  - 插件面 `api.llm.stream` 未包 LLM 背压槽——它是 provider 级 async generator，按 `app/core/concurrency.py` 既定约定槽只可包「跑完即返回的普通协程」（消费方提前退出未 `aclose` 会漏槽到 GC）；插件代码为运维方可信代码，风险等级低于宿主路径。
  - `api/usage.py` 的 `func.date(created_at, 'localtime')` 为 SQLite 专属函数（`db/base.py` 声明可切 Postgres）；窗口起点用「当前」UTC 偏移（跨 DST 日偏差 1 小时）；`created_at` 晚于今日的行因无上界会被 SQL 聚合但不进 `items`（图表不显示）。均为展示层偏差，不影响金额守恒。
  - 插件配额（500 thread / 2000 memory）为 check-then-act，并发下可略微越限（速率限制 30/min 约束了放大倍数；无 DB 级约束，改动需迁移）。
  - 插件 `api.llm.complete` 的 `get_provider` 仅捕获 `RuntimeError`（宿主端点捕获 `Exception`）——DB 异常会外泄为 500 而非 `no_llm_provider`；此处 500 语义更准确，故不改。
  - `api/deps.py` 的 `get_default_user` / `core/security.py` 的 `get_or_create_open_admin` 的一次性引导写仍在事件循环内（仅首请求/首次无管理员时触发，彼时库内无争用）。
  - 承接 iter 392 候选：`_STREAMS` 终态 300s 保留窗口内存足迹（F10）、LLM 信号量在无运行 loop 时被替换（F12）。

- **下一步**：① 继续 v1 Python 后端未审面（`api/progress.py`、`api/credentials.py`、`api/providers.py`、`api/setup.py`、`api/search.py`）——重点看配额 check-then-act、文件写路径与「表达与实现是否一致」；② 复查 iter 393 三处新引入机制的边界：写线程池在写锁风暴下的排队与连接池占用、body 预算在 chunked 路径的计入口径、插件 LLM 独立预算与宿主预算的叠加上限；③ 回到 Reasonix 阶段①（模块改名 M1.2 → 品牌化与 `DEEPTUTOR.md` M1.4 → 门禁基线重建与 CI M1.5 → `bin/deep-tutor.exe` 冒烟 M1.6）。

**iter 392（2026-10-21，v1 Python 后端长稳审查与修复轮，✅ 完成）**：用户指令「继续进行详细的检查，直到逻辑闭合，代码安全、稳定可长期运行；确保长时间高负载、多用户高频率使用下的稳定性；检查并修复所有逻辑错误；严格检查任何导致数据操作问题的 BUG；确保稳定性与代码安全性（需暴露为网络服务）；禁止随意停止，持续进行」。本轮**先审 v1 Python 后端**（当前可运行、即将以网络服务暴露的多用户服务），Go Reasonix 内核次之（阶段① M1.2 即将改名、M2 将新建 `internal/web` 承载多用户层）。

- **审查方法**：两个 explore 子 agent 并行审计（备份/恢复数据操作、并发与配额），主会话对每条发现先写**实证探针**复核再决定是否修复——推翻 2 条静态假设（见下「被实证推翻的假设」），避免修不存在的问题。

- **本轮修复（6 个 commit）**：

  | commit | 主题 | 关键内容 |
  |---|---|---|
  | `6d84d2b` | 备份导入导出硬化与写锁持有时长治理 | 总行数预算 10 万（超限 413，不做部分导入）、导出字节预算 256MB、preview/import 配额口径统一并按本机已有额度抵扣、材料三段制状态往返、重复 sid 首条胜出、下标重映射按 payload 下标、畸形 payload 400、整数钳制、时区归一、文件补偿登记册、not_backed_up 覆盖面披露、互斥锁前置到读文件之前、解析文本并发落盘（2000 文件 7.09s → 2.39s） |
  | `808867c` | 路径穿越守卫归一化 Windows 扩展长度前缀 | `storage._session_root`/`_kb_root`/`agent/workspace` 沙箱守卫/`kb` 原件回读/`export_plan`/`agent_tools` 产物登记统一走 `resolve_under_root`/`is_within_root`/`relative_to_root` |
  | `67a7dd0` | 生成流收口治理 | 孤立生成流回收（宽限期 30s + lifespan 15s 周期器）、finalize 持久化移出事件循环、事件跨线程投递（`call_soon_threadsafe` + 事件 id 分配加锁）、overlay run 收口并入 finalize、后台测验 inflight 标志 done_callback 兜底释放、关停等待 finalize 收口 |
  | `9058714` | 对话 run 生命周期 | 断连硬取消、二次取消强制收敛 run 终态 + 无条件 pop 注册表、收口 commit 移出事件循环、`max_running_chat_per_thread` 由死配置改为生效、`delete_user` 改总预算等待 |
  | `5c0f143` | 数据库写锁争用返回 503 + Retry-After | busy/locked 特征命中 -> 503 `server_busy` + `Retry-After: 1`；非争用类 OperationalError 仍 500 |
  | `954ef71` | iter392 回归 19 条 | 并发/收口/503/孤立流/配额/路径守卫/并发落盘全覆盖 |
  | `d6851ae` | finalize 在事件循环关闭时退化为同步收口 | 关停窗口内 create_task 抛 RuntimeError 会让该流永不落终态、订阅者挂死 |

- **被实证推翻的假设（未改代码，记录以免后续重复）**：
  1. **F3 / BUSY_SNAPSHOT 不成立**——本仓 SQLAlchemy 2.0 + pysqlite 为 `isolation_level=None`（DBAPI autocommit）：SELECT **不**开启读事务（`dbapi in_transaction=False`），SQLAlchemy 只在首个 DML 前发真 `BEGIN`（实测多语句写原子，rollback 可整批撤销）。故「读快照升级为写」在本仓配置下不会触发 `database is locked`；仓库既有的 `db.rollback()` 释放读快照约定（kb.py/materials.py）在此配置下为防御性措施。原判定的修复点（`_occupy_generation` 加 rollback）**未实施**。
  2. **`DELETE /api/threads/{id}`、`DELETE /api/users/{id}` 在 chat run 在途时无 FK/并发缺陷**——真实 ASGI 应用 + `httpx.ASGITransport` 探针复现：硬取消 + `_finalize_chat` 的 `run is None` 兜底已足够，`delete_thread` 路径安全，无需修复。

- **实测基线（本轮）**：全量套件 **1499 passed / 1 skipped**（388.04s，含 iter392 新增 19 条）；唯一 skip 为 `marker_single` 未安装（环境项，非回归）。中途一轮为 1479 passed / 2 skipped——多出的 1 项 skip 是 `test_verify_url.py` 的网络不可用跳过（环境相关）。`go build ./...` 退出码 0（Go 侧未改动）。

- **备份导入写锁持有多轮实测**（`data/sessions` 下 2000 个解析文本文件）：串行落盘 7.09s -> 并发落盘 2.39s；期间竞争写者等待 0.000s（此前写锁独占期内其他写请求等满 `busy_timeout=10s` 后 500）。**单事务语义未变**（原子性优先，不改批量提交），降级链改为：事件循环不被阻塞（finalize 移 worker 线程）+ 竞争写者得 503 可重试。

- **本轮发现但未修（后续 iter 候选）**：`_STREAMS` 终态后 300s 保留窗口的内存足迹（F10）；LLM 信号量在无运行 loop 时被替换（F12）；备份导入在行数预算上限（10 万行）下单事务持锁仍可达数十秒（DB 插入成本为主，文件 IO 已压缩）；日预算为 check-then-act 无预留，并发下实际花费可达上限的并发倍数（暴露面已被全局/单用户并发上限 + 每会话预算上限约束，非无界）；SSE 事件桥（`/api/events/stream`）与插件总线（`app/plugins/bus.py`）本轮复核**未发现缺陷**（有界队列 100、跨线程经 `call_soon_threadsafe`、finally 退订、心跳周期重验身份、按用户过滤 safe-by-default）。

- **下一步**：① 成本计量与预算绕过路径审计（`cost.py`/`budget.py`/`api_usage` 落账，`check_daily_budget` 只信 `api_usage` 求和）——**已复核**：chat 路径由 `agent/agent_loop.py` 每轮即时 `record_metadata_usage` 记账（取消/异常路径亦补记），无绕过通道；仅余并发下 check-then-act 的超额（已被全局/单用户并发上限 + 每会话预算上限约束）；② SSE 事件流生命周期与背压复核——**已复核未发现缺陷**；③ Go 侧 `internal/runtime/tutor` 存储/并发/数据操作审计——**已复核**：`Service` 占用表 `s.mu` + 运行态 `run.mu` 双锁、finalize 有「会话已删/被重新占用则不写回」守卫、store 写入全走 `fileutil.AtomicOverwriteFile`、列表读经 stat 校验缓存且返回副本、缓存有互斥保护；④ 文档偏差已在本轮校正（`note/01` 规模行 → 4,812 `.go` / 834,739 总行数、§7.1 `assembly` 343→337、`ext` 539→538、`backend/tests` 179 `.py`/1500 用例；M1.2 重命名范围 8,629 → 实测 8,431 处模块路径 import，README/plan/note 同步）；⑤ 后续候选：`_STREAMS` 终态 300s 保留窗口内存足迹（F10）、LLM 信号量无 loop 时被替换（F12）。

**iter R47（2026-10-20，历史细粒度拆分轮，✅ 完成）**：承接 iter R46——R46 的成果以 1 个 commit 提交 15 个文件、混装 2 个主题（SSE 口径复核 + 代词清扫），与仓库「细粒度 commit」约定不符，故将本会话 24 个 commit 整体重写为 **45 个主题单一 commit**（工作树内容零变化；加上本轮进度记录 commit，本会话区间最终为 46 个 commit）。

- **拆分口径**：1 个 commit = 1 项可独立回退的逻辑变更（代码修复 / 文档主题 / 披露登记 / 进度记录分列）。原 24 个 commit 中 12 个混装多主题，逐个拆分为：

| 原 commit | 拆为 | 拆分后主题 |
|---|---|---|
| `bec5525` | 2 | 清除 v1 期遗留草稿 `.claude/plan.md` ／ 清除上游无关资产树（release-notes·benchmarks·examples·workers·npm·output） |
| `3fa7748` | 4 | 死链修复 ／ `docs/kernel/README.md` 索引重写 ／ NOTICE 重写 ／ `.gitattributes`·`.gitignore` 失效规则清理 |
| `a3b7484` | 0 | `backend/.openapi_dump.json` 为生成物，原「先加后撤」两 commit 合并为不加，净树不变 |
| `49089a6` | 2 | 移除随资产清除而失效的上游件（cmd/e2ebench·cmd/signpath-contract·.signpath·prod_fast_test + repolint 条目）／ 权威文档重写 |
| `5b5dbda` | 2 | Go 门禁实况 ／ 上游文档本仓状态声明 + NOTICE 登记 |
| `731daef` | 2 | note/11 重基线 ／ note/01·02 标题层级修正 |
| `2f7cade` | 3 | 移除 7 个失效 effect 测试 ／ note/10 重基线 ／ note/07 复核修正 |
| `aa672b7` | 3 | note/00-INDEX 编码损坏修复 ／ 规模数字校准 ／ 去除修辞词 |
| `95035c4` | 3 | 代码路径常量修复 ／ 内核文档路径适配 ／ NOTICE 与笔记披露登记 |
| `cc30596` | 2 | 测试夹具路径修复 ／ 披露登记 |
| `a432948` | 3 | 修辞词清扫 ／ 归档计数修正 ／ 进度记录 |
| `497806d` | 3 | note/12 表数校准 ／ 方案族复核 ／ 进度记录 |
| `c75fa12` | 5 | note/10 SSE 口径复核 ／ 根文档与 NOTICE 代词清扫 ／ note/12 与方案族代词清扫 ／ 资产侧与 skill 文案清扫 ／ 进度记录 |

  其余 11 个原 commit 主题单一，原样保留（`b5d85b3`、`562e3c4`、`458b6d8`、`f07e6b9`、`76e0eda`、`146adcc`、`8b714d5`、`0496050`、`f36d9f1`、`ee91b15`、`cd13af0`）。

- **重放方式**：先建安全分支 `backup/pre-split`（旧 tip `c75fa12`），再 `git reset --hard` 回基线 `738b983`，按原顺序逐 commit 重放。涉及同一文件内多主题的 2 处（`note/00-INDEX` 的编码修复与规模校准、`stage.md` 的实测基线行与迭代条目）以中间态文件内容分列提交，避免合并冲突——冲突标记 `<<<<<<<` / `=======` / `>>>>>>>` 全仓复扫 0 处。

- **校验**：重写后 `HEAD^{tree}` = `63568f097e11cfcc336831f87d8006f520aa5daa`，与拆分前 `backup/pre-split^{tree}` 逐字节相同；`git diff backup/pre-split` 输出为空；`go build ./...` 与 `go vet ./...` 退出码 0；Python v1 **1461 passed / 1 skipped** 基线沿用 R45（工作树逐字节一致，结论不随历史重写变化）。

- **下一步**：同 iter R46——M1 剩余项（多实例 spike M1.3 → 模块重命名 M1.2 → 品牌化与 `DEEPTUTOR.md` M1.4 → 门禁基线重建与 CI M1.5 → `bin/deep-tutor.exe` 冒烟 M1.6）。

**iter R46（2026-10-20，SSE 口径复核与第一方文档代词清扫轮，✅ 完成）**：承接 iter R45——R45 收口表数与方案族，本轮处理两项遗留项：tutor 面 SSE 通道数口径、以及第一方文档的人称代词残留。

- **SSE 通道数复核（口径统一为「产品面 3 条」）**：全仓 Go 非测试代码中设置 `text/event-stream` 响应头的位置共 **4 处**，均在 `internal/frontend/serve`——`eventstream.go:276`（`GET /events` 控制器事件流）、`tutor.go:573`（`/tutor/...` 生成流）、`tutor_data.go:177`（`/tutor/plans/{pid}/materials` SSE 分支）、`browser_host.go:195`（`GET /browser-host/stream`，仅在 `opts.BrowserHost != nil` 时注册，`browser_host.go:180-183`，属 browser-host 窗口侧中继，不计入产品面）。其余命中项为客户端侧（`internal/ext/plugin/transport_http.go:150`、`transport_sse.go:111,126` 的 MCP SSE 传输；`internal/model/anthropic/anthropic.go:311`、`internal/model/openai/chat_headers.go:19` 的上游 LLM Accept 头）。据此修正 `note/10` §4.1 标题为「Go 侧产品面三条 SSE 通道」并补写出点全表、§5 对照表「3 条 SSE」→「产品面 3 条 SSE + browser-host 窗口中继 1 条（条件注册）」、`stage.md` 实测基线行同改；§4.3 原仅列 v1 三条通道，**漏 v1 材料流**，补 `POST /api/plans/{id}/materials`（`Accept: text/event-stream`，客户端 `frontend/src/lib/api.ts:319-345`，Go 侧 `tutor_data.go:155-210`），标题改为「与 v1 SSE 通道的对应（v1 四类 / Go 侧三条产品面）」。

- **第一方文档代词清扫（19 处）**：`README.md` 5（75/220/254/336/338）、`AGENTS.md` 3（7/8/9，「读它」→「读该文件」）、`NOTICE` 3（3/114）、`note/12` 2（29「复用它」→「复用 tutor.Service」；84「遵守其 MIT 协议」→「遵守 MIT 协议」）、`plan/reasonix/00` 1（91）、`plan/reasonix/01` 4（96/105/126/130）、`plan/reasonix/03` 1（8）。均为等义改写，不改决策实质与语义。

- **v1 资产侧文档与 skill 文案（6 处）**：`docs/archive/README.md` 17、`plugins/README.md` 61、`skills/deep-tutor-intent/SKILL.md` 33、`skills/deep-tutor-knowledge-map/SKILL.md` 44、`skills/deep-tutor-quiz/SKILL.md` 19、`skills/deep-tutor-resources/SKILL.md` 40（「进阶阶段配深入资料」→「配进阶资料」）。连同上一条合计 **25 处**（第一方活跃文档 19 + 资产侧 6）。

- **清扫边界（登记，不改）**：`docs/kernel/**`（上游原文，差异由 `NOTICE` 披露）、`docs/archive/plan-v1/**` 与 `chat.md`、`err.md`（历史归档，改写会失真）、`stage.md` Python 时代日志（append-only 进度史）、`API.md`（v1 冻结契约：正文为面向插件作者的第二人称语气，示例含 `system="你是温和的学习助教"`、`"拖我到处放"` 等**字面量字符串**，改写会变更示例语义；R45 已按「只改措辞不改语义」修正其「全面 CRUD」→「完整 CRUD」）、`skills/**` 与 `plugin-examples/**` 中的示例样本文本。代码内联 prompt（`backend/app/tools/build_knowledge_map.py:40` 等）与 Go 移植版 `internal/runtime/tutor/skills/*.md` 属功能性 prompt 文本，随 skills/ 文档既有差异保留（R45 起同例：skills/ 已改而代码内联串未同步）。

- **门禁**：全仓 Markdown 死链复扫 **0**；`文件:行号` 引用 **1 415 处**越界 **0**；第一方活跃文档（`README.md`、`AGENTS.md`、`NOTICE`、`note/00–12`、`plan/**`、`docs/archive/README.md`、`plugins/README.md`、`skills/deep-tutor-*`）**叙述性正文**人称代词复扫 **0**，修辞词复扫 **0**（「显著」按技术义「位置显著」保留）。保留项（属功能性文本，不计违规）：`skills/deep-tutor-intent/SKILL.md` 44/49/58 的澄清问句与输入示例、`skills/deep-tutor-quiz/SKILL.md` 64 的题目示例、`stage.md` 用户原话引用与 Python 时代日志、`API.md` 契约示例字面量、`docs/kernel/**` 上游原文、`docs/archive/plan-v1/**` 与 `chat.md`、`err.md` 历史归档。本轮零代码变更，`go build ./...` / `go vet ./...` 与 Python 1461 passed / 1 skipped 基线沿用 R45 结果。

- **下一步**：同 iter R45——M1 剩余项（多实例 spike M1.3 → 模块重命名 M1.2 → 品牌化与 `DEEPTUTOR.md` M1.4 → 门禁基线重建与 CI M1.5 → `bin/deep-tutor.exe` 冒烟 M1.6）。

**iter R45（2026-10-20，表数校准与方案族复核轮，✅ 完成）**：承接 iter R44——R44 核验了笔记侧清单，本轮把核验推进到**方案族与决策文本**，逐份扫描 `plan/reasonix/` 六份与 note/12 决策正文，修正 6 处过时引用。

- **表数校准（26 → 29）**：v1 schema 实测 **29 张 ORM 表**（`Base.metadata.tables`）。note/12 依据段与决策 #13 / #31 / #46 中的「26 表」是 2026-10-12 笔记快照时点值；决策实质（领域数据全 SQL、表结构自由设计、内核自身会话/记忆保留文件态）不变，故**不改决策正文**，在 note/12 修订记录新增「v1 表数校准（26 → 29）」条登记口径，并注明 `plan/reasonix/02` 的目标表清单为 25 行（依 #46 自由设计，与 v1 表数不一一对应）。

- **方案族复核**（R42 只更新了 00·01·05，本轮补齐 02·03·04）：`plan/reasonix/02` 语义基准「note/02（26 表）」→ **「29 张表」**；`plan/reasonix/00` 架构图「领域 26 表等价物」→ **「v1 领域表全集（29 张）语义等价物」**、R6 风险项同改；`plan/reasonix/04` §四 表头「全局态（REF 现状）」→ **「上游基线；吸收后为 `internal/` 同构实现」**，spike 方法的检索源由 `REF/` 改为在树内核代码（`internal/assembly/boot.Build`）——`REF/` 仅为本地对照副本，不入 git。

- **stage.md 自身校准**：R41 条目中的「SQLite 26 表」→「**29 表**」、`internal/runtime/tutor`「~60 文件」→「**67 文件**」。

- **门禁**：本轮零代码变更；全仓死链与行号引用越界复扫均为 0。

- **下一步**：同 iter R42——M1 剩余项（多实例 spike → 模块重命名 → 品牌化与 `DEEPTUTOR.md` → 门禁基线重建与 CI → `bin/deep-tutor.exe` 冒烟）。

**iter R44（2026-10-20，语义清单核验轮，✅ 完成）**：承接 iter R43——R43 统一了数字口径，本轮把核验从「数字与行号」推进到**清单实体本身**（表名 / 端点 / 命名空间 / 工具 / 版本），逐项与代码或实跑导出比对，修正 2 处结构表述。

- **端点清单**：note/06 §3 的 26 个模块计数与实跑 OpenAPI 逐前缀比对**全部一致**（`plans` 15+3、`memory` 3+13+7=23、`kb` 16、`progress` 11 等），合计 **145 operations / 119 paths**；`api/__init__.py:10-36` 注册区间与 `api_router = APIRouter(prefix="/api")`（第 10 行）一致。

- **插件门面**：note/07 的 15 个命名空间 + `api.threads` 别名与 `api.py:1354-1369` 装配块逐项一致（`self.threads = self.memory` 在 `api.py:1369`）；各命名空间引入版本与 `API.md` §十 版本历史逐项一致（tools 1.7/1.8/1.13、llm 1.5/1.6/1.14、mcp 1.9/1.12、webhooks 1.15/1.16、middleware 1.17、memory 1.19 等）；`api.py:59` 的 `API_VERSION = "1.20.0"` 与后端插件 API 版本一致。

- **工具面**：note/03 的 PIPELINE 8 工具与 `orchestrator.py:32-41` 一致，注册 9 个（`tools/__init__.py:28-36`，含不在 PIPELINE 的 `verify_url`）一致，`AGENT_TOOL_SCHEMAS` 12 个工具与 `agent_tools.py:493-545` 一致；note/09 的 9 个工具模块行数（650 / 180 / 169 / 80 / 856 / 363 / 131 / 81 / 182）逐项命中。

- **数据面**：note/02 的 29 张 ORM 表名与 `Base.metadata.tables` 实测逐一命中（缺失 0）；note/05 的 LLM 与计量模块行数（177 / 161 / 428 / 175 / 178 / 44 / 150 / 335 / 95）逐项命中，`__init__.py:126-149` 的协议分发区间与代码一致。

- **本轮修正**：note/09 §1 标题「流水线 8 工具」→ **「流水线 8 工具 + verify_url」**（该节实含 9 个小节，`verify_url` 独立注册且不在 PIPELINE）；note/06 的 `APIRouter` 实例数补注「**不含根 `api_router`**」（实测 27 处 `APIRouter(`，扣除 `api/__init__.py:10` 后为 26）。

- **常量与策略核验**（对照代码常量）：`CODE_STATUS` 26 个业务码与 note/06 §2 列举逐项一致（`errors.py:48-75`）；`_STATUS_TO_CODE` 14 项（`errors.py:124-139`）一致；5 个异常处理器注册于 `main.py:295-300` 一致；`_MAX_BODY_BYTES = max_upload_bytes + 5MB`（`main.py:27`，检查点 220/234）、`max_upload_mb` 默认 50（`settings.py:77`）、备份 256MB（`backup.py:34`）、kb 原件 32MB（`backup.py:50`）一致；`SlidingWindowLimiter` 60s 窗口 + key 上限 100 000（`ratelimit.py:18,27`）一致；原子写三函数（`storage.py:96-118`）一致；SQLite PRAGMA（WAL / synchronous=NORMAL / foreign_keys=ON / busy_timeout 默认 10 000ms，`base.py:41-58`）一致；导入配额 sessions 200 / materials 20 / plans 50 / notes 500（`backup.py:505-506`）一致；预算默认 会话 1.0 / 日 10.0（`settings.py:122-123`）与重生成上限 `plan.version > max_regenerations` → 429（`plans.py:622`）一致；工作区 scratch → committed → promoted 三段与 `workspace.py` 一致；`faster-whisper` 为可选运行时依赖（不在 `requirements.txt`，走 `dependency_missing` 降级），README 表述准确。

- **风格清扫（第一方文档）**：`API.md` §十一「全面 CRUD」→ **「完整 CRUD」**（冻结契约仅改措辞不改语义）；`skills/deep-tutor-resources/SKILL.md` 两处「真正」→ **「确实」**（语义等价）；`docs/archive/README.md` 的 err.md 条目「87 项」→ **91 项（89 已修复 + 2 不修 `[−]`）**（实测 `[x]` 89 + `[-]` 2 = 91，8 批次修复记录；note/11 原表述正确）。第一方文档（`API.md`、`plugins/`、`skills/`、`extension/`、`plugin-examples/`、`docs/archive/`、`.reasonix/commands/`）的人称代词与修辞词复扫命中 0。

- **门禁**：本轮零代码变更；全仓死链与行号引用越界复扫均为 0。

- **下一步**：同 iter R42——M1 剩余项（多实例 spike → 模块重命名 → 品牌化与 `DEEPTUTOR.md` → 门禁基线重建与 CI → `bin/deep-tutor.exe` 冒烟）。

**iter R43（2026-10-20，文档数字口径统一轮，✅ 完成）**：承接 iter R42 的复核——按实际代码逐项验证 R42 写入的数字，发现并修正**行数口径混用**：笔记头部声明「非空行」（`Get-Content | Measure-Object -Line`），而逐文件行数用的是总行数（`ReadAllLines`，等价 `wc -l`），同一份文档内两种口径并存。已统一为**总行数**口径（与文中行号引用同口径），并按该口径重算全部聚合数字。

- **口径统一**：note/00–11 统一声明「行数为总行数（`[System.IO.File]::ReadAllLines` 逐文件累加，等价 `wc -l`）」；note/00/01/02/03/04/05/06/07/09/10/11 逐份补口径行。

- **重算数字**：`backend/app` 22 614→**26 583**、`backend/tests` 29 438→**37 338**、`frontend/src`（.ts/.tsx）19 190→**20 250**（另 1 个 `.css` 206→**219** 行）、`internal/` 772 730→**834 739**、`plugins/` 8 个 `.py` 723→**891**（9 实例 945）、`extension/` 7 个代码文件 **343** 行（+README 27 行）。note/06 原口径说明「差异源于末行无换行符的计数方式」有误——实测差异来自**空行不计**，已改。

- **陈旧值修正**：note/03 与 note/09 的「pytest 收集 1,461 用例」→ **1,462**（`--collect-only` 实测）；note/11 资产链图「stage.md 133 KB」→ **140 KB**（实测 139.7 KB）；note/01 规模表补 `plugins` 945 行、`extension` 343 行两项口径。

- **抽查复核（全部与代码一致）**：逐文件行数 21 项（`plans.py` 2 899、`orchestrator.py` 421、`loop.py` 298、`registry.py` 75、`plugins/api.py` 1 379、`loader.py` 400、`bus.py` 226、`models.py` 879、`store.go` 695、`types.go` 254、`pipeline.go` 417、`tools.go` 531、`service.go` 663、`stream.go` 210、`tutor.go` 601、`tutor_data.go` 709、`plugins.ts` 1 461、`api.ts` 1 542、`taskEngine.ts` 495、`tasks.ts` 557、`App.tsx` 128）；目录聚合 6 项（pages 19 个 / 8 167 行、components 38 个 / 7 358 行、lib 10 个 / 3 833 行、store 2 个 / 645 行、widgets 1 个 / 56 行）；行号引用 5 项（`main.py:195` = `version="0.1.0"`、`api/__init__.py:10` = `prefix="/api"`、`go.mod:1-3` = `module reasonix` / `go 1.26.0`、`ci.yml` 全文 59 行、`protocol.go:22-29` = `ProtocolMajor = 2`）；插件命名空间引入版本 9 项与 `API.md` §八 逐项一致。

- **行号引用边界核验**：全仓 Markdown 的 `文件:行号` 引用 **1 357 处**逐条核验（同名文件按候选集合判定），越界 1 处——`docs/archive/err.md` 中 `health.py` 的第 24–51 行引用（该文件现为 49 行）改为第 24–49 行；复扫 **0**。

- **语义清单核验**（对照代码实体，非仅行号）：note/06 §3 端点表的 26 个模块计数与实跑 OpenAPI 逐前缀比对**全部一致**（`plans` 15+3、`memory` 3+13+7=23 等），合计 **145 operations / 119 paths**；note/07 的 15 个命名空间 + `api.threads` 别名与 `api.py:1354-1369` 装配块逐项一致（`threads` 别名在 `api.py:1369`），各命名空间引入版本与 `API.md` §十 版本历史逐项一致（tools 1.7/1.8/1.13、llm 1.5/1.6/1.14、mcp 1.9/1.12、webhooks 1.15/1.16、middleware 1.17、memory 1.19 等）；note/02 的 29 张 ORM 表名与 `Base.metadata.tables` 实测逐一命中（缺失 0）；note/06 的 `APIRouter` 实例数补注「不含根 `api_router`」（实测 27 处 `APIRouter(`，扣除 `api/__init__.py:10` 后为 26）。

- **门禁**：本轮零代码变更，文档改动不涉测试面；全仓死链复扫 **0**。

- **下一步**：同 iter R42——M1 剩余项（多实例 spike → 模块重命名 → 品牌化与 `DEEPTUTOR.md` → 门禁基线重建与 CI → `bin/deep-tutor.exe` 冒烟）。

**iter R42（2026-10-20，全仓文档治理轮：按实际代码重基线 + 上游无关资产清除 + 死链清零 + 门禁归因，✅ 完成）**：用户「请你根据实际代码，更新项目内所有文档，要求精准简洁，禁止使用修辞，禁止使用人称代词，专业、简洁技术化，并清除过时文档（清除前确保所有内容均在 git 跟踪，避免丢失）」。范围经用户问卷裁定两项：**note/01–11 全量重基线**（逐份重算数字与行号引用、结构描述，并补写 Go 侧对应章节）、**同时清理上游树中与本仓无关的资产**。本轮 18 个 commit（`b5d85b3`→`f36d9f1`），未 push。

- **文档重基线**：note/00-INDEX 重写（双栈形态 + 规模基线表）；note/01–11 逐份按当前代码重算（表数 26→29、端点 ~100→145 operations / 119 paths、路由模块 26→24、迁移 22→26、测试文件 ~105→177、pytest 收集 1462、前端 19 页 38 组件），各补 Go 侧对应章节（internal/ 12 子包 4 812 `.go` / 834 739 行、cmd/ 5 入口、tutor 8 步流水线与 v1 同构点、frontend-next 统一前端、扩展体系 v2）；note/12 补 2026-10-20 修订记录（#37 处置范围收窄 + 随被测对象移除的上游 effect 测试 + 路径常量修复登记）；README / AGENTS.md / NOTICE / plan/README / plan/reasonix/00·01·05 / docs/kernel/README / docs/archive/README 重写；根 `AGENTS.md` 新增 `## Reasonix host checks` 敏感路径清单（`go test ./tools/covergate/...` 由红转绿）。

- **上游无关资产清除**（commit `bec5525`）：删除 `release-notes/`、`benchmarks/`、`examples/`、`workers/`、`npm/`、`output/`，另删 `cmd/e2ebench`、`cmd/signpath-contract`、`.signpath/`、`prod_fast_test`；`.claude/plan.md`（v1 期 iter 34–36 遗留草稿）先 `git add -f` 归档入历史再删除（commit `562e3c4`），满足「清除前确保所有内容在 git 跟踪」。保留：`internal/`、`cmd/`、`tools/`、`sdk/`、`benchmark/`、`desktop/`、`scripts/reasonix/`、`docs/kernel/`（含 `upstream/` 原件存档）。

- **死链与路径常量**：全仓 Markdown 死链 98 处修复（84 处层级失配 + 10 处指向已删目录改为说明性表述 + 4 处文档内路径文本），复扫结果 0；`docs/` → `docs/kernel/`、`scripts/` → `scripts/reasonix/` 重定位造成的失效常量修复 14 个文件（`internal/contract/tool/contract_test.go`、`internal/assembly/boot/boot_test.go`、`internal/frontend/cli/{compact_ratio_test.go,upgrade_pointer_contract_test.go}`、`internal/frontend/serve/theme_author_example_test.go`、`internal/ext/extension/protocolgen/generate.go`、`internal/ext/hook/hook.go`、`tools/repolint/{docs.go,docs_test.go}`、`sdk/go/sdk.go`、`scripts/reasonix/official-theme-art/provenance.py`、`docs/kernel/{DOCS_STANDARD,EXTENSION_PROTOCOL,PLUGIN_PACKAGES}.md`），修复后 `internal/contract/tool`、`internal/ext/extension/protocolgen`、`internal/assembly/boot`、`internal/frontend/cli`、`internal/frontend/serve`、`tools/repolint` 六个包恢复绿；逐条披露见根 `NOTICE` 修改披露。

- **实测基线（v1 / v2）**：v1 = `backend/app/api` 28 文件（24 路由模块）、OpenAPI 119 paths / 145 operations、ORM 29 表、alembic 26 迁移、`backend/tests` 177 文件 / pytest 收集 1462、`backend/app` 26 583 行、`frontend/src` 20 250 行（73 ts/tsx + 1 css 219 行）、19 页 / 38 组件、plugins 9 实例、skills 8 目录、extension 7 代码文件 343 行；v2 = `internal/` 12 子包 / 4 812 `.go` / 834 739 行、`cmd/` 5 入口 / 21 `.go`、`internal/runtime/tutor` 67 `.go`（8 步流水线 + JSON 文件态存储）、`internal/frontend/serve` 仅挂 `/tutor/*`（78 端点 / 产品面 3 条 SSE，**无 `/api` shim**）。行数统一为总行数口径（`ReadAllLines` 逐文件累加，等价 `wc -l`）。

- **门禁结果与归因**：`go build ./...`、`go vet ./...` 退出码 0；`gofmt -l` 在本轮改动文件上无输出（仓库现存 1 处上游遗留格式偏差：`internal/platform/update/release_unit_test.go`，未改动）；Python v1 全量 **1461 passed / 1 skipped**（562.99s）。Go 侧：`go test ./...` 并行跑满工作树会因负载产生超时与用例级抖动（`internal/assembly/boot` 与 `internal/frontend/serve` 各约 605 秒被 Go 默认 600 秒超时截断），**逐包串行复跑**后的固定失败项共 6 处，全部为环境或工作树依赖，**非代码回归**：`internal/safety/sandbox` `TestIsWindowsWSLBash`（需本机存在 `C:\Windows\System32\bash.exe`）、`internal/base/testenv` `TestWindowsCILegsBypassTheTestResultCache`（要求 CI 含 Windows 上的根模块 `go test` 步骤，本仓 `ci.yml` 无 Go job）、`internal/ext/skill/builtincontent`（本机 AV 占用 `go-build` 临时 `.exe`，报 `Access is denied`）、`internal/runtime/agent` `TestChildConstructionForksStayEnumerated`（遍历工作树全部 `.go` 且未过滤 `REF/`，本地副本被判为未登记的子代理构造点）、`internal/runtime/schedrun` `TestKillingTheRunEndsItsWholeProcessTree`（Windows 子进程树取锁时序）、`internal/tools/builtin` `TestPipeStatusProbePreservesExitStatusAndReports`（Windows 管道退出码探针）；归因表落 `AGENTS.md` 门禁现状、`README.md` §8.3、`note/11` §D。另：`internal/assembly/boot` 单包本机耗时 974.9 秒，超 Go 默认超时上限，`go test ./...` 一次性执行必报 timeout panic（M1.5 需定分批与超时上限），该包单独执行（`-timeout 25m`）通过，`internal/frontend/serve` 单独执行亦通过。**本会话引入并已修复**：`examples/` 清除后 7 个失效 effect 测试（`internal/ext/skill/scenario_examples_test.go` 与 `internal/assembly/boot/effect_{bundled_skill_reference,installed_compatible_command,mcp_author_example,repo_map_example,review_context_example,work_note_prompt_example}_test.go`，每个文件仅含该 1 个用例，无其他断言丢失）与 14 个文件的重定位失效路径常量。`repolint` 仍红：报告 2429 行，源于 `tools/repolint/baseline.json` 仍是上游基线（含已删文件 23 条记录），且 repolint 会扫描 gitignore 的 `REF/`，属 M1.5 待办，本轮如实记录未掩盖。

- **下一步**：回 **M1 剩余项**——多实例 spike（M1.3）→ 模块/import 重命名 `reasonix`→`deep-tutor`（M1.2）→ 品牌化 + `DEEPTUTOR.md` 工程规约建档 + MIT 署名核对（M1.4）→ 门禁基线重建 + CI 接入 Go 门禁（M1.5）→ `make build` 产 `bin/deep-tutor.exe` 冒烟（M1.6）。

**iter R41（2026-10-20，全仓理解轮：note/00–12 + graft/STAGE + plan/reasonix 全通读 + 代码抽查对账，✅ 完成）**：用户「请你阅读并理解整个项目，要点在 note 文件夹」。**本轮零代码变更**，产出为「理解基线 + 现状对账」，把「笔记快照」与「代码现状」两条时间线对齐，供后续会话直接续接。

- **通读范围（15 份 note 全文 + 方案 + 代码抽查）**：note/00-INDEX（笔记目录与顶层架构速览）、01 架构总览、02 数据模型、03 生成流水线、04 Agent 对话与记忆系统、05 LLM 供应商与成本计量、06 API 全览、07 插件系统、08 核心基础设施、09 工具契约清单、10 前端架构、11 周边与工程化、12 改造决策（61 项宪法）、graft/STAGE（上游嫁接一手档案）；方案侧 plan/README + plan/reasonix/00·05 索引与四阶段任务分解；代码侧抽查 internal/runtime/tutor（pipeline/tools/service/store 等 60 文件）、internal/frontend/serve（tutor_* 系列）、cmd/、go.mod、Makefile、.github/workflows。

- **项目本质（两副身）**：**v1 = Python 全栈学习助手**（FastAPI + React SPA + SQLite 29 表 + 9 插件 + 端口 41173，单机单进程）。核心是「双执行模型」：`agent/orchestrator.py` 确定性 8 工具流水线（parse_material→understand_intent→build_knowledge_map→design_stages→{search_resources ∥ schedule_plan}→validate_plan→export_plan，反馈 ≤2 轮）产出学习方案；`agent/agent_loop.py` 原生 tool_use 迭代循环（≤25 步）承担 AI 对话。两者共享工具注册表与 ExecContext，但状态机/持久化/SSE 独立。贯穿性设计主线为**成本意识**（每次 LLM 调用即时落 api_usage，三层预算防线：会话/日/重生成次数）与**多用户隔离**（跨用户一律 404 防枚举）。**v2 = Reasonix 内核改造**（Go，模块名 `reasonix`），目标形态是「Reasonix 内核 + 多用户 SQL 域 + /api 兼容层」，四阶段递进，Python 侧保留至阶段① M5 验收后停用、阶段④删除。

- **对账结论（笔记 vs 代码现状）**：① note/01–11 是 **2026-10-12 时点分析快照**，与代码一致（16 流水线/领域工具 + 12 Agent 工具、29 表、15 插件命名空间、19 页 SPA 均在位），但**只描述 Python v1**，不含 Go 侧——AGENTS.md 已明确 note/01–11 定位为「领域语义基准」而非现状图。② Go 侧 `internal/runtime/tutor`（R-Tutor 移植，67 文件）**已逐条镜像 v1 语义**：pipeline.go 的 8 步/并行段/反馈目标/≤2 轮与 tools.go 的 `pipeline`/`parallelGroups`/`feedbackTargets` 与 note/03 完全同构（抽查 byte 级一致），store.go 为 JSON 文件态（阶段① M3.1 待 SQL 化）。③ **阶段①进度实测**：M1.1 吸收 ✅（8289 文件）；M1.2 模块改名 ❌（go.mod 仍 `module reasonix`，全仓 8629 处 `reasonix/` import 未改）；M1.3 多实例 spike ❌；M1.4 品牌化/DEEPTUTOR.md ❌（**根目录无 DEEPTUTOR.md**，而 AGENTS.md「内核引入后遵守 DEEPTUTOR.md 工程规约」已指向该文件——门禁规约实体待建）；M1.5 门禁基线+CI ❌（Makefile 仍为上游版，`build` 产 `bin/reasonix`；ci.yml 仍为上游单 Go job，Python v1 门禁另在历史 CI 中）；M1.6 `bin/deep-tutor.exe` ❌（cmd/ 下仍是 reasonix / reasonix-studio-host 等，无 cmd/deep-tutor）。④ **/api 兼容层尚不存在**：internal/frontend/serve 只挂 `/tutor/*`（tutor.go / tutor_data / tutor_export / tutor_kb / tutor_share / tutor_backup），全仓无 `/api` shim 路由 ⇒ 旧 SPA（frontend/src/pages 19 页在位）当前**无法**对接 Go 服务，M3.5 是 ①②阶段旧 SPA 回归的前置闸。⑤ 新前端已在库：desktop/frontend-next（1101 个 ts/tsx）——阶段④前端主体随吸收就绪，剩余是 19 页产品能力迁移 + 双语（#5/#8）。

- **门禁**：本轮零代码变更，工作区干净；未跑测试（无变更即无回归面）。仅做只读核查（Get-ChildItem/Select-String/读文件），未触碰 data/ 与 REF/。

- **下一步（顺序质量优先 #54）**：回 **M1 剩余项**——多实例 spike（M1.3）→ 模块/import 重命名 `reasonix`→`deep-tutor`（M1.2，独立 commit）→ 品牌化 + DEEPTUTOR.md 工程规约建档 + MIT 署名核对（M1.4）→ 门禁基线重建 + CI（M1.5）→ `make build` 产 `bin/deep-tutor.exe` 冒烟（M1.6）。注：M1.2 与 M1.4 触及决策 #42（品牌保留 Deep-tutor）与 #30（自由修改不跟上游、仓库内明写引用并遵守 MIT），实施前先对照 note/12。

**iter R40（2026-10-20，Python v1 深度审计第三十七轮「iter 397」，✅ 完成——审计覆盖闭合盘点）**：同一 /goal 第三十七遍——**全仓『文件 × R1-R39 已审计轮次』覆盖映射盘点 + 最后补漏精读**。零缺陷（补漏面全部干净），**滚动审计证据面宣告闭合**：

- **盘点方法**：全仓 88 后端 py（非 init）+ 68 前端 ts/tsx（非 vite-env）逐文件建立「stage.md 轮次日志提及 × note/ 领域笔记提及」矩阵（提及是精读的保守下界；矩阵假阴性只会高估未覆盖面，不漏真缺口）。
- **后端零 UNTOUCHED**：全部文件至少有轮次日志或领域笔记覆盖（note/ 01-11 为 2026-10-12 全项目系统分析）。8 个仅笔记提及文件本轮补核：sse_utils（SSE 无状态叶子，帧规范干净）、ratelimit（iter 246 锁原子性 + 惰性淘汰 + key 洪泛兜底，干净）本轮精读；generate_study_materials 为 R36 深改对象；anthropic_provider/plugin_provider（LLM 供应商层，结构快扫 + iter 380 异常注入六场景端点级钉死行为面）、migrate（note×7）、build_ics/build_plan_slides（export 端点测试覆盖）由行为级证据兜底。
- **前端 5 个 UNTOUCHED 本轮全部补读**：modalEsc（全局弹层栈基件——栈生命周期/active 切换/closeRef 防陈旧/splice 防错位，全对）、tabs（WAI-ARIA 键盘模式）、homeDashboard（4 widget 无数据自隐藏模式一致）、EmptyState/Skeleton（哑组件）——全部干净；PlanOutline（仅笔记提及）一并补读（parent_id 孤儿节点归根防丢失 ✓）。
- **行为级兜底面复核**：158 路由 × 授权矩阵两轮零漂移（R18/R33）+ 契约面两轮零漂移（R30/R39）+ 1461 测试全绿 + 四大真实进程演练（热管理风暴/泄漏巡检/40 分钟中窗/480 分钟过夜档 soak）+ 恢复演练互斥双方向——API 行为面的证据与文件精读面正交互补。
- **闭合结论**：R1-R40 后全仓每文件至少有「轮次精读 | 领域笔记分析+行为级测试覆盖 | 本轮补漏精读」三者之一；核心数据操作/安全面（backup/kb/materials/plans/users/credentials/plugins、storage 路径安全、ratelimit/locks/concurrency 基件、taskEngine/SSE 前端交互）均有轮次级深读 + 回归钉死。**Python v1 滚动审计证据面穷尽，后续转入 M1 剩余项**（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）；新变更随时可按既有门禁与脚本复验。
- **门禁**：本轮零代码变更；ruff 复跑绿；全量 1461 通过为 R38 门禁态且此后无代码变更（R39/R40 均零代码变更）。

**iter R39（2026-10-20，Python v1 深度审计第三十六轮「iter 396」，✅ 完成）**：同一 /goal 第三十六遍——**契约漂移复跑（R30 基线 × R36-R38 变更面）**。零漂移零缺陷（变更全部在实现层，不触契约形状），审计证据闭合：

- **双向契约零漂移**：contract_audit.py（158 路由）与 contract_frontend.py（61 接口/107 调用面）重提取后与 R30 基线 **byte-identical**（diff 0 行）——R36 的 plans.py 保位修复（`_insert_study_unit` order 参数化 + UnitPlan.orig_order）是内部实现层改动、R36-R38 前端修复全在组件内部（错误态按钮/重试逻辑），均不改 API 返回形状。
- **重点核对（order 字段 × 前端消费一致性）**：`GET /plans/{plan_id}/materials` 契约 top=[index_md, units]、units 元素嵌套键集 [done, estimated_time, id, objectives, **order**, prerequisites, stage_index, title]——order 稳定收录；R36 修复只改 order 的**值语义**（重生成单元写回原 order 保位），不改键集。前端 StudyUnitMeta.order 消费（阅读器重写后按 order 定位新行）与契约一致。
- **双向 diff 报告复跑**：重新生成的 contract_diff_report.txt 与 R30 版逐行一致（9 条既有分派项不变：AuthUser/AdminUser/SessionStatusDetail 等「遗漏」=提取器 AST 口径已知局限、quiz/wrong warning 与 pricing note 幽灵=R17 已分派、credentials 幽灵端点=动态拼接路径已知；无新增幽灵/遗漏条目）。
- **门禁**：本轮零代码变更（工作区干净，契约基线 json 按 R30 惯例 gitignore 本地留存）；ruff 复跑绿；全量 pytest 1461 通过为 R38 门禁态且此后无代码变更。
- **下一步**：滚动审计续（候选：回 M1 剩余——多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行；或 R40 大轮次里程碑盘点）。

**iter R38（2026-10-20，Python v1 深度审计第三十五轮「iter 395」，✅ 完成）**：同一 /goal 第三十五遍——**过夜档 soak（SOAK_MINUTES=480 真实进程 8 小时长窗全绿）+ 零散组件扫尾**：

- **过夜档 soak（tests/soak_overnight.py，R32 基建，480 分钟档）——小时级以上长窗证据面补齐，全部断言通过**：请求 9295 全 200（唯一 net_err=1 为 churn 流断开瞬间的预期网络错误，非 5xx）；SSE 常开双流各 1918 心跳、最大间隙 15s（断言 ≤45s）、churn 流 92 次重连 100% 成功；**RSS 131.0→136.2MB（8 小时 +5.2MB，断言 ≤48）**；句柄 336→329（-7 无泄漏）；asyncio 任务 25→19、OS 线程 3→3（无任务/线程累积）；notes 净零（CRUD 平衡）、api_usage 零增长；WAL 收尾 0.02MB（checkpoint 持续生效）。此前证据链：R32 40 分钟中窗 → 本轮 480 分钟过夜档，时间维度外推闭合。
- **零散组件扫尾（六组件精读，1 缺陷修复）**：① ProgressBoard——进度加载失败错误卡无重试路径（此前依赖 refreshToken 联动或重挂载），补 retryTick + 重试按钮（与 R31 Chat / R36 阅读器/材料弹窗口径统一）。其余五组件干净留档：HeaderSearch（防抖+abort iter 370/IME 组合态/`/` 劫持防护/combobox ARIA）、ShortcutsHelp（Portal 修复 iter 346 F3/`?` 劫持防护）、UsageMeter（失败静默不渲染，非关键 widget 可接受）、CheckinCalendar（月历数学/跨年归一/汇总口径全对）、FocusTimer（终点时间戳防漂移/暂停冻结/AudioContext 手势解锁静默降级/title 分层 iter 371/onComplete ref iter 365 E4/卸载资源全清）。
- **门禁**：ruff 全绿 + 全量 pytest 1461 通过/1 跳过 + 前端 tsc/build 绿（soak 独立数据目录不与门禁互扰）。
- **下一步**：滚动审计续（候选：契约漂移复跑；或回 M1 剩余——多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R37（2026-10-19，Python v1 深度审计第三十四轮「iter 394」，✅ 完成）**：同一 /goal 第三十四遍——**kb/知识库文档链专项（后端 kb.py 全链 + 前端 kb 族组件）**。深审计型轮次：链路经 iter 212-372 多轮打磨质量高，1 处交互缺陷修复 + 1 个行为级测试缺口补齐，全量 1461 门禁绿：

- **审计范围**：后端 kb.py（1095 行全读——upload 三段式 pending/confirmed/parsed 状态机、per-doc 解析互斥 + rowid 复用防护（iter 247）、原件读取 R20 路径越界防线、删除防解析中 409、孤儿清理（commit 失败/解析中被删 unlink）、问答快照自包含（citations 落 title 不落外链）、导出 spool 流式、generate 逐文档复制 + 异常回滚清目录）；前端 HomeKbGenerate / KbDocsView（796 行）/ KbViewerModal / （R36 已修的 KbChatPanel）。
- **干净面确认**：text/url 类文档 status 落模型默认 "parsed"（创建即已解析，语义正确——一度疑为错杀，查 models.py:269 server_default 后排除）；用户级联删除 kb_documents/kb_chat_records 行（User 侧 cascade）+ delete_kb_files 磁盘清理（users.py:190）既有测试双钉死；上传随机 id 预命名 + 撞名重摇（iter 228）；KbViewerModal blob revoke 闭包修复（iter 367 M12）/双弹层 Esc 栈；KbDocsView 上传泵单飞 + 在传取消（iter 368）/深链一次性消费/重命名 Enter+blur 防双触发。
- **缺陷修复（KbDocsView.toggleSelect）**：选择上限 toast 在 setState updater 内——StrictMode/并发渲染下 updater 可重放，副作用不保证恰好一次（与 R36 KbChatPanel.togglePick 同款反模式，全仓最后两处已清零）；校验移出 updater。
- **测试缺口补齐**：generate_from_documents 拒绝未解析文档（pending/confirmed → 404「尚未解析」引导）无行为级钉死 → 新增回归（含 404 无孤儿会话断言、confirmed 同拒）。
- **低危留档（不动）**：① 上传并发同内容双插窗口极短（读+写盘后 commit），影响=重复行多占一格配额，且「不加唯一约束」为既定设计决策（models 注释明示）；text/url 路径已有解析后重查双保险；② export ids 全非法时 wanted 空 → id==-1 → 404「知识库为空」文案略误导（合法前端不可达）；③ KbDocsView 列表 error 面无显式重试按钮（筛选/搜索切换是天然重试入口）；④ 上传行 key 含 index，取消行后 index 漂移致 DOM 重建（纯视觉）。
- **门禁**：ruff 全绿 + 全量 pytest **1461 通过**/1 跳过（+1）+ 前端 tsc/build 绿。
- **下一步**：滚动审计续（候选：过夜档 soak 择机（SOAK_MINUTES=480，小时级以上长窗证据仍缺）；或 HeaderSearch/ShortcutsHelp/UsageMeter/ProgressBoard 等零散组件扫尾；或 MaterialOut/kb 契约漂移复跑）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R36（2026-10-19，Python v1 深度审计第三十三轮「iter 393」，✅ 完成）**：同一 /goal 第三十三遍——**前端剩余小组件二轮抽检（MaterialsModal / KbChatPanel / TaskCenter / TaskPanel / StudyMaterialReader 组件级『表达与实现一致性』精读 + 阅读器重写链后端穿透）**。1 条功能链断裂修复（前后端各一）+ 5 处交互缺陷修复，全量 1460 门禁绿：

- **核心缺陷（阅读器内「重写本单元」链路 id 断裂，前后端）**：后端 unit_ids 重生成是**删旧行插新行**（plans.py，新 id；SSE 事件不携带 id；`_cleanup_stale_material_files` 注释自证 rowid 复用仅巧合）——前端重写完成后 `getStudyUnitContent(旧 id)` **必 404**（重写实际已成功并计费，界面却报「学习单元不存在」），且父级 units 列表仍指向已删行（点导航继续 404，新单元不可见直到重开弹窗）。修复：前端重写完成后重取单元列表→按 order 定位新行（rowid 复用巧合 id 未变时手动重取内容）+ 同步父级列表；后端配套保位修复（见下）。
- **后端 B1（重生成丢失 order → 同阶段单元错位）**：新行 order 一律用流内顺序号（子集内 0 起），`_resequence_study_units` 稳定排序后重写单元抢占同阶段兄弟位置——**测试先行实证**：同阶段 4 单元选「乙+丁」重生成，序列变为 甲、乙'、**丁'、丙**（丙被挤后）；单单元保位也依赖未定义的同序 tie-break。修复：UnitPlan 增 `orig_order`（默认 None 不影响全新生成/scope 整阶段路径），`_unit_plan_for_existing` 透传原 order，`_materials_sse` 插入写回——重生成单元确定性留在原位（前端按 order 定位的依赖）。
- **其余五处交互缺陷（R31 同族）**：① KbChatPanel 历史查询失败被空态掩盖——`useQuery` 未取 isError，失败渲染成新手引导空态（用户误以为历史全丢）；补错误横幅+重试+空态守卫（isError/isFetching）。② StudyMaterialReader 检索定位误中非正文 chrome——TreeWalker 从内容区根出发，工具条（A−/A+）、「目标：」行、参考资料同样命中（搜「A」标记字号按钮）；约束到 `.markdown-body`。③ 阅读器单元内容加载失败无重试（重进同一单元不重触发 effect）——contentRetry 计数器入加载 effect 依赖 + 重试按钮。④ MaterialsModal 列表加载失败无恢复动作（非任务错误只有横幅，须关窗重开）——提取 loadUnits 回调 + 「重试加载」按钮（与任务错误的「重试生成」分治）。⑤ KbChatPanel togglePick 上限 toast 在 setState updater 内——StrictMode/并发渲染下 updater 可重放，副作用不保证恰好一次；校验移出 updater。
- **回归**：+2 测试（同阶段多选/单选重生成保位，test_api_unit_regen.py，修复前实证失败→修复后绿）；门禁 ruff 全绿 + 全量 pytest **1460 通过**/1 跳过（+2）+ 前端 tsc/build 绿。
- **审计干净面留档**：TaskCenter/TaskPanel（台账排序/进度口径/取消重试分诊/start_rejected 走 startPlan/标题角标分层）与 MaterialsModal 既有面（iter 363 phase 合并 effect、iter 368 弹层栈、深度切换确认、导出三选一、staleRunning 刷新即真相）全部成立；阅读器滚动记忆/检索失败分治（iter 368）/重写面板复位收口（iter 371）亦成立。
- **下一步**：滚动审计续（候选：kb/知识库文档链专项；或过夜档 soak 择机（SOAK_MINUTES=480）；或 HomeKbGenerate/KbDocsView/KbViewerModal 等 kb 族组件随 kb 专项同轮）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R35（2026-10-19，Python v1 深度审计第三十二轮「iter 392」，✅ 完成）**：同一 /goal 第三十二遍——**备份/恢复链真实进程演练（R26 静态结论的真实 uvicorn × 并发流量复验）**。零产品缺陷（R26 全部结论实证成立），演练脚本入库，连续两轮全绿：

- **演练（tests/soak_restore_drill.py，手工运行不自动收集）**：真实 uvicorn × u1 种子（150 篇 24KB 笔记直插 + API 全链路建会话/材料含文件落盘）× 4.9→3.6MB 备份导出/预检 × u2 并发流量（读 300ms 间隔 + 笔记 CRUD churn 40 写/min）× restore 窗口内**双导入竞态**（+10ms 第二发）× SSE 看护 × 进程外只读核对。
- **R26 结论逐项实证**：① `_RESTORE_LOCK` 互斥——并发导入一胜一 **409**（『已有备份导入正在进行』信封），**两个竞态方向均捕获**（跨轮次 first/second 各赢过一次——胜者身份=先完成解析触达互斥者，断言对称化）；② restore 全程 to_thread **事件循环不冻**——875~1344ms 导入窗口内 /api/health 最大延迟 **16ms**、全程零 5xx、SSE 流不断；③ append-only——种子 150 篇原样 + 导入全量重建（笔记 300、150 个标题恰好 ×2、会话 1→2、材料 1→2 且 parsed_text 文件在新会话目录落盘=**DB↔文件系统一致**）；④ 他用户零扰动——u2 churn 净存精确对账；⑤ `PRAGMA integrity_check`=ok；⑥ 附带实证：导入配额面（早期 200×24KB 轮双导入串行时撞 500 笔记上限，超限部分 skipped 不硬失败——产品按设计工作）。
- **测试脚本自身缺陷（三轮修复）**：MaterialOut 字段名 material_id（非 id）；parsed_text_path 为绝对路径（列名 source 是可读摘要非路径）；双导入竞态胜者身份不定——断言改对称口径（[200,409] / [200,200] 两分支，配额核算 150×3=450<500 保证双分支确定性）。
- **门禁**：全量 pytest 1458 通过/1 跳过（本轮无新增自动测试——演练为手工脚本，连续两轮全绿实证）+ ruff 全绿。
- **下一步**：滚动审计续（候选：剩余小组件二轮抽检（MaterialsModal/KbChatPanel/TaskCenter）；或过夜档 soak 择机（SOAK_MINUTES=480）；或 kb/知识库文档链专项）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R34（2026-10-19，Python v1 深度审计第三十一轮「iter 391」，✅ 完成）**：同一 /goal 第三十一遍——**前端交互面二轮深读抽检（R31 未精读的五页：Home/Plans/Review/Wiki/Learn 组件级『表达与实现一致性』）**。1 处交互缺陷修复，tsc+build 与全量 1458 门禁绿：

- **深读矩阵（五页全部过目，分派结论）**：Home——草稿持久化（rawText+urls，时间字段不入草稿=接受：注释明示草稿范围为描述与链接、结构化字段重填成本低）/prefill 取代陈旧草稿/上传池 3 路有界并发+firstErr 停派发/失败清理孤儿会话/卸载中止在传上传（iter 368）/SSE 启动异常码分诊（409/503/402 toast+出口）；Plans——占位行状态机（running/clarify/interrupted/error/idle 五态徽标+活跃 5s 条件轮询）/批量删 session 去重+顺序 await+失败计数/选择作用域翻页清空/错误-空态分治（iter 369 B16）/删除级联缓存失效；Review——翻卡诚实回忆（未翻面先翻面）/回写失败不跳卡/判分锁+键盘流+弹层守卫/离页失效快照（iter 367 M5）/重练薄弱多轮收窄；Wiki——单飞状态循环/深链强制 docs 视图/三视图错误态齐备（iter 369 B16）/热力图当日明细按需拉取；Learn——打卡 1-1440 校验+未来日期拦截/补卡成功复位今天/D4 操作错误与整页错误分离/D6 切换防覆写/日志失败可见不阻塞（iter 368）。
- **缺陷 H1（Home 每日时长超界静默丢弃·表达不一致）**：手输超界值（如 5000）被 timeOpts 构造的过滤条件静默丢弃——界面上值仍在、提交无报错，实际生成走 LLM 猜测时长（用户以为已生效）。HTML min/max 只约束步进器不拦手输。修复：start() 入口显式校验报错（「每日时长须在 1-1440 分钟之间」，与 Learn 打卡同口径），内层过滤条件移除（单一事实源）。
- **回归**：`npm run build`（tsc --noEmit + vite build）绿；后端未动但按流程全量 pytest 1458 通过/1 跳过。
- **下一步**：滚动审计续（候选：备份/恢复链真实进程演练复验 R26；或剩余小组件二轮抽检（MaterialsModal/KbChatPanel/TaskCenter 等）；或过夜档 soak 择机）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R33（2026-10-19，Python v1 深度审计第三十轮「iter 390」，✅ 完成）**：同一 /goal 第三十遍——**授权矩阵横向复验（R18 auth_matrix 之后新增/变更端点的角色×端点证据面）**。零产品缺陷（授权面全部成立），2 个行为级测试缺口补齐 + 1 项测试基建留档，全量 1458 通过：

- **静态矩阵复跑（auth_matrix.py）**：158 路由（USER 138 / ADMIN 14 / PUBLIC 6），PUBLIC 面与 R18 裁定完全一致（login/health/shared token/setup 三件套）；R23-R32 变更端点授权级别零漂移——插件热管理四端点（R27 async 化）仍 ADMIN+频控、pricing GET/PUT 仍 USER 且 `user.id` 逐用户隔离（UserPref user_id+key，30/min 限流 + 409 基线）、notes/节点笔记 PUT 仍 USER+owned、SSE 流仍 require_auth。
- **行为级证据盘点（缺口仅 2）**：既有覆盖确认——reload/disable 非管理员 403（test_plugin_hotreload）、SSE 未认证 401（test_api_events）、pricing 跨用户隔离（test_api_pricing）+ 409（iter379）、自由笔记跨用户 404（test_api_free_notes）、节点笔记跨用户 404（test_api_notes）、SSE 心跳重验断流（iter381）、tools.call 404 信封（test_plugin_tools_call）。**缺口**：enable 403 与 GET /api/plugins 列表 403 无行为级钉死（前端有 403 分支但后端无断言）→ 新增 tests/test_iter390_auth_matrix_incremental.py 三测试（enable 403 / 列表 403 / 列表+三端点未认证 401）。
- **测试基建留档（全量首跑红→二分定位）**：新测试复用 test_plugin_hotreload 的插件 id "pp"/路径 ping 时，其「disable 后 404」断言在组合序下必炸——根因：`reset_plugins` 按设计**不摘已挂载路由**（docstring 明示「已挂载的路由随 app 实例存在」，conftest 每测试隔离的既有语义；端点 disable 走 _unmount 身份摘除才是产品路径且正确——进程级探针实证 reset 后路由仍 200 系测试助手语义而非产品缺陷）。处置：装载型测试的插件 id/路由路径须全仓唯一（本测试改 amx390/ping390 + 注释留档雷区）。
- **门禁**：全量 pytest 1458 通过/1 跳过（1455 基线 + 3 新增）+ ruff 全绿。
- **下一步**：滚动审计续（候选：Home/Plans/Review 前端交互面二轮抽检；或备份/恢复链路的真实进程演练复验 R26 结论；或过夜档 soak 择机）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R32（2026-10-18，Python v1 深度审计第二十九轮「iter 389」，✅ 完成）**：同一 /goal 第二十九遍——**长时间低频真实进程 soak（时间维度证据：补齐 R29 泄漏修复在时间而非代数维度的验证）**。零产品缺陷（审计面 clean），soak 脚本入库，40 分钟全绿：

- **静态审计（时间维度增长面清单化）**：① SSE 事件流端点——有界队列（maxsize=100，满则丢帧防慢客户端 OOM，iter 365 C8）+ finally 退订 sink + 心跳周期重验身份（iter 381）+ call_soon_threadsafe 跨线程入队（循环已关静默），连接生命周期无注册表残留；② 宿主唯一后台任务 maintenance_loop——自愈闭环内建（api_usage 保留裁剪 iter 370 / WAL checkpoint(TRUNCATE) iter 368 / 门控 VACUUM / 孤儿目录 mtime 冷却清理）；③ bus._sinks append/remove 对称；④ 29 张 DB 表清单化——历史增长面仅 api_usage（有保留策略）与 Run 系（维护环收敛），其余均限界。全部分派 clean。
- **soak 脚本（tests/soak_overnight.py，手工运行不自动收集）**：真实 uvicorn 进程 ×3 用户低频读（3s 间隔）× 笔记 CRUD 净零写（每 12 周期，含 expected_updated_at 乐观并发链长跑复验）× SSE 常开双流心跳看护（ping 计数 + 最大间隙）× churn 流 300s 周期断开重连（重连质量面）× 分钟级采样（进程内 ctypes RSS/句柄/asyncio 任务/OS 线程 + 进程外只读直连 DB 九表行数 + WAL 尺寸）× 维护环 120s 加速运转。**两相设计**：流量相（32min）捕「随请求增长」，空闲相（8min，零流量仅 SSE+采样）捕「随时间增长」——真时间维度与流量维度解耦。旋钮支持过夜档（SOAK_MINUTES=480）。
- **结果（40 分钟正式跑，exit 0）**：628 请求**零 5xx**（全 200）；SSE 双流各 **159 心跳全程零断**（maxgap 恰 15s=心跳周期本身）；churn 重连 **8/8**；**RSS 130.7→131.8MB（总 +1.2MB 噪声级，空闲相 +0.2MB）**；**句柄 336→329（净 -7，空闲相 +0）**；asyncio 任务/OS 线程恒稳（19/3）；WAL 在 0~0.07MB 震荡被维护环反复截断回 0（checkpoint 生效实证）；DB 九表全程零增量、notes 收尾净零、api_usage 零行（无 LLM 流量不自增）。R29 修复的时间维度证据面闭合。
- **测试脚本自身缺陷（烟雾三轮修复，方法论复用）**：① ctypes 伪句柄须 argtypes=[c_void_p,...] 显式签名（R29 patrol 同款坑，默认 int 截断静默失败）；② metrics 键名 threads 与聊天表 threads 撞名——DB 快照覆盖进程指标，改 os_threads；③ notes 基线须取在长驻笔记创建前；④ 任务数断言改中位数斜率——churn 连接/维护 to_thread/采样请求各占瞬态任务，单点差分误报。
- **门禁**：全量 pytest 1455 通过/1 跳过（本轮无新增自动测试——soak 为手工脚本，由 40 分钟全绿实证）+ ruff 全绿。
- **下一步**：滚动审计续（候选：Home/Plans/Review 前端交互面二轮抽检；或 auth/权限面横向复验 R18 矩阵后的增量端点；或过夜档 soak 长窗（SOAK_MINUTES=480）择机）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R31（2026-10-18，Python v1 深度审计第二十八轮「iter 388」，✅ 完成）**：同一 /goal 第二十八遍——**前端交互逻辑专项抽检（组件级『表达与实现一致性』：编辑器/表单流程、UI 状态机与加载失败路径、导航与权限交互）**。5 处用户交互缺陷修复（4 处集中在 Chat.tsx），tsc+build 与全量 1455 门禁绿：

- **审计矩阵（精读分派，全部过目）**：编辑器/表单——Notes（openSeq 迟到响应/draftRef 并发输入/自动草稿+beforeunload 双保险/批量删重置）、NodeNoteEditor（同款草稿兜底+保存往返不覆盖）、Settings 行内编辑（预算空串拦截 iter 370/0=不限确认）、ModelPricingManager（R22 基线绑定/init+自保存采纳/重复与非法单价就地报错）、UserManager/ProviderManager/CredentialManager（统一 run() 防重入+opError 模式）；状态机——GenerateProgress（attach 判态/404/idle 引导/断线双横幅/iter 364-368 修复在位）、PlanDetail（D1/D3/D6/D8 分离+版本对比/重生成 planIdRef 守卫）、QuizModal（M10 集合更换清作答/回写去重 ref/键盘流）；导航权限——RequireAuth（needs_setup/open_mode/token 三态+search 保留）、Login/Setup（skipBusy 分离）、auth 401 链（apiFetch/SSE/上传三面 + clearAuth 清用户级 localStorage）、Home dt-home-prefill 消费闭环。**留档接受**：NodeNoteEditor 收起 <500ms 防抖窗（草稿兜底既有设计）、Home 可选增强面板静默隐失（非假信息）。
- **缺陷 C1（Chat 新对话失败零反馈）**：createM useMutation 无 onError——创建失败按钮弹回但无任何提示。修复：onError → toast。
- **缺陷 C2（会话列表错误伪装空态·假信息）**：listThreads 失败时 items 回退 []，列表落「尚无对话，点上方新建」——用户会误以为全部会话丢失（/goal「表达与实现一致」直接命中）。修复：threadsQ.isError 错误卡+重试（Notes/Plans 同口径），空态加 !isError 守卫。全仓同类排查：仅 Chat 一处 endemic（Wiki/Plans/LearnIndex/Search/Usage/Review/SharedPlan/Learn/Clarify 均有显式错误分支）。
- **缺陷 C3（消息错误伪装空白会话）**：listMessages 失败渲染成完全空白的对话区（无任何提示）。修复：msgsQ.isError 错误卡+重试。
- **缺陷 C4（重命名失败不可见路径）**：patchThread 失败写 setError——仅选中会话时可见，activeId 为 null（刚进页在列表直接重命名）时失败完全不可见。修复：改全局 toast。
- **缺陷 N1（Notes 数据丢失窄窗）**：点击列表中**当前正编辑（dirty）**的笔记——同 id 跳过 confirmDiscard 直接 GET 全文覆盖编辑器，最后 500ms 防抖窗内未落盘的输入被静默丢弃（防抖已落盘则有恢复确认双保险，仅 <500ms 窗裸奔）。修复：同 id 且 dirty 直接不动作（编辑中无需重取）；非 dirty 保留原重取刷新路径。
- **回归**：前端无测试基建（无 vitest/jest）——门禁为 `npm run build`（tsc --noEmit + vite build）两轮绿；后端未动但按既定流程跑全量 pytest 1455 通过/1 跳过。
- **下一步**：滚动审计续（候选：长时间低频 soak 过夜档；或 Home/Plans/Review 剩余交互面第二轮抽检）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R30（2026-10-18，Python v1 深度审计第二十七轮「iter 387」，✅ 完成）**：同一 /goal 第二十七遍——**前端契约一致性增量复核（R23-R29 后端改动 vs R19/R21/R22 契约矩阵基底）**。零产品缺陷（1 个契约提取器工具缺陷修复），全量 1455 通过：

- **形状面（契约三件套复跑）**：contract_audit（158 路由）→ contract_frontend（61 接口/107 调用）→ contract_diff 复跑。首轮 diff 报 2 行「遗漏字段」（GET/PUT /settings/pricing 的 `updated_at` vs PricingTable）——查证前端**已声明**（api.ts PricingTable JSDoc 注明装载基线），提取 JSON 实证漏提；根因 `_balanced()` 把 JSDoc 注释里的 `->` 计入尖括号深度，接口体在 `无已存表 -` 处截断。修复：contract_frontend.py 增引号感知 `strip_comments()`（保留 ' " ` 字符串含转义、剥 // 与 /* */），入口统一先剥注释再解析。复跑 **diff 与 R17 基线字节一致（rc=0）——本会话 R23-R29 全部后端改动（删除族收敛/热管理 async 化/404 信封/displaced 栈/lifespan 修复等）前端契约零漂移**。
- **行为面（逐项核查，全部成立）**：① SSE 重连契约——api.ts 声明「调用方负责重连退避与生命周期」↔ EventsBridge.tsx 实装（指数退避+full jitter ≤30s、干净关闭 attempt 不复位防快打循环 iter 370、sleep 可被 abort 唤醒防协程泄漏 iter 368、401 不重连、卸载清理完整）——R24 心跳重鉴权造成的服务端主动断流有兜底；② 乐观并发 409——notes/pricing PUT 均回传 expected_updated_at（api.ts 452-456/808-811），taskEngine 409-stale→interrupted 可重试、failFast 402/404/409；③ 插件热管理 429 信封——`rate_limited` 码中文 message 经 http 层 `new ApiError(err.code, err.message, status)` 原样透出 → Plugins.tsx catch toast 呈现；页面单飞互斥（toggling 禁用全部按钮）+ useQuery `retry:false`（无自动重试风暴）+ GET 列表不限流（invalidateQueries 不吃 20/min 配额，设计自洽）；④ `api.tools.call` 未知工具 404 信封——前端无直接调用面（grep 证实），仅后端插件面消费（R28 已端点级钉死双契约），分派销案；⑤ 基线 diff 报告其余项（credentials 幻影端点模板工件等）系 R17-R22 时代已裁定项，未变。
- **工作区核查**：契约生成物四件（backend/frontend_contract.json、contract_diff_report.txt、auth_matrix.json）.gitignore 覆盖确认（b2cff97 入库）；iter380 测试已入库、probe 脚本已清——本轮唯一改动 = contract_frontend.py。
- **门禁**：全量 pytest 1455 通过/1 跳过（基线不动，本轮无新增测试——工具修复由三件套复跑字节等价验证）+ ruff（app/tests + 根 plugins/）全绿。
- **下一步**：滚动审计续（候选：长时间低频 soak 过夜档；或前端交互逻辑专项——表单/编辑器丢失更新面在 R22 已覆盖、剩余交互面抽检）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R29（2026-10-18，Python v1 深度审计第二十六轮「iter 386」，✅ 完成）**：同一 /goal 第二十六遍——**长时间运行内存/句柄泄漏巡检（热管理多代循环趋势）+ 沿线两个真缺陷修复**。7 新回归、patrol/soak 双脚本验证，全量 1455 通过：

- **静态审计（模块级增长容器清单化）**：runtime._statuses/_plugin_tools、loader._plugin_apis/_disabled_ids、bus 三表（done 回调清引用）、scheduler._tasks（iter 365 C4）、webhooks._bg_tasks、providers/parsers/exporters 注册表 + displaced 栈、plans._STREAMS / memory._RUN_STREAMS / _CHAT_RUN_TASKS（R23-R24 已审）、verify_url 缓存（TTL+4096 上限）、频控桶（惰性淘汰+_MAX_KEYS 兜底）、LLM client 缓存（LRU+aclose 调度 iter 365 B3）、skills 注册表（文件有界）——除 displaced 栈外全部有界。
- **缺陷 A（displaced 幽灵复活·三注册表同构）**：iter 369 的覆盖回填栈按身份管理前任，但被覆盖插件**重载**后旧 entry 对 map 是 no-op、以幽灵身份永留栈——后续卸载按栈顶回填会**复活已卸载插件的 handler**（解析器/导出器/Provider，致命序列：A 注册→B 覆盖→A 重载→B 卸载→A 卸载 ⇒ map 指向死 B）。修复：unregister(entry) 先把 entry 自身从全部 displaced 栈剔除（3 处同构修复）；4 回归（三注册表各一 + 活前任正常回填不回归）。
- **泄漏 patrol（tests/soak_leak_patrol.py，手工运行）**：自检插件（进程内 ctypes 读自身 RSS/句柄——伪句柄须按 64 位指针传参，默认 int 截断会静默失败）+ 8 插件 30 代 reload/disable/enable 循环 × 流量。首轮结果：RSS 平稳（+2.2MB/30 代）、dt_plugin 模块逐出恒稳、**句柄线性 +1/代**（60 代 +75）。
- **四层二分定位**：纯流量平稳（含 webhook 死端口路径）⇒ 重载路径；逐插件二分 ⇒ hookp 单独即漏；变体二分 ⇒ 纯路由（含 emit 闭包）漏、纯 webhook 注册平稳；第三变体 ⇒ **常量返回路由平稳——触发器 = 路由闭包捕获 `api` 门面**。进程内最小复现放大（20 代 +123 句柄、gc 后仍滞留 14k 函数/1676 Dependant）+ 引用者透视 ⇒ **根因（FastAPI 0.139 源码级）：include_router 末段无条件 `_merge_lifespan_context` 把子路由器 lifespan 合并进 app.router.lifespan_context 且无反合并 API——卸载只摘路由条目，合并链闭包永久持有插件 APIRouter→路由→endpoint（捕获 api 时整张门面图连同插件状态被长寿引用）**。
- **缺陷 B 修复（RoutesNamespace.mount）**：include 前把默认 `_DefaultLifespan` 换为无状态实现（router.on_startup/on_shutdown handler 本就被 include 复制进 app 事件表由 app 侧执行——行为不变，且消除合并链里默认 lifespan 的潜在双跑）；自定义 lifespan 路由器不替换（保留既有语义）。weakref 红绿验证（修复前 api 图不死→修复后可回收）+3 回归。**patrol 终验：句柄 30 代漂移 +0（344→345 震荡）、RSS +2.4MB、模块逐出干净**；R28 热管理 soak 复验全绿。
- **测试基建留档**：① gc 断言类测试中，测试帧的局部变量（router/默认参元组）会把待回收图钉住致假红——被测对象图须在辅助函数内构造；② 泄漏趋势断言按实测墙钟换算（宿主机 sleep 超时会伪装成服务端异常）；③ 进程内 GetProcessHandleCount/GetProcessMemoryInfo 需显式 argtypes/restype。
- **门禁**：全量 pytest 1455 通过/1 跳过（1448 基线 + 7 新增）+ ruff（app/tests + 根 plugins/）全绿。
- **下一步**：滚动审计续（候选：前端契约一致性抽检——R19/R21/R22 已有矩阵基底的增量复核；或长时间低频 soak 过夜档）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R28（2026-10-18，Python v1 深度审计第二十五轮「iter 385」，✅ 完成）**：同一 /goal 第二十五遍——**真实进程插件热管理 soak（R27 修复的线上语义复验 + soak 首个战果）**。1 个 soak 实证缺陷修复，soak 脚本入库，连续两轮全绿：

- **soak（tests/soak_plugin_hotreload.py，手工运行不自动收集）**：5 型插件（调度器 every/中间件/webhook+事件/LLM Provider+计量/api.tools.call）× 8 worker 请求风暴（~3000 请求/轮）× 并发 reload 全部→disable 全部→enable 全部交错 + SSE 事件流常开 + 本地 webhook 接收器（HMAC 验签）。全绿×2：**S1 复验**（reload/enable 后全部 loaded，修复前调度器插件必 failed）；**S2 复验**（disable 后 tick 文件 3s 采样全平零泄漏，enable 恢复递增）；**500=0**、中间件头 on/off 双向生效、**SSE 全程存活心跳不断**、**webhook 稳定期 10/10 精确投递 + 验签全对**、**计量精确守恒**（complete 200 次数 == api_usage soak-model 行数，tokens 严格 10/5 每行）、优雅关停 + integrity ok。
- **soak 战果（真缺陷·已修）**：`api.tools.call` 未知工具抛裸 `ValueError`——插件热管理窗口期（unload 后在途路由请求 / reload 重注册前，在途请求挂在依赖解析 await 上恢复时工具已注销）逃出插件路由成 **500**。其余四个扩展点（providers/parsers/exporters）同类时序均信封化优雅降级，唯独 tools 500。修复：`UnknownToolError(ApiError(not_found,404), ValueError)` 双继承（app/api/errors.py 叶子模块定义——门面顶层导入会构成 app.api.__init__ 环）：既有 `except ValueError` 插件与 API.md 契约完全兼容，未捕获时宿主统一信封 404。+2 回归（类型双契约 / 端点级 404 信封非 500）+ API.md 契约同步。
- **调查并销案两条**：① disable 窗 503（no_llm_provider）= 在途请求恢复时 Provider 已注销的设计内信封降级（与 429 频控同类，可重试）——soak 判定改 500 零容忍 + 503 有界豁免（≤5）；② 启动窗 tick +5~6/s 疑双任务——时间戳诊断实证服务端节奏恒 0.31s 单任务（单/五插件环境一致、无任何 <0.2s 间隔），系宿主机 time.sleep 实际超时的测量伪影——soak 窗口断言全部改按**实测墙钟**换算周期数。
- **soak 方法论沉淀（脚本注释留档）**：泄漏检测的 tick 计数必须落共享文件（模块级 dict 在 disable→enable 重执行模块后归零，断言空真——首版实证踩坑）；进程外直读 DATA_DIR 文件可绕过「禁用期路由 404」的观测盲区。
- **门禁**：全量 pytest 1448 通过/1 跳过（1446 基线 + 2 新增）+ ruff（app/tests + 根 plugins/）全绿。
- **下一步**：滚动审计续（R23-R28 已覆盖删除族/更新面/取消计费/导入写入族/热管理两轮；候选新证据面：前端契约一致性、或长时间运行内存/句柄泄漏巡检）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R27（2026-10-18，Python v1 深度审计第二十四轮「iter 384」，✅ 完成）**：同一 /goal 第二十四遍——**插件热重载 × 在途请求交互全矩阵审计（十二扩展点逐面分派）**。2 个真实缺陷修复 + 1 个既有测试前缀误命中修复，4 新回归，全量 1446 通过：

- **审计矩阵（分派结论）**：routes 按身份摘除（在途请求持已匹配路由对象跑完旧代码，设计内降级）✓；events/sinks/webhooks 退订后在途协程有界完成（10s 超时 + 强引用台账）✓；SSE sink 经 call_soon_threadsafe（R24 已处理）✓；parsers/exporters/providers 三注册表身份匹配注销 + displaced 栈回填前任（iter 369），在途调用持 entry 对象跑完 ✓；工具注册表每请求快照（default_registry）+ remove_tool 相等性实为 func 身份 ✓；PluginProvider 持 entry 无逐次查表 ✓；middleware 栈重建原子替换、在途走旧栈 ✓；api.llm 在途用量 finally 落账 ✓。留档接受 1 项：reload 后新旧 StorageNamespace 实例并存窗口（旧实例内存态滞后写可覆盖新写，窗口=在途请求生命周期、仅插件 KV、完整修复需静默等待在途——v1 接受）。
- **S1（行为不一致·真缺陷）**：热管理端点 reload/disable/enable 原为 sync def——FastAPI 跑 anyio 线程池，register 内 `api.scheduler.every/once` 的 `get_running_loop()` 必抛 RuntimeError → **使用调度器的插件启动成功（lifespan 内有循环）但永远无法热重载/热启用**（热路径必 failed）。修复：三端点改 `async def`——register/回滚在事件循环线程执行，与启动路径同语义（register 阻塞暴露面与启动一致，admin+频控 20/min 已限面）。
- **S2（线程安全·真缺陷）**：卸载回滚的 `TaskHandle.cancel` 在线程池线程裸调 `task.cancel()`——asyncio Task 非线程安全：debug 模式 `_check_thread` 抛 RuntimeError 被回滚异常隔离吞掉 → **任务泄漏、已卸载插件的旧代码永续后台跑**（红验证：独立脚本 debug 模式实证 RuntimeError + 任务悬置 cancelling 态不终止）；非 debug 轻则取消延迟生效（Future.cancel 内部 call_soon 不唤醒选择器）。修复：`scheduler._cancel_task` 判断当前线程即所属循环线程，否则经 `call_soon_threadsafe` 投递取消（循环已关静默返回）；TaskHandle.cancel 与 reset 统一走它（覆盖插件 sync 路由自行 cancel、conftest reset_plugins 测试线程路径）。
- **附带加固**：scheduler.every/once 任意线程可注册——无循环上下文回退 `bind_loop` 绑定的宿主主循环（load_plugins 于 lifespan 内绑定、reset_plugins 解绑防指向已关闭循环；Event 握手 10s 超时报可诊断错误）——覆盖插件 sync 路由直接注册调度的面；`SchedulerNamespace` docstring 同步（"须在循环内调用"的旧约束已失效）。
- **既有测试修复（iter 369 假红）**：`test_package_plugin_submodule_reload` 的 sys.modules 残留断言裸 `startswith("dt_plugin_pkg")` 误命中他测试装载的 `dt_plugin_pkgp`（pkgp）——组合序确定性假红（stash 实证与本轮改动无关）；收窄为精确名 + `dt_plugin_pkg.` 前缀。
- **回归（tests/test_iter384_hot_reload_inflight.py，4 个）**：T1 端到端——调度器插件完整热生命周期（lifespan 加载→reload 保持 loaded 且旧任务取消/新任务跑→disable 计数冻结+路由 404→enable 恢复）；T2 跨线程 cancel（debug 模式钉死旧实现红）；T3 线程池上下文注册回退绑定循环；T4 未绑定明确报错。测试基建留档：协程内同步轮询/join 会堵死事件循环使 threadsafe 回调永不到达（须异步轮询 / `await asyncio.to_thread(t.join)`）。
- **门禁**：全量 pytest 1446 通过/1 跳过（1442 基线 + 4 新增）+ ruff（app/tests + 根 plugins/）全绿。
- **下一步**：滚动审计续（候选：真实插件级 soak——多插件并发装载/卸载 × 在途请求风暴）；或回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R26（2026-10-18，Python v1 深度审计第二十三轮「iter 383」，✅ 完成）**：同一 /goal 第二十三遍——**备份导入写入族竞态（R23 删除族的对偶面）+ R23-R25 新改动路径的真实进程 soak 复验**。零产品代码改动（审计面 clean + 1 项留档），1 个 soak 脚本入库、1 commit：

- **静态审计（导入面分派）**：_RESTORE_LOCK 互斥 ✓（iter 369）；restore 全程 to_thread ✓（R6 B14，导入不冻事件循环）；append-only 不触内存态（无 _STREAMS/_RUN_STREAMS 失效需求，导入无 running 状态白名单）✓；preview 摘要无一致性承诺（TOCTOU 无害）✓；导入事务持写锁期间 id 分配不受并发删除交错（写锁串行）✓。
- **留档（v1 接受，v2 数据层销项）**：导入事务持 SQLite 写锁期间（实测 0.41-0.53s/30 线程载荷；上限 256MB 导入可超 busy_timeout 10s），在途 _finalize 的同步 commit 在**事件循环上等锁**——秒级循环冻结窗口（全部用户 SSE 心跳停发/请求停）与极端超时后的计费丢失（record 吞异常回滚）。R7 已裁定导入维持单事务（产品决策），v2 SQL 数据层多连接并发写天然分离。
- **真实进程 soak（tests/soak_restore_races.py，手工运行不自动收集）**：
  - **S2 = R24 修复的真实进程复验**：真实 SSE socket 连接 → 管理员改密（吊销 tokens_valid_after）→ 服务端 **15.0s 收流**（恰一个心跳周期——重验机制精确生效；浏览器/EventsBridge 同路径）。
  - **S1 = 删除族×导入写入族交错风暴**：8 用户 15s 混合读写**含删除**（386 次成功删除）× 期间串行 3 笔实质导入（threads×60 messages 载荷，写锁各持 0.4-0.5s）+ 导出 910KB——**1302 请求零 5xx**（429=限流设计行为）。
  - **S3 = 事后完整性**：优雅关停后 SQLite integrity_check=ok、foreign_key_check 零违规；threads=90/messages=5400 与 3×(30×60) 导入精确对账。
- **测试基建发现（留档）**：httpx 0.28.1 的 `stream()` 进入阶段会等首个网络读——SSE「头即时、首字节 15s 心跳」场景进入即 ReadTimeout（客户端特性；原始 socket 实证服务端头即时到达、心跳准时）——soak 的事件流长连接须用原始 socket（本脚本已按此实现）。
- **门禁**：全量 pytest 1442 通过/1 跳过（本轮零产品改动，基线不变）+ ruff（app/tests + 根 plugins/）全绿。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R25（2026-10-18，Python v1 深度审计第二十二轮「iter 382」，✅ 完成）**：同一 /goal 第二十二遍——**长连流家族断连取消与 finalize 收敛语义一致性**（材料 SSE / quiz 后台 / slides / kb，对照 R23/R24 已钉死的 plans/chat/events 三类流语义标准：断连任务独立、终态必达、计费守恒、内存零泄漏、硬取消传播）。1 个真实缺口修复、3 个新回归、1 commit：

- **缺口（取消路径成本黑洞，iter 243 语义的最后一处缺口）**：LLM 工具调用在途被取消（task.cancel → CancelledError 传播）时，**已累积的部分 usage 随协程局部变量丢弃**——run_tool 的 except Exception 不捕 CancelledError（Python 3.8+ 属 BaseException），_billed 的回填值无人消费，api_usage 无行 = 绕过日预算守卫的不可观测黑洞。触发面：①编排任务取消（删会话/删用户，R23 场景）；②材料流断连取消在途 worker；③材料流「worker 已完成、主循环未消费」窗口（断连恰在 task.result() 前——历轮修复覆盖了文件清理与槽释放，计量面漏了）。
- **修复（寄存器设计，不吞取消）**：`ExecContext.orphan_billed` 列表——`loop._run_llm_tool` 在 CancelledError 传播前寄存 `build_metadata(total,...)`（设计取舍：**不吞取消**——吞掉会让编排器走 error 而非 cancelled 终态，前端 taskEngine 的 cancelled 相位与事件码语义回归）；`plans._run_start` 把 `ctx.orphan_billed` 挂到 stream（同一 list 对象）+ `_finalize` 开头统一 drain（cancelled/异常/守卫分支全覆盖，record_metadata_usage 内部自 commit、FK 自防）；材料流三接线：worker run_tool 完成即寄存、主循环消费后移除（防双落）、finally 清理线程统一落账（在 rollback 后逐条补记，随 index 写入同事务提交）。
- **分派（clean，留档）**：slides/quiz/kb 为同步或短后台任务——线程池执行不受客户端断连中断、计费在调用完成点即时落账、quiz 后台对会话删除的 FK 冲突已留档（R23：计费已先行落账）；前端材料流为裸 fetch 无自动重连（非幂等端点无误重连双计费）；材料流断连的收敛延迟 = 心跳间隔（≤15s，Starlette aclose 等 yield 注入的固有机制）。
- **回归（test_iter382_cancel_billing.py，3 用例）**：T1 单测级——畸形响应（accumulate 100/50）后挂起再 cancel：CancelledError 照常传播 + orphan_billed 恰一条且 token 守恒；T2 端到端——生成中 DELETE 会话（iter 380 后台线程流模式）：取消后 intent 修复链第一轮的部分计量经 _finalize drain 落 api_usage（用户键幸存）；T3 材料流断连（`agen.aclose()` = Starlette 客户端断连的内部机制）——unit1 挂起、unit2 完成：断连后 unit2 的未消费计量 drain 落账（增量恰 1 行 100/50）、内容不落库（丢弃可重生成）、unit1 挂起取消零伪造行。测试技巧留档：**async generator 挂在 yield 处不自行推进**——两次 anext 消费 unit_start 后 ensure_future 尚未执行到，须第三次 anext 驱动到 keepalive yield，worker 才被创建（首版 T3 因此假失败）。
- **门禁**：全量 pytest **1442 通过/1 跳过**（+3）+ ruff（app/tests + 根 plugins/）全绿；前端无改动（材料流前端本就无自动重连，无接线需求）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R24（2026-10-17，Python v1 深度审计第二十一轮「iter 381」，✅ 完成）**：同一 /goal 第二十一遍——**更新面 × 服务端内存态一致性矩阵**（全部 PATCH/PUT/DELETE 端点对进程内状态——SSE 流身份/事件归属缓存/TTL 缓存/解析锁/注册表——的失效与同步语义，R4-R23 未系统核对过的交叉面）。2 个真实缺口修复 + 1 个测试时序缺陷修复、6 个新回归/更新、1 commit：

- **修复 1（事件归属缓存跨用户错发窗口）**：`resolve_owner` 的 session_id→owner 60s TTL 缓存（iter 367 为「每事件×每订阅者查库」加的收敛层）在「删会话后 SQLite 复用 rowid」场景把新会话事件按缓存的**旧 owner** 过滤——旧 owner 收到他人 plan_created/materials_completed（含 topic/单元数＝**跨用户信息泄露**）且正主收不到（前端 toast/实时刷新静默失效）。修复＝**移除缓存即时查库**：负载前提已不成立（现事件面仅 plan_created/materials_completed 不带 user_id，每方案 2 次；其余核心事件全部直归属零查询），直查=每订阅者×每方案 2 次主键读可忽略；即时查库即真实归属，窗口彻底关死（删除/rowid 复用/备份导入恢复均自洽）。
- **修复 2（吊销后已建事件流永续）**：GET /api/events/stream 只在建立时认证一次，SSE 靠 15s 心跳保活无寿命——管理员删用户/改密（tokens_valid_after）后，**已建立的流永续接收该用户全部实时事件**。修复＝events_generator 心跳周期重验身份：`_user_still_valid`（用户存在 + JWT 吊销水位，与 deps 的 JWT 业务校验同口径含 +1s iat 容差；PAT/开放模式只验存在性），失败即断流，客户端按既有退避重连→重新认证→401。端点从 Bearer 提取 iat 随流传入（request 参数；非 JWT 凭证 iat=None）。每心跳一次短会话主键读（微秒级，15s 周期），100 用户 SSE ≈ 7 查询/秒。
- **分派（clean，留档）**：PATCH /threads（title/model 标量；在途 run 启动时锁定 model 不受影响）；PUT credentials/providers（anthropic 客户端缓存按 (key,base_url) 寻址，换 key 自然新 client+旧 client 后台关闭 R6；在途 run 持旧 provider 跑完＝有限寿命快照语义）；PUT prefs（daily_budget/llm_max_tokens 下次生成前读取生效，在途快照）；PUT pricing（落账点按最新表重算）；PUT users 改密（新请求 401 R5；已建生成/chat 流有限寿命+R23 wait_generation_settled 收敛，可接受）；PUT kb title/notes（无内存态；notes R21 已审）；plugins 热管理（displaced 栈 R6）；_PARSE_LOCKS（finally pop，R23 T4 护栏）；_STREAMS 300s 保留窗（R23 已验证 DB 行删除后不再被引用）；plan topic 5min 缓存（消费方 stats_by_plan 以 DB LearningLog 聚合为准，plan 删→logs 级联删→不再触达，重生成陈旧注释已声明）。
- **测试**：test_iter381_events_reauth.py 5 用例——rowid 复用归属即时翻转（正主收/旧主不收，防引擎行为变化显式 skip）；正常用户心跳持续（重验不误伤）；删用户断流；JWT 吊销断流（含 iat+1 容差边界恰吊销）；PAT 模式只验存在性。既有测试同步：iter367 缓存测试反转为直查语义（3 次解析 3 次查库）、events 心跳测试 reauth=False、event_stream 直调传 stub request。
- **测试时序缺陷修复（全量偶发红根因）**：test_plugin_scheduler test_every_cancel_stops 全量跑 1 失败（单独 8/8 过）——实証定性：cancel 打断的是调度循环等待，**已在途的 to_thread fn 线程不可中断且必然完成**（iter 368 sync-fn 移线程池的固有语义），测试在 cancel 后立即采样基线把这次在途完成误判为「cancel 后仍在跑」。修＝基线采样延后一个窗口（0.12s）吸收在途轮次，断言强度不变（0.1s 无新轮次）；修后 6 连跑全绿。教训与 R13 同款：全量偶发红先复现率实证再定性，本次定性为测试假设缺陷而非产品缺陷。
- **门禁**：全量 pytest **1439 通过/1 跳过**（+5）+ ruff（app/tests + 根 plugins/）全绿；前端无改动。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R23（2026-10-17，Python v1 深度审计第二十轮「iter 380」，✅ 完成）**：同一 /goal 第二十遍——**删除族 × 在途操作竞态审计**（R4-R22 从未系统核对过「每个 DELETE 端点执行时，恰有任务在途操作同一资源」的存活假设）。2 个真实缺口修复 + 1 项测试基建发现、6 个新回归、1 commit：

- **修复 1（删除路径成本黑洞）**：生成中 DELETE /sessions → 取消任务 → `_finalize` 「会话已删除」守卫一刀切**不写 DB**——理由只对 tool_calls 成立（session FK 不可写），但取消前已执行工具烧掉的 **api_usage（user 键）本可保留**，结果整批蒸发：日预算只信 api_usage 求和，缺账 = 预算绕过通道（iter 243 语义的删除路径缺口）。修复（三层）：①`_SessionStream` 增 `user_id` 锚点（`_run_start` 赋值）；②守卫分支 + 取消/异常路径均传 `orphan_user_id`，`_persist_tool_calls` 会话已删时降级 usage-only（跳过 ToolCall 只落 ApiUsage，计价同口径）；③落账撞 FK（与删除事务交错的竞态）再降级 `_persist_usage_only` 重试；用户亦被级联删时整段跳过（FK 防孤儿行，配合 users.py 显式清 api_usage）。
- **修复 2（删除端点 500）**：`cancel_running_generation`/`_cancel_run_stream` 只**调度**取消——`_finalize`（事件循环线程）与 delete_user/delete_session 的删除事务（线程池）并发交错时，finalize 新插的 api_usage 行会让 user 删除的级联 FK 检查失败 → **DELETE /users 500 用户删不成**（实测复现：单独跑通过、全量跑必挂的时序敏感缺陷）；session 侧同交错致 tool_calls 整批回滚（只剩降级兜底）。修复：新增 `plans.wait_generation_settled`（`task.done()` 且 `terminal_id` 置位判收敛——`_finalize` 各分支含兜底 except 均以 mark_terminal 收尾且落账在前；取消传播毫秒级，15s 超时容忍、降级兜底仍在），`delete_session`/`delete_user` 取消后等收敛再删。
- **测试基建发现（坑，留档）**：**starlette 1.3.1 `TestClient.stream()` 是完全缓冲 transport**（`portal.call` 等 ASGI app 整体返回后才让 with 块进入）——「流进行中在主线程做别的」不能用同步 `with client.stream(...)`：无超时兜底的挂起（如门控 LLM 阻塞）with 永远进不去；有 LLM 120s 硬超时兜底的要等 2 分钟才进入（且 DELETE 实际发生在任务收敛后——测的已不是「生成中删」）。正确模式 = **后台线程发流请求（线程恰挂在 transport 等 app 返回 = HTTP 请求在途状态），主线程轮询到位后 DELETE，终态后 join**；探活走 fixture client 同一 portal 事件循环顺带驱动后台任务。
- **回归（test_iter380_delete_inflight_races.py，6 用例，4s）**：T1 生成中删会话——删除前计费未落、删除后 intent 100/50 token 落 api_usage（钉死修复 1）+ session/state/plan/tool_calls 零残留 + 流收敛（terminal 判据）；T2 对话 run 在途删 thread——硬取消 + 级联 + finalize run=None 优雅收口 + `_RUN_STREAMS` 清空；T3 生成中删用户（admin）——在途取消 + 全库零孤儿（含 orphan 落账识别用户已删不插行）+ 管理员完好；T4 三护栏——生成中删资料 409 / 解析中删 KB 文档 409 / running run 删 409（统一信封）+ DB-running-无流残留（重启后形态）删 thread 200 收敛。5 连跑全绿（4s/次）。
- **静态分派（clean，留档）**：其余 DELETE 无在途写面——kb/notes 问答记录=请求内原子持久化；笔记/凭证/供应商/分享/记忆/打卡日志=叶子行。已知良性残留：生成 quiz 的 HTTP 请求在途时会话被删 → quiz 行插入撞 FK 500（计费已在调用完成时落账 user 键，语义即「会话已不存在」，可接受留档）。
- **顺带**：R17/R18 手工审计脚本的 4 个生成物（backend_contract.json / frontend_contract.json / contract_diff_report.txt / auth_matrix.json）加入 .gitignore（脚本复跑再生，不入库）；上一会话遗留的调试探针（test_iter380_probe*.py）已删。
- **门禁**：全量 pytest **1434 通过/1 跳过**（+6）+ ruff（app/tests + 根 plugins/）全绿；前端无改动。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R22（2026-10-17，Python v1 深度审计第十九轮「iter 379」，✅ 完成）**：同一 /goal 第十九遍——**全量覆盖型写面并发语义收尾**（R21 笔记缺口的同类别残留面）。9 处 PUT/PATCH 逐一读实现分派 + 1 处真实缺口修复，3 个新回归、1 commit：

- **写面矩阵（除 R21 已修两处笔记外，余 7 处逐一定性）**：①PATCH /threads/{id}（title/model 标量）②PUT /credentials/{id}（None=保留的标量合并 + 重名 409）③PUT /providers/{id}（同上）④PUT /kb/documents/{id}（仅 title 单字段重命名）⑤PUT /settings/prefs（exclude_unset 键级合并——改 A 不动 B，双标签改不同键互不吞）⑥PUT /users/{id}（管理员面标量）——均字段级/单字段/管理面，**无跨字段合并丢失面，clean**；⑦**PUT /settings/pricing——整表读改写（设置页载入→编辑→全量 PUT，UserPref 单行 JSON ≤200 模型×4 单价），双标签后保存者静默吞掉先保存者条目＝R21 同类真实缺口**。
- **修复（乐观并发，R21 先例原样）**：ModelPricingIn 加可选 `expected_updated_at`，不匹配（含他窗口清空删行 -> cur=None）→ 409 `conflict`「计费表已在其他窗口被修改，请刷新后重试」；不传保持旧行为。GET /pricing 与 PUT 回执均加 `updated_at` 基线（加性字段）。
- **前端（ModelPricingManager）**：`baseline` 状态与 `rows` 的派生版本一一绑定——初始化（data 装载）与自身保存成功（saved 回执）时采纳；**后台 refetch 换新表不回写基线**（rows 仍派生自旧版本，基线必须跟旧版本走，否则他窗口保存会被静默吞掉——这是本轮接线的关键点）。409 经既有 opError 呈现，编辑行保留。
- **回归（test_iter379_pricing_concurrency.py，DB 回拨法）**：过期基线 409 且他窗条目不被吞 / 新鲜基线放行+回执新基线+GET 一致 / 不传 LWW 兼容 / 他窗清空删行后旧基线 409、清空后 GET 基线归 null。
- **门禁**：并发回归 7/7（含 R21 4）、settings 族 36 通过、ruff 绿、前端 tsc+build 绿（3.52s）、全量 pytest **1428 通过/1 跳过**（+3）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R21（2026-10-17，Python v1 深度审计第十八轮「iter 378」，✅ 完成）**：同一 /goal 第十八遍——**同用户多标签页并发写语义审计**。逐路径分派 + 1 处真实缺口修复（笔记双写丢失更新防护），4 个新回归、2 commit：

- **并发语义矩阵（逐路径分派）**：①进度更新 POST /progress——已有乐观锁（iter 367 mastery CAS 重放 + 插入竞态 A11 兜底），无需动；②测验提交——纯 append（每次作答一行，「最新 attempt」口径消费），无丢失更新面；③学习打卡——append by design（多次打卡各记一条）；④计划重生成——sessions 状态机互斥 409（既有）；⑤**自由笔记 PUT /notes/{id} 与知识节点笔记 PUT /knowledge/notes/{node_id}——全量盲覆盖（last-write-wins），双标签编辑同笔记后保存者静默吞掉先保存者的修改，零提示**＝真实缺口。
- **修复（乐观并发，兼容式）**：两处 PUT 加可选 `expected_updated_at`（客户端把装载基线的 updated_at 原样回传），不匹配（含他窗口清空删行 -> cur=None）→ 409 `conflict`「笔记已在其他窗口被修改，请刷新后重试」；**不传保持旧行为**（插件/外部调用方兼容）。前端 Notes.tsx / NodeNoteEditor 把已跟踪的基线时间戳随保存回传（两编辑器本就在状态里维护 updatedAt，仅接线）；409 经既有 toast.error 呈现服务端消息，编辑器内容保留（用户可复制后刷新，不静默丢失）。已知粒度限制：SQLite CURRENT_TIMESTAMP 秒级——同秒双写仍 LWW（代码注释留档）。
- **回归（test_iter378_note_concurrency.py，DB 回拨 updated_at 模拟他窗口先保存，避开秒粒度等待）**：过期期望 409 且服务端内容不被吞 / 基线一致放行 / 不传兼容 LWW / 节点笔记清空删行后的旧基线 409。
- **门禁**：全量 pytest **1425 通过/1 跳过**（+4）+ ruff 绿 + 前端 tsc+vite build 绿（前端接线改动）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R20（2026-10-17，Python v1 深度审计第十七轮「iter 377」，✅ 完成）**：同一 /goal 第十七遍——**LLM 调用面异常注入审计（真实端点 + monkeypatch）**。三层静态审计（provider 异常映射/工具层 _billed/落账层 executed 全量记）+ 6 场景端点级注入全部通过，**零真实缺陷**——此前 iter 243 成本黑洞语义与 R24 断连持久化只有注释/单元级声称，本轮首次端点级钉死（`tests/test_iter377_llm_failure_injection.py`，6 用例）。1 commit：

- **注入矩阵（ScriptedProvider 按脚本出牌 + POST /plans/generate 真端点）**：①429 瞬态重试耗尽（首工具）→ error 终态、重试恰 N+1 次有界、零伪造计费（失败调用无 usage 不落账）、tool_calls error 留痕、无 Plan 行；②首轮成功(畸形 JSON)+次轮 4xx → error 终态且**首轮 100/50 token 必须落账**（日预算只信 api_usage——成本黑洞端点级回归）；③中途（第 3 工具）4xx → error 终态 + 前两轮落账 + 无 Plan；④intent 畸形修复耗尽 → **降级澄清追问**（default_data needs_clarify=True，paused_clarify 优于硬错误，用户可补信息续跑）+ 修复链聚合落账；⑤stages 畸形修复耗尽 → repaired 降级续跑交付（Pillar 2，泛型模板方案 + tool_calls repaired 留痕）+ 聚合计费；⑥**客户端断流**（收到首事件即断开）→ 生成任务独立完成、done + Plan 持久化 + 5 行计费（R24「单一持久化路径」首次端点级验证）。
- **钉死的两条实测语义**（此前无文档/无断言）：api_usage 粒度 = **每工具一次调用**（修复链多轮聚合为一条，总量守恒——日预算按求和计粒度不敏感）；understand_intent 畸形耗尽的降级去向是澄清追问而非 error。
- **门禁**：全量 pytest **1421 通过/1 跳过**（+6）+ ruff 绿；前端无改动。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R19（2026-10-17，Python v1 深度审计第十六轮「iter 376」，✅ 完成）**：同一 /goal 第十六遍——**前端供应链审计（R10 pip-audit 的对称面）+ 全局错误恢复核查**。20 项漏洞分派，运行时可达项全部修复（20→11，余项均破坏性升级留档）；1 commit：

- **审计口径**：本机 registry 为 npmmirror（audit 端点未实现）——`npm audit --registry=npmjs.org` 直连官方；本地 npm 缓存有一条损坏内容项（rehype-katex tarball ENOENT），换干净临时缓存目录绕过。
- **修复（运行时可达，随产物发给浏览器）**：①**katex 原型污染绕过 trust 限制（GHSA-238p-pmpm-9mq7）**——用户 markdown 数学公式直达的运行时代码。直接依赖升 ^0.18.10 后，rehype-katex@7/micromark-extension-math 仍各嵌一份**漏洞版 0.16.47**（peer ^0.16 落嵌套，且 rehype-katex 实际加载的是自己那份）——加 `overrides: {"katex": "^0.18.10"}` 强制全树去重（npm 规则：直接依赖 spec 须与 override 一致，同步升）。烟雾实证：renderToString 正常产出、`\href{javascript:}` 在 trust 默认关下不产出 `<a>`（渲染链本就 sanitize→katex 双保险）；②react-router-dom 6.26→6.30.6、postcss→8.5.30、browserslist/nanoid/baseline-browser-mapping/source-map-js（非破坏性 audit fix 一并）。
- **留档豁免（11 项，全部需破坏性升级，可达性已核）**：tailwind≤3.4.19 链（braces/micromatch/fast-glob/chokidar/postcss-selector-parser/nested，6 high+1 mod）——**纯构建期**，攻击需向构建管线喂恶意 glob/选择器（输入全在仓库内），v2 前端重做时升 tailwind 4；vite≤6.4.2/esbuild（mod，dev server 请求窃取）——**仅 dev server**，vite 配置无 host 覆盖（默认仅 localhost），生产走 FastAPI 静态托管；react-router 6（2 mod：backslash 开放重绕 + SSR 反序列化）——SSR 不适用（纯 SPA）；开放重绕**本应用不可达**：全部 `to` 为静态串、唯一动态 `navigate(from)` 的 from 来自 `location.state`（应用内编程导航才可设置，URL 无法注入）且值域为站内 path+search、handleUnauthorized 走静态 '/login'——v7.18.4 迁移留 v2 前端。
- **全局错误恢复核查**：Layout 页面级 ErrorBoundary（iter 273）已在——顶栏/侧栏不随页面崩、resetKey=路由路径切换自愈、fallback 带重载/回首页；插件 overlay/小组件各有隔离边界。无白屏缺口，零改动。
- **门禁**：前端 tsc+vite build 绿（3.56s）；后端本轮零改动（工作树仅 frontend/package*，R18 的 1415 通过仍有效）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R18（2026-10-17，Python v1 深度审计第十五轮「iter 375」，✅ 完成）**：同一 /goal 第十五遍——**全路由授权矩阵审计（新证据类别）**。R4-R17 从未系统核对过「158 条路由每条的认证级别与所有权 scope」。2 个真实修复、4 个新回归、1 份可复跑矩阵脚本：

- **矩阵提取 `tests/auth_matrix.py`**：lifespan 内递归展开路由（R17 工具复用），按 dependant 依赖匹配 require_admin/require_auth/get_current_user 分级，handler 源码静态所有权特征（user.id/get_owned_…/resolve_owner）辅助。分布：**PUBLIC 6 / USER 139 / ADMIN 13**。
- **PUBLIC 6 全部白名单内**：auth/login、health、shared/{token}（R15/R16 已审计）、setup/status（仅 4 个布尔标志）、setup 与 setup/dismiss——后者经 `_setup_caller`（resolve_user→非管理员 403）+ 10/min IP 限流，**R7 期 iter 367/368 已闭合接管窗口**，本轮复核确认。
- **ADMIN 13 核对**：users CRUD、plugins 热管理、mcp_bridge tools/invoke、provider_bridge PUT config、webhook set/del config——与「全局 admin 管理」决策（note/12 第四轮）一致；provider_bridge GET config/chat 留 USER 属桥接共享设计（管理员配 Key、用户使用，明文不回显）。
- **USER 139 的 43 条静态无所有权证据项逐一分派**：全部经命名 helper（`_owned_material_or_404`/`_owned_or_404`/`_due_items`(join Session.user_id)/`_owned_thread`/`_owned_run`）或既有家族隔离测试覆盖（sessions/plans/quiz/unit/providers/usage/shares/notes/credentials/kb/logs/threads/active_run 等十余人测试文件点名核对）；`runs/{id}/files/{name}` 读路径经 DB 存档 path 而非用户输入——无路径穿越。
- **真实修复 1（跨用户隐私泄露）**：`study_stats` 插件 storage 为每插件全局 KV——**所有用户的学习分钟/答题数/连续天数聚成一份，任意认证用户经 /dashboard 可见他人学习数据**（docstring 却宣称「我全局学了多久」＝表达与实现不一致）。三类事件 payload 均带 user_id（发射点核对），改按 `days:{uid}`/`quiz_count:{uid}` 等键分用户记账、dashboard 只读本人、scheduler 逐用户重算 streak；旧全局键弃置不迁移（v1 演示插件）。v0.1.0→0.2.0。3 新回归：双用户互不可见 / 无 user_id 载荷不混入真实用户视图 /（见下）。既有 16 用例补齐事件 user_id 对齐仪表盘用户。
- **真实修复 2（运营情报暴露）**：`security_guard /stats` 返回全站路径→频次画像，对普通认证用户属侦察辅助——收紧 `require_admin`（插件本身即运维工具），v0.1.0→0.2.0，新增非管理员 403 + 管理员 200 回归。
- **门禁**：全量 pytest **1415 通过/1 跳过**（+3）+ ruff（app/tests + 根 plugins/）全绿。前端无改动（两插件端点前端零消费已核实）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R17（2026-10-17，Python v1 深度审计第十四轮「iter 374」，✅ 完成）**：同一 /goal 第十四遍——**前后端契约系统性 diff（新证据类别）**。动机：R16 发现 api.ts 的 share 类型是陈旧的（加 expires_at 前没同步），说明「手写 TS 类型 ↔ 后端真实返回」这道缝从未被系统性核对过——tsc 信任声明，幽灵字段=运行时 undefined 解引用、缺字段=能力不可见。结论先行：**零真实契约 BUG**（干净账单）。1 commit、0 产品代码改动、3 份可复用审计脚本入库：

- **Side A 后端提取器 `tests/contract_audit.py`**：lifespan 内递归展开 FastAPI 0.139 惰性 `_IncludedRouter` 得 158 条路由（×方法），AST 提取返回形状（literal dict 键并集 / pydantic `model_dump()` 字段 / 同模块+跨模块 helper 跟进深度 2 / `x: dict = {...}` AnnAssign / `return await` / `asyncio.to_thread(helper)` 解包 / 多分支 kind 优先级合并）——154/158 可比形状（12 response 流、1 none、3 多态混合豁免）。
- **Side B 前端解析器 `tests/contract_frontend.py`**：解析 api.ts 61 接口 + 107 个 `apiFetch<T>` 调用点（模板串/拼接/方法提取），类型解析支持内联对象/联合分支并集/接口引用嵌套。
- **Side C 双向 diff `tests/contract_diff.py`**：URL 模板归一（`${x}`→`{}`、剥查询串、条件查询尾 `{}` 剥离、无 method 默认 GET 回退）后比对幽灵端点/幽灵字段/遗漏字段（含嵌套一层）。终版报告 10 行**逐项代码级分派**：AuthUser×3=接口在 auth.ts（跨文件误报）、SessionStatusDetail=type 别名交集（含 clarify_questions）、getPlan `unknown`+三处消费 cast 均正确取 `.plan`/`.session_id`、export 幽灵 4 项=提取器 AnnAssign 缺陷（已修，ics/event_count/html/slide_count 后端实发）、export 遗漏 chars/bytes_b64 与 progress node_id=加性字段 UI 未用（无害）、quiz/wrong `warning`=与 generate 共型且 `?? ''` 兜底、pricing PUT `note`=消费方只读 table/defaults 且立即 refetch GET、credentials=三元实参解析器不支持（后端实有 GET /credentials）。
- **补盲区人工核**：SSE 事件名 `quiz_ready` 双端一致（plans.py:2615 发射 / QuizModal 订阅；插件事件面走 API.md 自有契约 R12 已审）；`live` 载荷 5 键（current_tool/step_index/total_steps/cost/last_event_id）双端一致；`latest_plan/error` 嵌套一致。
- **审计工具自身修了 6 个缺陷**（惰性路由展开、嵌套函数 dedent、Await/to_thread 解包、AnnAssign、TS 泛型 `<>` 与引号的字段切分保护+mask、kind 优先级）——工具缺陷会产出假阳/假阴，逐项以代码复核后才下「干净」结论（不编造）。
- **门禁**：全量 pytest **1412 通过/1 跳过**（与 R16 基线一致；一次 1411/2 为 `test_real_url_reachable_or_skip` 真网络探测的外网可用性波动，-rs 复跑确认）+ ruff 全绿。前端无改动（本轮零产品代码变更）。
- **价值**：R16 类「陈旧类型」缺口被系统性排除（107 调用×158 路由全量核对），且审计脚本可复跑——后续改 API 时可作契约回归工具。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R16（2026-10-17，Python v1 深度审计第十三轮「iter 373」，✅ 完成）**：同一 /goal 第十三遍——**闭合 R15 自己引入的「表达与实现不一致」**：share 加了 90 天过期但前端零呈现（属主不知链接何时失效、到期后静默 404），且审计发现两处过期后续语义缺口。3 commit、3 个新回归测试：

- **后端缺口 A（死链回发）**：`create_share` 对既有**已过期**链接原样沿用——前端「分享」按钮（get_share 过期即 shared=false，按钮回到『分享』态）触发的正是该路径，用户会拿到 201 + 一条公开 404 的死链。修复：过期则**轮换 token + 重置 created_at**（90 天重新计时）：旧 token 即使已被复制到别处保持失效，属主拿到可用新链接；未过期链接幂等语义不变（同 token 原样返回，避免每次点「复制分享链接」意外换 token 作废已分享出去的链接）——两种路径各有回归钉死。
- **后端缺口 B（管理面失实）**：`/shares` 列表把过期链接与活跃链接混列（Settings 卡文案称『任何人无需登录即可查看』对过期链接为假，且与 get_share 的 shared=false 互相矛盾）。修复：逐条加性 `expired` 标记（行保留可撤销清理，服务端判定不做客户端时钟推算）。
- **前端三面呈现**：① Settings 分享卡——条目展示失效日期（`至 YYYY-MM-DD`/`永久有效`）、过期条目「已过期」徽标 + 行置灰 + **隐藏复制按钮**（复制死链是陷阱）、保留撤销供清理、卡文案改述 90 天默认；② PlanDetail——分享/复制消息带有效期（`链接已复制，有效期至 …`）、已分享方案常驻有效期提示（打开即见，不再只在复制瞬间出现）、**过期态显式提示**『上次的分享链接已过期失效，再次分享将生成新链接』（此前静默变回未分享，属主不知旧链为何打不开）；③ api.ts 类型补 `expires_at`/`expired` + `shareExpiryText` helper（naive UTC 串直接截取展示，与既有 created_at 口径一致，不做本地时区 Date 换算——午夜边界会漂一天）。
- **门禁**：后端全量 pytest **1412 通过/1 跳过**（+3）+ ruff 全仓；前端 tsc+vite build 绿（本轮前端改动）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R15（2026-10-17，Python v1 深度审计第十二轮「iter 372」，✅ 完成）**：同一 /goal 第十二遍——**长跑模拟 + 三项安全遗留处置**。4 commit、4 个新回归测试：

- **长跑模拟（`backend/tests/soak_longrun.py` 手工脚本）**：组合 8 用户风暴 ×90s × 加速维护 5s/轮（跨 ~18 个维护周期叠加负载），**14498 请求零 5xx**；内存 6MB→6MB（前后 1/3 窗口均值漂移 +0MB）、句柄 75→76（Δ+1）——无累积泄漏；DB 增长 0.09MB、**-wal 峰值 1.2MB 且收尾被 checkpoint 截断回 0 字节**；负载中并发双导入 [200,409] 互斥仍成立、200 天种子行在负载窗口内被裁剪（维护与负载互不干扰）；关停后 integrity/foreign_key 干净、threads 落库 480 与成功响应计数一致。指标口径经 tasklist 交叉验证（ctypes GetProcessMemoryInfo 与系统一致）。
- **CSP 落地（销 R5/R8 遗留「CSP 刻意不加」）**：全站 `Content-Security-Policy`——v1 插件 inline 注入所需的 script/style `'unsafe-inline'` 保留（已核全仓零 eval/new Function，**不含 unsafe-eval**），其余全收紧：**connect-src 'self'（XSS 后数据外带被拦）**、object-src 'none'、base-uri/form-action/frame-ancestors 收口、媒体 self+blob（KB 预览）、图片放行 https:（用户 markdown 外链图为被动内容）。**真浏览器实证**：起服加载 SPA，设置向导完整渲染（3 JS+1 CSS 全载）、`securitypolicyviolation` 事件零条——CSP 不破坏既有功能。回归并入安全头测试（413 短路也带头）。v2 前端（无 inline 注入）再收紧 script/style。
- **share 过期默认（销 R5 遗留「share 永久链接」，**待追认缺省 90 天**）**：`SHARE_DEFAULT_EXPIRE_DAYS`（默认 90，0=恢复永久）——读取时按 created_at+天数判定（零迁移）；公开端点过期与不存在**同形 404**（不向探测者泄露存在性）；属主查询面回传 `expired` 标记 + 全响应面加性 `expires_at`；3 回归（默认过期/0=永久恢复并即时生效/创建响应 90 天口径）。
- **transformers CVE 复核（豁免留档，v2 自然销项）**：2026-10-17 实查——修复路径存在（marker-pdf 2.0.0 要求 transformers≥5.12+surya≥0.22+torch≥2.7，四包大版本迁移）；但 transformers 在本服务**仅经 marker_single 子进程 CLI 触达**（parse_material.py:380 `shutil.which`+subprocess，服务进程零导入、仅认证用户 full 档 PDF 解析触发、上传 50MB 上限+parse 限流），且 note/12 决策 v2 砍掉整个 OCR 栈（PDF 用 Go 真实现）——v1 生命周期内迁移无回报窗，维持豁免。
- **门禁**：后端全量 pytest **1409 通过/1 跳过**（+4）+ ruff 全仓；前端无改动。
- **遗留（更新）**：~~CSP~~（R15 已上，script/style 收紧留 v2）、~~share 过期默认~~（R15 90 天待追认）、币种字段改名（待追认）、SQLite 专有日期函数、事件断线游标（v2 #60）、transformers CVE（豁免留档 v2 销项）、GET /notes 无分页（30MB 上界接受）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R14（2026-10-17，Python v1 深度审计第十一轮「iter 372」，✅ 完成）**：同一 /goal 第十一遍——**维护任务真实调度实证**（此前 maintenance 只有函数级单测，lifespan 拉起的 `maintenance_loop` 周期调度层从未在真实进程下验证）。2 commit、0 新自动测试（实证走手工脚本）：

- **节奏旋钮化（行为保持）**：`maintenance_interval_seconds` / `orphan_grace_seconds` 入 Settings（默认恒 86400=每日/24h，语义不变）；maintenance.py 模块常量改读 settings——兼作运维调节旋钮与实进程验证的加速驱动口；.env.example 补模板行。
- **真实调度四实证（`backend/tests/soak_maintenance.py` 手工脚本，加速 3s/轮 + 8s 冷却）**：真实 uvicorn 子进程两阶段（起服建 schema → 离线种子 → 加速节奏起服观察循环）。①**WAL checkpoint(TRUNCATE) 由循环真实触发**（日志「WAL checkpoint(TRUNCATE) 完成」+ 事后 `-wal` 0 字节截断实证）；②**prune_api_usage 按 API_USAGE_RETENTION_DAYS=180 裁掉 200 天旧行、保留新行，且幂等**（裁剪日志恰出现一次——后续轮次 0 删不再记）；③**孤儿目录冷却双向语义**：陈旧孤儿（mtime 3 天前）首轮即删（恰一次）、新鲜孤儿 8s 冷却窗内不删/过期后由后续轮删除、存活会话目录（行在、mtime 30 天前）多轮永不删——「行在则不删」与冷却窗各自独立生效；④服务面全程健康 + 关停后 `integrity_check=ok`。首跑即全绿（+5.9s 前两轮四命中、+11.3s 到期侧、日志幂等计数、-wal 截断）。
- **门禁**：后端全量 pytest **1405 通过/1 跳过** + ruff 全仓（含 maintenance/settings 改动回归：test_maintenance/test_iter368_stability/test_iter370_usage_pruning 全绿）；前端无改动。
- **遗留（承前未变）**：CSP（v2）、币种字段改名（待追认）、share 过期默认、SQLite 专有日期函数、事件断线游标（v2 #60）、transformers CVE（marker-pdf 锁）、GET /notes 无分页（30MB 上界接受）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R13（2026-10-17，Python v1 深度审计第十轮「iter 372」，✅ 完成）**：同一 /goal 第十遍——**读路径收尾 + 全量门禁偶发牵出的真并发 BUG**。3 commit、2 个新回归测试：

- **thread 记忆列表两端收敛**：GET /threads/{id}/memory 此前无界全量返回（2000 条/用户配额 × 20000 字符/条上限最坏 ~40MB 单响应），而 Chat 页记忆面板只是 160px 速览小窗且 `items.map` 全量渲染——后端封顶最近 200 条 + `total` 全量计数（加性扩展），前端只渲染最近 50 条并如实显示总数（「仅显示最近 50 条（共 N 条）」）；API.md §11.2 行勘误注记。回归锁死封顶数/total 口径/updated_at 倒序。
- **插件存储 Windows 共享冲突丢写修复（全量门禁偶发失败牵出的真 BUG）**：全量套件偶发 `test_storage_namespace_concurrent_writes` 失败 → 独立连跑 **2/5 复现**，捕获 `PermissionError(13)` 从 set() 逃逸——StorageNamespace._save 高频同文件 write+replace 下 Windows 瞬时共享冲突（杀软/索引器扫过目标文件）令插件该次写入丢失且异常上抛。补 10-50ms 短重试窗口（与 plans 溢出 JSON 的 os.replace 重试同法，连续 5 败才上抛）；修复后连跑 **8/8 全绿**。教训：全量套件的偶发红不是噪音，先复现率实证再定性。
- **其余读热点核查（结论：干净）**：kb chats 列表 offset/limit 分页 ✓、messages limit≤500 ✓、GET /api/memory（user 作用域）分页 ✓、runs/steps/artifacts id 型有界 ✓、progress 统计 R6 已 SQL 聚合化 ✓。
- **R13 追加（同日第二轮）**：①user 级 GET /api/memory 复查发现同为无界全量（前端无消费方、插件 REST 代理可达）——与 thread 级同口径封顶 200 + total，回归并入 test_iter372_thread_memory_cap；②R11–R13 全部改动后**真实进程 soak 复跑全绿**（1157 请求零 5xx、双导入 [200,409]、崩溃重启恢复、优雅关停、integrity/foreign_key 零违规；net_err=8 为风暴截止时刻客户端侧中断计数，非服务错误）。
- **门禁**：后端全量 pytest **1405 通过/1 跳过**（+1）+ ruff 全仓；前端 tsc/vite build 全绿（R8 后首次复跑）。
- **遗留（承前未变）**：CSP（v2）、币种字段改名（待追认）、share 过期默认、SQLite 专有日期函数、事件断线游标（v2 #60）、transformers CVE（marker-pdf 锁）、GET /notes 无分页（30MB 上界接受）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R12（2026-10-17，Python v1 深度审计第九轮「iter 372」，✅ 完成）**：同一 /goal 第九遍——**契约一致性终检 + 读路径热点下推**。3 commit（含 1 处性能修复）、2 个新回归测试：

- **API 契约终检（结论：干净，零修复）**：以 openapi()（lifespan 装载后 145 端点，含 /api/tools、/api/plugins、providers/kinds、events/stream、双 formats 等惰性挂载面）为权威面，对 API.md 做双向差集——澄清 API.md 自我定位为**插件 API 契约**（头部第 7-10 行明示），核心 REST 129 端点不属其范围（v1 无核心目录文档属设计现状，v2 目录为 plan/reasonix/03）；其自身声称面全部核实：§11.2 记忆族压缩表格按行语义（POST→集合/GET→两者/DELETE→{mid}）逐条命中实际路由、11 条「幽灵」全为解析器叉积伪差；文档声称参数界（threads page≥1/limit 1-100 默认 20、messages limit 1-500 默认 100 + before_id）与 openapi schema 逐字段一致；§11.3 RunIn 边界承 R6 B17 既有回归。
- **错题族「每题最新 attempt」窗口函数下推（真热点修复）**：wrong-queue / wrong-overview（用户级）与 plans quiz/wrong（方案级）三端点此前把**全部** QuizAttempt 物化为 ORM 对象再 dict 累积——quiz_attempts 为全库增速最快表（每次提交一行、永不清理），每次打开复习页全量重算随历史线性劣化。改为 `ROW_NUMBER() OVER (PARTITION BY quiz_id ORDER BY created_at DESC, id DESC)` 子查询，仅物化 ≤题数 行，排序语义逐字保留（新回归锁死 created_at 主序 + 同刻 id 大者优先两陷阱 + iter 368 最近判错在前截断）。**基准：2 万作答下 wrong-queue 51ms / wrong-overview 32ms**（均 10 次均值）。
- **读放大面核查（结论：有界，零修复）**：review due/queue 服务端已钳 limit≤500、wrong-queue 已钳≤200（openapi 未声明 maximum 但实现安全）；GET /api/notes 无分页全量返回——上界=500 条配额 × 20k 字符 ≈ 30MB（自伤型、认证内、配额封顶），v1 接受记录；runs files/kb 详情等 id 型端点天然有界。
- **门禁**：后端全量 pytest **1404 通过/1 跳过**（+2）+ ruff 全仓；前端无改动。
- **遗留（承前未变）**：CSP（v2）、币种字段改名（待追认）、share 过期默认、SQLite 专有日期函数、事件断线游标（v2 #60）、transformers CVE（marker-pdf 锁）、GET /notes 无分页（30MB 上界接受）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R11（2026-10-17，Python v1 深度审计第八轮「iter 372」，✅ 完成）**：同一 /goal 第八遍——七轮后代码级新发现已枯竭，本轮换**横切面矩阵核查**：全仓写端点 × 限流覆盖矩阵、备份 v2 规模化实证、配置面一致性。3 commit、4 个新回归测试：

- **限流矩阵终检（两处昂贵路径无频控，已修）**：脚本化扫描全仓 27 个 API 文件的全部写端点 × limiter.check 覆盖——31 个未挂限流端点中 29 个为幂等单行写（认证+属主校验覆盖，无需频控，矩阵结论留档），**2 处真实缺口**：①plans 生成族（generate/retry/clarify/regenerate/materials）——并发配额只约束「同时运行数」，快败循环（error 终态→立即重试）每轮 `_load_parsed_materials`+状态写入无频次上界，补 `_gate_generate_start` 共桶 `plans_gen:{uid}` 12/min（materials 仅 LLM 新生成路径计频，幂等重渲染不限）；②kb POST /generate 建会话却不受 POST /sessions 的 sess_create 桶约束（两条路径可叠加绕出 60/min），改共桶——回归实证先经 /sessions 耗尽 30/min 后 kb/generate 立即 429。
- **备份 v2 规模化实证（test_iter372_backup_scale，2 测试）**：既有 v2 回归只覆盖小载荷——本轮补①配额边界软跳过语义（250 线程备份→空用户精确导入 200+50 skipped 留痕；二次导入 room=0 不放大）；②真实端点全量往返 150 线程×1500 消息+8×128KB base64 原件（~1.4MB 载荷）——计数精确、kb 原件字节级一致、消息 run_idx 重映射后 run_id 非空、export+import 合计 2.4s（to_thread 提交链不冻结事件循环的规模化实证）。
- **配置面一致性**：Settings 40 字段 ↔ .env.example 39 键双向差集——唯一缺口 `API_USAGE_RETENTION_DAYS`（R7-F4 新增漏记）已补模板行；app/ 内零散落 env 读取（全走 pydantic-settings 单一来源）✓。
- **门禁**：后端全量 pytest **1402 通过/1 跳过**（+4）+ ruff 全仓；前端无改动。
- **遗留（承前未变）**：CSP（v2）、币种字段改名（待追认）、share 过期默认、SQLite 专有日期函数、事件断线游标（v2 #60）、transformers CVE（marker-pdf 锁）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R10（2026-10-17，Python v1 深度审计第七轮「iter 372」，✅ 完成）**：同一 /goal 第七遍——代码面六轮闭环后换两类收尾证据：**依赖供应链 CVE 分诊升级 + 真实进程级 soak**（此前全部证据走进程内 ASGITransport，无真实 socket/进程边界）。2 commit：

- **依赖 CVE 升级（requirements.txt floors + venv）**：pip-audit 全量分诊——**Pillow 10.4.0→12.3.0**（13 个已知 CVE，含恶意图像解码可达——上传图片/materials 解析为直接暴露面）、pyjwt≥2.15、pypdf≥6.19、urllib3 2.5→2.8、soupsieve（传递）；cryptography 49→50（经 google-auth 传递；应用 JWT 走 HS256 未直接用其算法——不需显式钉）。**豁免并留痕**：transformers CVE（marker-pdf 锁定链条，ecosystem-locked 升级会断 OCR——v2 内核期随上游解锁）；pytest/pip/virtualenv 自身 CVE（开发工具链，非运行时暴露面）。marker-pdf/surya 元数据声称 Pillow<11：实证 import + marker_single CLI + PIL 图像操作在 12.3 下全部正常（pip check 警告已知，注释留痕 requirements.txt:32）。
- **真实进程 soak（`backend/tests/soak_process.py`，手工运行不入自动收集）**：uvicorn 子进程（临时 DATA_DIR/DB，真实 TCP）全链实证——①SPA 托管+安全响应头（真实进程面）；②9 次登录+8 用户 JWT 就绪；③12s 八用户混合读写风暴 **1247 请求零 5xx**（429=限流设计行为，163 次；201 创建 202 次）；④**并发双导入 [200,409]**（_RESTORE_LOCK 互斥竞争路径实证，合法 v2 载荷 format=deep-tutor-backup）；⑤硬杀（崩溃模拟）→同库重启→健康+认证读写恢复（WAL 恢复收敛）；⑥CTRL_BREAK 进程组优雅关停 30s 内退出；⑦事后 `PRAGMA integrity_check=ok` + `foreign_key_check` 零违规 + 行数核对（threads=240 与风暴写入一致）。脚本要点：子进程 stdout 落日志文件（PIPE 不排水可致子进程写满缓冲阻塞，且失败可倒尾诊断）；健康等待 240s 上限+固定间隔轮询防忙等（首跑 60s 超时复盘=依赖升级后首跑 .pyc 冷编译慢，复跑 2.6s 起步——非代码问题）。
- **门禁**：后端全量 pytest **1398 通过/1 跳过** + ruff 全仓；前端无改动。审计用 pip-audit 已从 venv 卸除（恢复运行时依赖面干净）。
- **遗留（承前未变）**：CSP（v2）、币种字段改名（待追认）、share 过期默认、SQLite 专有日期函数、事件断线游标（v2 #60）、transformers CVE（marker-pdf 锁）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R9（2026-10-17，Python v1 深度审计第六轮「iter 371」，✅ 完成）**：同一 /goal 第六遍——文件级覆盖已闭环（R8 后无未审文件），本轮换证据类别：models.py 本体首审 + 模块级状态泄漏面清点 + **高负载/数据完整性从读码结论升为实测**。1 commit、3 个新回归测试（2 类新证据）：

- **热查询四索引（模型+迁移 `b9d2e4f71a35`+探针，schema parity 守护覆盖）**：models 列定义 × 实际查询模式核对出四处线性劣化点——kb_documents(user_id)（知识库列表全表扫）、quizzes(plan_id)、**quiz_attempts(quiz_id)**（错题队列取最新 attempt，全库增速最快表）、learning_logs(plan_id,date) 复合。user_prefs(user_id,key) 原计划同补，核查发现既有唯一约束已自带索引——冗余剔除（教训：加索引前先查唯一约束）。
- **级联完整性实测（test_iter371_cascade_integrity，2 测试）**：构造覆盖全部 24 张用户数据表的完整数据树（sessions/plans/materials/kb/threads/runs/messages/memories/notes/node_notes/prefs/quizzes/attempts/progress/learning_logs/api_usage/双 profiles/share/study_units/study_materials/kb_chat/tool_calls/orchestrator_state），经真实端点删用户逐表断言**零孤儿**；删会话实测 thread.session_id 置空语义。models.py 审计读码结论（ORM delete-orphan 全覆盖）就此有了端到端证据。
- **多用户并发压测（test_iter371_concurrency_burst，1 测试）**：ASGI 级 8 虚拟用户 × 6 回合并发读写 + 无效 token 攻击者风暴——零 5xx、每用户写入计数精确一致、攻击者仅 401/429；3.4s 常驻套件。测试侧发现并修正：开放本机模式下无效 token 被设计放行，压测需 monkeypatch passwordless_open 才能真实考核认证失败路径。
- **模块级可变状态清点（干净面）**：全仓 27 处模块级 dict/list/set 逐一核界——运行时注册表（plugins 五族）unload/reset 清理（R6 B15 已硬化）、缓存四族（events sid 60s+上限清空/plan topic 60s+FIFO/verify_url 4096 FIFO/robots 有界）全有 cap+TTL、任务引用三族（closing_tasks done-callback/scheduler 完成即移除/RUN_STREAMS 释放即删）自清理、常量可变类型（PIPELINE/CODE_STATUS 等）只读——**无长跑泄漏面**。
- **门禁**：后端全量 pytest（+3 测试）+ ruff + 前端无改动。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。


**iter R8（2026-10-17，Python v1 深度审计第五轮「iter 371」，✅ 完成）**：同一 /goal 第五遍——四轮后建文件级覆盖矩阵定残留面：从未直接审计的「main.py 装配层、tools 后半、前端未审组件批」。方法：自审 main.py 暴露面核心 + 全端点限流矩阵核对 + 两路并行 Explore（后端 19 文件 / 前端 27 文件，均附干净清单）→ 12 发现全修（1 中危逻辑 + 1 中危交互 + 10 低/加固），2 commit、10 个新回归测试：

- **B21 后端（`a7bb012`）**：**全站安全响应头**（nosniff/X-Frame-Options DENY/Referrer-Policy no-referrer，外层注册覆盖 413 短路；CSP 因 v1 插件注入体系遗留 v2）——暴露服务此前无点击劫持/MIME 嗅探基础防护；**备份导入往返断裂修复**（v2 备份含 kb 原件可超通用 body 上限 ~55MB，multipart 先被通用 413 拦下、端点自限 256MB 形同虚设——两端点豁免通用限，58MB 回归实证）；插件热管理三端点补 20/min 频控；402 消息币种对齐 CNY；**understand_intent deadline 顺序错位修复**（过期判定挪到 args 权威覆盖后对最终值统一执行——表单合法日期被虚假暂停追问、表单过期日期绕过策略两向错位，4 场景回归）；export_pdf data: URI 解码防护+10MB 上限；export_plan 落盘移 to_thread；build_anki deck id 模数 10 万→9000 万。
- **B22 前端（`15bd2aa`）**：**document.title 三层合成**（lib/title.ts：base/badge/timer——TaskCenter/FocusTimer/PluginPageHost 三方直写互踩：计时结束恢复陈旧假角标/倒计时覆盖角标不恢复/角标覆盖插件页标题）；**SSE 整流三行尾**（裸 CR 流此前事件全部静默丢弃 + BOM 剥除 + 空 data 块不派发）；StudyMaterialReader 目录改 DOM 派生（与跳转同源，修引用块/缩进标题跳错位）+ rewrite 复位 finally 收口；Timeline 非法日期过滤（「第 NaN 周」）；Plugins busy 全行禁用；SharedPlan token 切换竞态守卫；HomeKbGenerate 截断提示。
- **干净面记录**（agent 附核实点，后续轮免重复审）：后端 19 文件中 15 干净（build_knowledge_map/design_stages/record_progress/schedule_plan/validate_plan/plan_assembly/registry/budget/locks/logging/setup_state/storage/db.base 等——详见当轮报告于 git 历史）；前端 27 文件中 19 干净（Markdown XSS 面重点核查无通道、KbChatPanel/Toast/TaskPanel/插件宿主四件等）。
- **门禁**：后端全量 pytest + ruff + 前端 tsc/build 全绿。
- **遗留新增**：CSP（v2 前端再上）；SSE 裸 CR 实际不可达（后端只产 LF/CRLF，防御性修复）；FocusTimer 计时与角标并存时角标暂隐（分层渲染的自然取舍，计时结束恢复）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。


**iter R7（2026-10-17，Python v1 深度审计第四轮「iter 370」，✅ 完成）**：R6 收口后按指令处理遗留 8 项产品级决策（AskUserQuestion 未获答复 → 按推荐缺省推进、全部标注**待追认**），并行启动第四轮审计（4 路后台 agent：scripts 启动链路 8 条 / docs 一致性 21 条 / 前端残留 20 条 / 后端残留 3 条+大面实证干净清单）→ 修复 5 commit、21 个新回归测试：

- **缺省决策（待用户追认）**：①备份补齐全量 v2 格式（五类表+原件内嵌，兼容导入 v1）；②币种可见面统一 CNY 标注（前端已实证全链￥，仅补文档口径；字段名不改——改名需迁移，若追认可另起）；③备份导入维持单事务（不改——分块提交语义=产品级取舍）；④api_usage 自动裁剪默认 180 天（`API_USAGE_RETENTION_DAYS` 可配，0=禁用）。
- **F1 备份 v2（`1aab467`）**：导出格式升 version=2——threads（runs/messages 索引互链，非终态 run 导出为 interrupted）+ memories（thread 索引）+ kb_documents（解析文本内嵌 + 原件 base64，单件 ≤32MB 超限跳过留痕）；恢复端 append-only + id 重映射 + 配额（threads 200/runs 2000/messages 5000/memories 2000/kb 200）+ kind/状态白名单 + base64 validate（损坏降级文本并留痕）；v1 字典兼容导入；version>2 拒绝；preview 补 5 类计数。销 R6 遗留②。
- **F4 用量裁剪（`16dafcc`）**：maintenance 日任务增 prune_api_usage（naive UTC 截断，删失败 rollback+告警不中断），settings.api_usage_retention_days 默认 180。销 R6 遗留⑦。
- **B20 后端残留（`5c3f012`）**：**quiz 生成 WAL 快照冲突数据丢失修复**（plans._generate_quiz_spec 持读快照跨 LLM await → BUSY_SNAPSHOT 写失败丢 quiz；端点 inflight 轮询同理——两处 LLM 等待前 rollback）；计量族 8 处同步 commit 移 to_thread（plans 材料/HTML 导出/kb_answer/materials/exports/plugins 三处）；**provider 归属链闭合**（LLMProvider.kind 类属性 → PluginProvider 承接 entry.kind → build_metadata(provider=) → ToolMetadata.provider → api_usage.provider 落库；agent_loop/loop/search_resources 全调用点接通）销 R6 遗留⑧；**init_db↔alembic schema 等价守护测试**（create_all 库与 upgrade head 库全表结构快照比对，防迁移链漂移）销 R6 遗留⑤。
- **B19 前端残留（`ed08e31`）**：计费表保存前本地校验+保存后服务端回读（不再假设写入即生效）；预算空串拒绝/设 0（=不限）confirm+展示区分；422 校验错误字段级明细透传；**EventsBridge 干净 EOF 不清零 attempt**（后端 250ms-1s 反复断流时全速重连风暴）；taskEngine boot 重试仅在有 token 时调度；Usage/SharesCard 错误态可重试；HeaderSearch 卸载清理；**验证卡 onDone 等写库确认后再收卡**（答对≠已落库，表达与实现一致）；ProviderManager 未知插件协议徽标+输入框 maxLength；Search q 截断 100。
- **B18 文档/脚本（`9b857ea`）**：README 坏启动命令修复（backend 无 `__init__.py`，`backend.app.main` 不可导入——实证后改 `cd backend && uvicorn app.main:app`）+ 插件计数实证 8 内置+1 示例；vite 代理目标 VITE_API_PROXY_TARGET 可配（loadEnv 实现）；**start 脚本零配置引导态默认仅绑 127.0.0.1**（开放模式设密码前免认证，0.0.0.0=局域网可认领；HOST 可覆盖）——R5 遗留「开放模式+0.0.0.0」的脚本层根治；ps1 浏览器改健康检查通过后再开；.env.example 补 4 并发键+死链+bing 措辞+CNY 标注+VITE 段；API.md 九处契约补遗（§11.2 ThreadIn 约束/分页、§11.3 RunIn 边界与 409/503/402+message_start、§8.4 门面表 api.memory 行、§8.6 台账字段、§8.4g 并发措辞、PageDef.override+版本表 1.16.0 行、usage CNY 注记）；plan/reasonix/03 备份 409/413 语义；**门面记忆写入硬化**（save_memory 此前无任何限额且 thread_id 不验归属=跨用户注入；add_message 无界刷行——补 REST 同口径限额，7 回归测试）。
- **门禁**：后端全量 pytest **1385 通过/1 跳过**（较 R6 +21）+ ruff 全仓 + 前端 tsc/build 全绿。
- **遗留销项对账**：R6 八项——②⑦⑧已修，⑤守护闭环，①可见面已标 CNY（字段改名仍待追认），③维持单事务（裁决缺省），④⑥仍遗留；R5 四项——开放模式绑定已脚本层根治（后端仍按 HOST 直绑，部署面收敛），share 过期默认/daily_budget=0 共享语义/CSP 仍遗留；**新增**：EventsBridge 事件断线重连无游标（错失窗口=设计级，v2 事件流 #60 一并设计）。
- **R7 追加减项（同日，`51a42e9`）**：④插件 override 表达一致收口——可覆盖路由白名单（未接线值注册即抛 Error，不再静默不生效）+ plans/learn 详情页粒度独立键（前端插件 API 升 1.18.0）；前端信息级低项重扫 20 条 → 速修 14（定价行 index key 焦点错位真 BUG、LearnIndex 错误分流错位、MaterialsPanel 解析 0 字文案矛盾、今日待复习直达 /review、6 处 role=alert/busy 文案、对比弹层 Esc、Setup 跳过 busy、页脚 MVP 文案等）/遗留 6（KbDocsView Esc 绕栈重构、getPlan 类型化、兼容面死导出——均无数据风险）。4 项缺省决策已两次 AskUserQuestion 提交裁决（自主模式未获答复），维持推荐缺省与待追认标注。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R6（2026-10-17，Python v1 深度审计第三轮「iter 369」，✅ 完成）**：同一 /goal 第三遍——两轮后残留集中在「长跑路径的资源/记账完整性、工具面一致性、插件热管理、前端可达性」。6 路并行审计 agent（导出与工具面/LLM 供应与计费/备份与统计/插件与 skills/chat-run 生命周期/前端三轮）产出 ~70 条发现 → 去重核实（Windows pyc 陈旧 8 次实证 6 复现、伪造 robots 头、备份恢复窗口等先实证再修）→ 6 批次逐批「修复→回归测试→套件验证→commit」。共 **7 commit、~40 项修复、35 个新回归测试**：

- **B12 导出与工具面（`470609b`）**：导出族共享限流（export:{uid} 12/min——plans/materials 双端点此前无速率限制）；zip/md/HTML 导出渲染与读盘移 to_thread；HTML 导出逐单元 llm_task_slot + rollback 先行（长导出持读快照）；导出文件名控制字符清洗（\x00-\x1f 防 Content-Disposition 头注入）；robots.txt 流式读取 64KB 上限（Content-Length 伪造可爆内存）；Bing 域名校验收紧（host 后缀匹配——evilbing.com 此前可过）；process-wide search_fetch_slot(2) 全局出站抓取界；ICS 日重复 UID 后缀（同日多事件互覆消失）；修复 html 导出尾日志 NameError。
- **B13 LLM 供应与计费（`b79d604`）**：Anthropic 客户端 OrderedDict LRU（32 上限——每请求新客户端泄漏连接池）+ 显式 httpx.Timeout（默认 5s 连接池超时在长流下误杀）；流对象 finally close（断连/异常路径 SDK 连接不归还）；流中断部分 usage 补记（message_start 已计费 token 此前成本黑洞）；**agent_loop 硬取消路径记账**（task.cancel 打断流式读取时已产生费用照入账，挂 partial_usage 传播）；check_daily_budget 本地时区日界（UTC 切日致用户午后预算翻倍）；api_usage(user_id, created_at) 复合索引（日预算求和全表扫）+ alembic e8c4a1f63b02；get_active_profile 按 id 排序（同用户多 active 行不确定性）+ 失败日志。
- **B14 备份与统计（`ee1ac50`）**：备份导入 threading.Lock 互斥（并发导入交叉写库）+ 恢复提交移 to_thread + 导出 FileResponse+BackgroundTask 流式（256MB json 此前整载内存）；恢复溢出 JSON 原子写；progress 统计 SQL 聚合化（Python 内存聚合全表扫）+ 方案主题 60s TTL 缓存（每行统计重复 _load_plan_content）。
- **B15 插件与 skills（`701bc1c`）**：**Windows pyc 陈旧根治**（同时间戳双写致 pyc mtime==源 mtime，SourceFileLoader 服务陈旧字节码——8 次实证 6 复现；unload/reload 删 `__pycache__`）；reload failed 状态可恢复（此前一次失败永久卡死需重启）；插件 storage 跨线程锁 + 唯一 tmp 名（并发写丢更新/临时文件碰撞）；包插件子模块 sys.modules 全清；三注册表（parsers/exporters/providers）displaced 栈回填（unregister 恢复前驱而非掏空）；PluginProvider 处理器 300s 超时（挂死 handler 永久占 llm 槽）；webhook 句柄幂等注销台账；SKILL.md 64k 截断；check_zip_safety 公开别名（epub 插件改用统一防护）；文档热管理措辞修正（新插件首次发现需重启——API.md/plugins README 此前承诺可热加载）。
- **B17 chat/run 生命周期（`2560422`）**：**硬取消记账闭合**（CancelledError.partial_usage——此前空 usage 覆盖把已入账 run 的 budget_used 抹 0）；_finalize_chat 抗二次取消（final_status 预置修 UnboundLocalError——终态 SSE 永不发/流泄漏/db 泄漏三连；workspace commit shield 化）；嵌套 finally 保 db.close；硬取消抽 _cancel_run_stream 三处共用（delete_thread/delete_user 此前仅软取消——删完行后台 run 仍烧 token 最长 300s）；ThreadIn kind 白名单+长度上界、RunIn max_steps 1..200（此前可传 10^9 单 run 跑数小时占全局槽）；agent_tools 六工具 DB 提交/MB 级文件读写移 to_thread；**memory_save 工具补 REST 同口径配额族**（60/min、20000 字符、2000 条/用户——模型自写路径此前不设防，长跑 run 可刷爆 memories 表；检查顺序对齐 REST：长度→限流→配额）。
- **B16 前端三轮（`a3e9888`）**：Chat 会话列表无限查询+「加载更多」（此前仅首页 20 条，更早会话永久不可达）；消息「加载更早」（before_id 翻页+视口位置保持）；自动滚动改贴底跟随（上翻阅读不再被强拉回底）；删除/重命名补 catch+busy；MessageBubble memo；**跨标签登出闭合**（storage 事件清 react-query 缓存 + 任务台账内存清空 + clearAuth 补清搜索历史）；LearnIndex/Clarify/Wiki 笔记失败态显式呈现（此前报错伪装成「无数据」或永挂骨架屏）；Plans 三处删除补 plans 前缀失效；UserManager minLength 4→8 对齐文案+仅创建成功清表单；插件皮肤注册时恢复 dt_skin 保存值；后端插件 enable/reload 按装载状态分流提示（HTTP 200 但装载失败不再 toast 成功）。
- **迁移探针补丁（`1f9183f`）**：e8c4a1f63b02 补 stamp 特征探针（B13 误判无需探针——当前模型 create_all 库被 stamp 落后一版，幂等迁移掩护下版本表失真）。
- **门禁**：后端全量 pytest（1364 通过/1 跳过；两支旧回归断言随本轮有意变更同步更新——ICS 新 UID 形状、客户端缓存上限 8→32 常量化）+ ruff 全仓 + 前端 tsc/vite build 全绿。
- **遗留（产品级决策，未动待用户定夺；承 R5 四项之外新增）**：①计费币种——cost 全链按 CNY 计算但字段名/前端文案写 USD（budget_usd/daily_budget_usd），改名或换算需迁移与文案双改；②备份 v1 覆盖面——kb_documents/threads/messages/runs/memories 及 uploads/kb 原件不在导出内（恢复后这些数据丢失，无提示）；③备份导入单事务长写锁——大备份恢复期间全库写锁（分块提交语义=产品级取舍）；④插件 override 路由粒度——plans/learn 整页替换吞详情页路由、chat/search 静默不生效（需细粒度路由注册 API 设计）；⑤init_db create_all 掩盖迁移缺失（新库不跑 alembic 链，模型与迁移漂移无告警）；⑥func.date(...,'localtime') 为 SQLite 专有（换 PG 需改写，v2 内核期一并处理）；⑦api_usage 无保留策略（长期运行无限增长，需定期归档/裁剪产品决策）；⑧api_usage.provider 恒写 'anthropic'（plugin provider 计量归属失真）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R5（2026-10-17，Python v1 深度审计第二轮「iter 368」，✅ 完成）**：同一 /goal 复审——第一轮（iter 367/R4）覆盖 plans 状态机/数据完整性/Agent 逻辑/基础稳定性/基础安全/前端主页面，本轮 6 路并行审计 agent 深查**未覆盖面**（认证与用户体系/分享与凭证/测验与进度回写/存储与并发原语/插件子系统/前端二轮）产出 ~60 条发现 → 去重核实（关键论断先实证再修，如 httpx 头限制、wrong_ids dict 插入序、_store 事务范围）→ 5 批次逐批「修复→回归测试→套件验证→commit」。共 **5 commit、~35 项修复、17 个新回归测试**：

- **B7 认证与 API 面（`128e614`）**：认证失败 per-IP 限流（仅失败计数，30/min）+ 失败日志（三出口统一 _gate_auth_failure）；非 ASCII Bearer 头 500→401（compare_digest 字节级）；**改密吊销该用户全部 active PAT**（users/setup 两路径——泄漏令牌此前在改密后无限期有效）；密码 min 4→8（前后端一致，存量不受影响）；登录 per-IP 聚合桶（轮换用户名绕 ip+username 桶白耗 pbkdf2）+ 用户名哈希脱敏日志；JWT_SECRET 弱值告警；providers 写限流+每用户 50 上限；notes/knowledge 读写导出限流；计费表 NaN/inf 拒入 + 条目 200/名称 128 上限；/setup+dismiss per-IP 限流（拉长开放模式接管竞态窗口）。
- **B8 数据正确性（`4480bf2`）**：**/quizzes/wrong-queue 截断偏置**（wrong_ids 按 dict 插入序=quiz_id 升序截断，最近判错被无差别丢弃→按最新 attempt id 倒序后再截断）；choice 空 answer ""=="" 空选判对→不可判分；materials._store / kb text+url 入库**跨 await 持读快照**（BUSY_SNAPSHOT+挡 checkpoint）→检查后 rollback+解析后新鲜重查（并发同哈希插入复用其行）；quiz 双跑竞态（inflight 登记移至调度点同步执行——信号量排队期间端点此前看不到；refresh 同等待在途收口）；progress 两处 .one() 级联删除竞态 500 + 幂等口径文档修正（每次提交=一次复习事件，非幂等）。
- **B9 稳定性（`09068ee`）**：WAL 治理（maintenance 增 checkpoint(TRUNCATE)、引擎 journal_size_limit=64MB、VACUUM freelist≥10% 门控）；孤儿目录 mtime 24h 冷却（**DB 重查兜不住备份恢复「先文件后提交」窗口**——行在提交前必不存在）+ rmtree 失败留痕；plans 溢出 JSON 原子写+读失败日志+os.replace Windows 杀软锁重试；GET /sessions 最新方案 MAX(id) 投影（原全版本载入 512KB×N content 列打满热路径）；llm_task_slot 补齐 4 缺口（kb 问答/材料同步/前台测验/slides 导出）+排队前释放读快照；**材料 SSE 排队心跳**（等槽每 15s 注释行——排队分钟级无字节输出被代理 60s 断连；acquire shield 防取消丢槽）。
- **B10 前端二轮（`b5bb640`）**：**登出/换号清 react-query 缓存**（缓存键无用户维度，公共设备换号命中前任数据）；材料 zombie 任务收敛（boot 时 running+>5min 无事件+无 live → conn_lost 重试入口）；全局 Esc 弹层栈（4 弹层统一——叠加弹层一次 Esc 此前全部同关）；上传可取消（XHR AbortSignal + KbDocsView 行内 ✕ + 两处卸载中止）；apiFetch 空 body 200 不再裸 SyntaxError；登录回跳保留 query；EventsBridge 退避 sleep 可被 abort 唤醒（协程泄漏）；标记已学/学习日志/单元检索三处静默吞错补反馈。
- **B11 插件子系统（`f3a7940`）**：事件总线同步监听器移出事件循环（emit 循环上下文→to_thread 任务；emit_async 逐个 to_thread 保序——插件误写阻塞调用不再卡死整段服务）；调度器 sync 任务 to_thread+300s 上限；SettingsView _DENY 补 url/dsn/conn；webhook_notify GET /config 收紧 require_admin（目标 URL 属集成配置）。
- **门禁**：后端全量 pytest + ruff + 前端 tsc/build 全绿。
- **遗留（产品级决策，未动待用户定夺）**：开放模式 + 0.0.0.0 绑定的接管窗口本质风险（已加限流缓解，根治需改默认绑定或首访令牌——产品决策）；share 链接过期默认（现永久）；daily_budget_usd=0 在共享 agent 路径语义（管理员限定 or 用户可改）；前端插件 CSP 策略。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R4（2026-10-17，Python v1 高负载深度审计与修复轮「iter 367」，✅ 完成）**：用户 /goal「继续详细检查直到逻辑闭合、安全稳定可长期运行；长时间高负载多用户高频使用稳定；检查并修复所有逻辑错误（含交互逻辑、表达与实现一致）；严格检查数据操作 BUG；网络服务安全」。方法：6 路并行审计 agent（plans 状态机/数据完整性/Agent 逻辑/稳定性/安全/前端）产出 ~90 条发现 → 去重分诊为 6 批次逐批「修复→回归测试→套件验证→commit」。共 **6 commit、~40 项修复、41 个新回归测试**：

- **B1 plans 状态机（`9119563`）**：_occupy_generation 注册失败回滚 _STREAMS；_schedule_cleanup 闭包身份校验（防误删新流定时器）；_finalize 持久化失败收敛 error 终态 + 回滚；_run_start 解析段（预算/注册表/密钥）失败镜像 no_provider 错误终态；_persist_plan 版本号 max 查询防并发重号；材料 SSE 收尾清理移入 finally（预算超限 return/断连此前绕过 → 旧单元文件永久孤儿）。
- **B2 数据完整性（`c1fc584`）**：progress 掌握度并发丢更新（5 轮乐观锁条件 UPDATE + 兜底直写）；maintenance 删目录前逐候选复查（会话/run 复活竞态防误删）；backup 导出补 node_sids + 导入去重 + profiles 上限 50 + kind 缺失 None[:64] 崩溃修复（本轮回归测试实证的真 BUG）。
- **B3 Agent 逻辑（`57702cf`）**：thinking 签名链路（AgenticStreamChunk.signature → 累积器重建 → 历史无签名 thinking 剔除，Anthropic 回传契约防 400）；悬空 tool_use 自愈扩展为逆序修复窗口内**全部**悬空对（连续崩溃仍 400）；/runs/{id}/cancel 硬取消 task.cancel（流式长读取 SDK 600s 超时内挂死）；approve CAS 抢占防并发双 note + workspace_committed 置位（防 24h 维护误删已审批产物）+ finalize 不降级已审批终态；单元补料筛选 LLM 用量归集（此前成本黑洞：预算/日用量/用量页全失明）+ BudgetTracker.add 加锁 + materials ctx 注入 user_id/model_pricing。
- **B4 稳定性（`fd3fd0c`）**：webhook sink 无循环分支有界 daemon 线程（原地 asyncio.run 占线程池 worker 10s/事件可抽干 anyio 池饿死登录）；材料 SSE 逐单元落库/收尾清理移 to_thread（单融合调用防断连取消跳步）；备份 import/preview 256MB json.loads + restore 移 to_thread；events.resolve_owner sid→用户 60s TTL 缓存（此前每事件×每订阅者同步查 DB 在事件循环上）；/api/events/stream 补 SSE 头；优雅关停（lifespan 取消在途流 + start 脚本 --timeout-graceful-shutdown 20）。
- **B5 安全（`702bbc0`）**：限流补齐 5 处（sessions/text/parse×2，配额只限总量不限速率）；/setup/dismiss 补 _setup_caller（此前完全公开可翻全局状态）；require_auth JWT 业务校验（已删用户/改密吊销旧 JWT 此前仅密码学验证即放行）；zip 防护第二层——**实验验证**伪造 file_size 头可绕过声明值检查且 read() 爆内存后才 CRC 报错，改 lift 钳制 + 1MB 块流式计数实际解压字节；slides 净化白名单化（svg/form/action/xlink:href 等黑名单外攻击面）；export file_url 不再回显服务器绝对路径。
- **B6 前端 12 项（`a42a1dc`）**：Home startPlan 首事件即兑现（此前 await 整流用户钉死首页）；Chat 干净 EOF 兜底（永久卡「生成中」）；Notes 保存往返并发输入不覆盖；PlanDetail 重生成流随 planId 中止 + 回调归属校验；Review 离页失效快照；NodeNoteEditor 未保存草稿保护；TaskCenter \$→￥；幻灯片导出改 Blob 下载（弹窗拦截器）；QuizModal 换题清作答；Clarify 按 sessionId 重挂载；KbViewerModal revoke 出 setState 更新器；GenerateProgress 判态失败持久视图+重试。
- **门禁**：后端全量 pytest（~130 文件）+ ruff 全仓 + 前端 npm run build 全绿；测试侧 3 处 unused import 由 ruff --fix 清理（随收尾 commit）。
- **下一步**：回到 M1 剩余（多实例 spike → 模块重命名 → 品牌化 → 门禁基线 → 冒烟产物，见 iter R1 下步行）。

**iter R3（2026-10-17，全仓文档按实况对齐，✅ 完成）**：用户 /goal「根据实际代码内容更新项目内所有文档，删除过时内容（删除必须 git 跟踪、不丢数据）」。方法：先盘点全仓自有文档（60 份，排除 docs/kernel/ 冻结树与 desktop/ 等上游文档）→ 过时标记扫描 → 相对链接死链脚本全检（python，避开本机 grep=ugrep 坑）→ Python v1 文档抽查对照目录/代码。**结论：R0/R1/R2 已消化绝大部分，本轮修正 4 处 + 确认无需再删除**（历史删除均已走 git rm，历史完整）。4 笔 commit：

- **README 目录树重写（`3b41db3`）**：唯一硬伤——树还是吸收前形态。重写为双栈分区实况：改造工程文档 / Reasonix 内核树（internal 含 runtime/tutor、cmd、tools、sdk、desktop、docs/kernel、scripts/reasonix、workers 等周边、根级构建基座）/ Python v1（阶段④退役）/ 运行时与参考；补 NOTICE/AGENTS 行、REF 改「吸收用源快照」口径。
- **note/00-INDEX + plan/01 状态口径（`1a5dee1`）**：INDEX #12 行 30→61 项+#37 修订注记、新增 graft/STAGE.md 索引行；plan/01 §二剔除表前置指针（表已被全量吸收修订取代，保留为决策存档，指向文末注记与 NOTICE）。
- **API.md 死链修复（`8278b94`）**：REST 端点全集引用 plan/03-api-frontend.md → 归档位 docs/archive/plan-v1/03-api-frontend.md（R0 归档时遗漏）+ 补 v2 指引。
- 审计确认无需改动：docs/kernel/（冻结政策见其 README）、note/01-11（2026-10-12 时点分析快照，AGENTS 定位为语义基准）、docs/archive/（归档 README 准确）、plugins/skills/extension/plugin-examples README（v1 代码未动、均带改造期标注、清单与目录一致）、.github/workflows/ci.yml（Python v1 仍可用，M1.5 重建内核 CI）、.env.example（v1 口径）。死链扫描余 5 条均为归档件内 file:/// 历史路径与 URL 占位符（冻结保留）。
- **同日追加（stage.md 本体清理，本节下方「历史归档说明」即本轮产物）**：用户要求 stage.md 过时内容全部清理（git 跟踪即可）——删除 Python 时代全部记录（路线图/约定/步骤/iter 1–365 日志，约 1230 行），保留头部常设要求与 R 系列日志；长期有效的执行约定（中文细粒度 commit、严禁 co-author、不编造、问而不猜、文字模型、Windows 环境坑）自旧「二、执行约定」节收编至「长期执行约定」节。

**iter R2（2026-10-17，开源许可合规与 Reasonix 引用声明，✅ 完成）**：用户「请遵守开源协议，并对 Reasonix 的引用给出说明」。审计结论：MIT 核心要求（许可与版权声明随附）已满足——根 `LICENSE-REASONIX` 含上游 MIT 全文（Copyright (c) 2026 Reasonix Contributors）；上游自带的第三方许可已随吸收树入库（`desktop/frontend-next/public/fonts/{Inter,NotoSansSC,IBMPlexMono}-OFL.txt` + `desktop/electron/assets/cua-driver-LICENSE.txt`），字体（含 subset 化的 *-studio 系列）均有所属 OFL 文本覆盖。本轮补齐引用声明体系，3 笔 commit：

- **NOTICE（根）+ docs/kernel/README.md 溯源（`6a9158a`）**：NOTICE = 引用权威声明——上游仓库/分支/快照 zip/许可/吸收记录五要素表 + 完整路径映射表（docs→docs/kernel、scripts→scripts/reasonix、根冲突文件→docs/kernel/upstream/ 等）+ **修改披露**（代码级仅 productdocs 两行 import 适配 `5af3470`，其余为搬运性质）+ 捆绑第三方许可清单 + 致谢。docs/kernel/README.md 说明该目录为上游原样文档、upstream/ 存档非本仓生效文档、上游语境（Reasonix 命名/社区链接）将按 M1.2/M1.4 本地化。
- **README/AGENTS 落点收口（`80d43bb`）**：README「开源许可与致谢」权威声明从"DEEPTUTOR.md（待建）"改指 NOTICE，快照名精确化；AGENTS「仓库状态速览」2026-10-15 → 2026-10-17（M1.1 吸收完成、新增引用合规条目：**此后修改上游吸收代码须同步更新 NOTICE 修改披露**）。
- **stage.md 本记录**。验证：NOTICE 引用的 10 个仓库内路径全部存在（无死链）。MIT 合规要点：保留许可与版权声明 ✅（LICENSE-REASONIX + NOTICE）、署名致谢 ✅（NOTICE/README）、不冒充上游 ✅（upstream/ 原件存档、修改披露）；MIT 无 copyleft，改造与再分发无额外义务，但仍按用户要求明写引用以示尊重。

**iter R1（2026-10-17，REF 全部吸收：R-Tutor 20261017 快照入仓，✅ 完成）**：用户 /goal「基于 Reasonix 的改造实现放在 REF 下，逐步吸收其中内容，细粒度 git commit，直至包含其中全部内容」。REF 已更新为 `R-Tutor-src-studio-20261017.zip`（较写方案时的 1015 快照：**嫁接完成 + 产品翻转 + 统一前端 + 11 轮性能优化 + 实机验证轮**，一手记录在 note/graft/STAGE.md）。本轮 **20 个细粒度 commit** 完成 8289 文件吸收：

**复核审计补记（同日第二轮，目标重触发后的全链验证）**：zip→extracted→git 三级全链重验：(1) zip 9138 条目 = 8289 文件 + 849 目录（awk 权威过滤；首报 9138 vs 8289 疑缺系本机 grep=ugrep 对 '/$' 过滤失效的假警报）；(2) extracted↔仓库 17 组 diff -r 字节一致（唯一差异仍为 productdocs 两行 import 适配）；(3) **zip 映射清单 vs git ls-files 求差发现 3 个漏网**——`desktop/frontend-next/perf/media/camera-{lite,pro,travel}.png`（上游嵌套 .gitignore `*.png` 挡住的基准夹具，工作树快照 zip 携带）→ `git add -f` 补纳（`7b40d7a`），复验差集=0。教训：吸收类目标的完备性验证必须以**源清单（zip）映射后对 git ls-files 求差**为准，working-tree 比对会被忽略规则蒙蔽。

- **前置规则**（`e64b5a9`/`be04bfb`）：.gitattributes 并入内核 LF 规则；.gitignore 并入内核忽略段（`data/` 锚定 `/data/` 防误伤 benchmarks/desktop 源码目录，补 `/backend/data/`）。
- **内核逐层**：构建基座（`e3095c5`）→ base+contract（`0fbb806`）→ safety+state（`bd95d85`）→ model+platform（`18bfac3`，禁用项代码保留在库、不接线，见 note/12 修订记录）→ tools+ext（`9f5e867`）→ runtime（`2107868`）→ **runtime/tutor 嫁接先例单独成 commit（`1b76932`）**→ session+assembly（`30a3e3d`）→ frontend（`d1022d2`）→ cmd（`1bdd2ca`）→ tools/sdk 门禁（`8a16174`）。
- **desktop/**（`c651c79`，含 frontend-next 统一前端——阶段④前端主体已就绪）→ 周边资产（`107488d`）→ docs/kernel + upstream 存档 + LICENSE-REASONIX（`154cb4b`）→ note/graft/STAGE.md（`3fa83ca`）。
- **重定位映射**（与仓库既有文件避让）：`docs/ → docs/kernel/`；`scripts/ → scripts/reasonix/`；根冲突文件（README±/AGENTS/CLAUDE/REASONIX/CONTRIBUTING/SECURITY/design-qa/.env.example/.gitignore/.gitattributes）与 `.github/ → docs/kernel/upstream/`；`LICENSE → LICENSE-REASONIX`；`note/graft/STAGE.md → note/graft/`。
- **验证**：17 组 `diff -r` + 全部根文件 `cmp` **字节级一致**（唯一 "Only in" 为重定位目标目录 docs/kernel/upstream，预期内）；`go build ./...` 与 `go vet ./...`（含全部测试文件编译）**退出码均 0**；唯一内容适配 = productdocs 嵌入包 import `reasonix/docs → deep-tutor/docs/kernel` 两处（`5af3470`，重定位必然代价；M1.2 全量重命名时统一处理）。
- **文档同步**：note/12 新增「修订记录」节（全部吸收取代 #37 剔除拷贝；禁用语义改为不接线）；plan/reasonix/01 加执行状态注记（吸收已完成、重命名未做）。
- **下一步**：M1 剩余——多实例 spike（M1.3）→ 模块重命名 reasonix→deep-tutor（M1.2）→ 品牌化+DEEPTUTOR.md（M1.4）→ 门禁基线重建+CI（M1.5）→ make build 冒烟产物（M1.6）。顺序质量优先（#54）。

**iter R0（2026-10-15，Reasonix 改造启动：方向定稿 + 完整方案落盘 + 全仓文档整理，✅ 完成）**：用户 /goal「写完整改造方案到 plan，并更新项目内所有文档（过时可删，但必须全部 git 跟踪或有备份）」。前置：五轮 61 项决策确认（note/12 第一至八节，决策 #1–#61）。本轮落地（分支 **refactor/reasonix** 自 FIX-Filter 切出，4+1 commits）：
- **决策档案**：note/12-Reasonix改造决策.md（九节 61 项，含每轮来龙去脉与阶段①体量提示）——**改造宪法**，实施遇冲突先对照、分歧问用户。
- **完整方案**：plan/reasonix/ 六份（`963d77f`）——00 总方案（架构图/资产处置/四阶段/风险 R1–R10）· 01 仓库布局与内核落位（拷贝裁剪清单/重命名流程/MIT 署名/门禁搬入）· 02 数据层（SQLite+sqlc+迁移、领域 schema 草案、data/ 总图）· 03 API v2 与 shim 映射（端点清单/SSE 协议/全局事件流）· 04 多用户与内核集成（用户装配层/per-user 装配/内核多实例清单/Chat 桥接/planner 审查者）· 05 任务分解（阶段① M1–M5 细粒度、9 插件等价表、19 页回归清单）。
- **文档整理**：旧 plan 12 份+chat.md+err.md 归档 docs/archive/（`5dcfdd2`，含归档 README 与 API.md shim 基准状态头）；**note/ 纳入 git 跟踪**（`a92fae9`，.gitignore 移除 /note；/REF 保持忽略=zip 本地备份）；README 改造期更新+致谢节、AGENTS.md 工作指引（`fb3a6c2`）；子目录文档状态标注——plugins/extension/plugin-examples/skills 四份 README 加改造期去留说明、skills 失效链接修复（见本条目收口 commit）；本条目（`8e90714`）。stage.md 本体保留根目录（活文件）。
- **工作方式确认**（note/12 #58/#61）：全自动推进、任务拆尽可能细、每步谨慎；细粒度 commit 自主提交不 push，完成后用户合 main。
- **下一步（已被 iter R1 执行）**：阶段① M1.1 内核拷贝与裁剪——已完成（见上）。
