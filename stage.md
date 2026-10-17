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
- **验证**：17 组 `diff -r` + 全部根文件 `cmp` **字节级一致**（唯一 "Only in" 为重定位目标目录 docs/kernel/upstream，预期内）；`go build ./...` 与 `go vet ./...`（含全部测试文件编译）**退出码均 0**；唯一内容适配 = productdocs 嵌入包 import `reasonix/docs → reasonix/docs/kernel` 两处（`5af3470`，重定位必然代价；M1.2 全量重命名时统一处理）。
- **文档同步**：note/12 新增「修订记录」节（全部吸收取代 #37 剔除拷贝；禁用语义改为不接线）；plan/reasonix/01 加执行状态注记（吸收已完成、重命名未做）。
- **下一步**：M1 剩余——多实例 spike（M1.3）→ 模块重命名 reasonix→deep-tutor（M1.2）→ 品牌化+DEEPTUTOR.md（M1.4）→ 门禁基线重建+CI（M1.5）→ make build 冒烟产物（M1.6）。顺序质量优先（#54）。

**iter R0（2026-10-15，Reasonix 改造启动：方向定稿 + 完整方案落盘 + 全仓文档整理，✅ 完成）**：用户 /goal「写完整改造方案到 plan，并更新项目内所有文档（过时可删，但必须全部 git 跟踪或有备份）」。前置：五轮 61 项决策确认（note/12 第一至八节，决策 #1–#61）。本轮落地（分支 **refactor/reasonix** 自 FIX-Filter 切出，4+1 commits）：
- **决策档案**：note/12-Reasonix改造决策.md（九节 61 项，含每轮来龙去脉与阶段①体量提示）——**改造宪法**，实施遇冲突先对照、分歧问用户。
- **完整方案**：plan/reasonix/ 六份（`963d77f`）——00 总方案（架构图/资产处置/四阶段/风险 R1–R10）· 01 仓库布局与内核落位（拷贝裁剪清单/重命名流程/MIT 署名/门禁搬入）· 02 数据层（SQLite+sqlc+迁移、领域 schema 草案、data/ 总图）· 03 API v2 与 shim 映射（端点清单/SSE 协议/全局事件流）· 04 多用户与内核集成（用户装配层/per-user 装配/内核多实例清单/Chat 桥接/planner 审查者）· 05 任务分解（阶段① M1–M5 细粒度、9 插件等价表、19 页回归清单）。
- **文档整理**：旧 plan 12 份+chat.md+err.md 归档 docs/archive/（`5dcfdd2`，含归档 README 与 API.md shim 基准状态头）；**note/ 纳入 git 跟踪**（`a92fae9`，.gitignore 移除 /note；/REF 保持忽略=zip 本地备份）；README 改造期更新+致谢节、AGENTS.md 工作指引（`fb3a6c2`）；子目录文档状态标注——plugins/extension/plugin-examples/skills 四份 README 加改造期去留说明、skills 失效链接修复（见本条目收口 commit）；本条目（`8e90714`）。stage.md 本体保留根目录（活文件）。
- **工作方式确认**（note/12 #58/#61）：全自动推进、任务拆尽可能细、每步谨慎；细粒度 commit 自主提交不 push，完成后用户合 main。
- **下一步（已被 iter R1 执行）**：阶段① M1.1 内核拷贝与裁剪——已完成（见上）。
