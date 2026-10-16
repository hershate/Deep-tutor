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
- **验证**：17 组 `diff -r` + 全部根文件 `cmp` **字节级一致**（唯一 "Only in" 为重定位目标目录 docs/kernel/upstream，预期内）；`go build ./...` 与 `go vet ./...`（含全部测试文件编译）**退出码均 0**；唯一内容适配 = productdocs 嵌入包 import `reasonix/docs → reasonix/docs/kernel` 两处（`5af3470`，重定位必然代价；M1.2 全量重命名时统一处理）。
- **文档同步**：note/12 新增「修订记录」节（全部吸收取代 #37 剔除拷贝；禁用语义改为不接线）；plan/reasonix/01 加执行状态注记（吸收已完成、重命名未做）。
- **下一步**：M1 剩余——多实例 spike（M1.3）→ 模块重命名 reasonix→deep-tutor（M1.2）→ 品牌化+DEEPTUTOR.md（M1.4）→ 门禁基线重建+CI（M1.5）→ make build 冒烟产物（M1.6）。顺序质量优先（#54）。

**iter R0（2026-10-15，Reasonix 改造启动：方向定稿 + 完整方案落盘 + 全仓文档整理，✅ 完成）**：用户 /goal「写完整改造方案到 plan，并更新项目内所有文档（过时可删，但必须全部 git 跟踪或有备份）」。前置：五轮 61 项决策确认（note/12 第一至八节，决策 #1–#61）。本轮落地（分支 **refactor/reasonix** 自 FIX-Filter 切出，4+1 commits）：
- **决策档案**：note/12-Reasonix改造决策.md（九节 61 项，含每轮来龙去脉与阶段①体量提示）——**改造宪法**，实施遇冲突先对照、分歧问用户。
- **完整方案**：plan/reasonix/ 六份（`963d77f`）——00 总方案（架构图/资产处置/四阶段/风险 R1–R10）· 01 仓库布局与内核落位（拷贝裁剪清单/重命名流程/MIT 署名/门禁搬入）· 02 数据层（SQLite+sqlc+迁移、领域 schema 草案、data/ 总图）· 03 API v2 与 shim 映射（端点清单/SSE 协议/全局事件流）· 04 多用户与内核集成（用户装配层/per-user 装配/内核多实例清单/Chat 桥接/planner 审查者）· 05 任务分解（阶段① M1–M5 细粒度、9 插件等价表、19 页回归清单）。
- **文档整理**：旧 plan 12 份+chat.md+err.md 归档 docs/archive/（`5dcfdd2`，含归档 README 与 API.md shim 基准状态头）；**note/ 纳入 git 跟踪**（`a92fae9`，.gitignore 移除 /note；/REF 保持忽略=zip 本地备份）；README 改造期更新+致谢节、AGENTS.md 工作指引（`fb3a6c2`）；子目录文档状态标注——plugins/extension/plugin-examples/skills 四份 README 加改造期去留说明、skills 失效链接修复（见本条目收口 commit）；本条目（`8e90714`）。stage.md 本体保留根目录（活文件）。
- **工作方式确认**（note/12 #58/#61）：全自动推进、任务拆尽可能细、每步谨慎；细粒度 commit 自主提交不 push，完成后用户合 main。
- **下一步（已被 iter R1 执行）**：阶段① M1.1 内核拷贝与裁剪——已完成（见上）。
