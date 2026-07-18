# Deep-tutor 全项目审计问题清单

> 生成时间：2026-07-18。审计范围：backend（api / agent / tools / llm / core / db / alembic）、frontend/src 全部、scripts / CI / 配置。
> 基线：后端 405 tests 通过，前端 tsc 无错误。
> 状态标记：`[ ]` 未修复 / `[x]` 已修复 / `[-]` 不修复（注明原因）。

---

## A. 后端 API 层（backend/app/api/）

### 高

- [x] **A1. /generate 与 /clarify 并发竞态，可绕过并发配额并起双编排器**
  `plans.py:274-314, 527-529`。状态检查（`status==running`、`_check_generate_quota`）在端点内执行，但占位（`_STREAMS[session_id]=stream`、`_set_state(running)`）发生在 StreamingResponse 生成器 `_run_start` 内部。并发两个请求都能通过检查：同会话 → 双 orchestrator 并行（重复计费、`_STREAMS` 互覆盖、`_finalize` 各写各的）；跨会话 → 「每用户并发 1」配额失效。`/clarify`（plans.py:317-342）同构。
  修复方向：端点内、返回响应前同步完成「置 running + 注册流」，或加 per-session 锁。

- [x] **A2. stale running 状态无恢复，可永久锁死用户生成能力**
  `plans.py:85-98, main.py:30-79, core/maintenance.py`。进程崩溃/重启后 `orchestrator_state.status=running` 残留：该会话 /generate 返回 409；且 `_check_generate_quota` 把该用户其他会话的 stale running 也计入 → 该用户所有新会话永远 409，无自救路径。
  修复方向：lifespan 启动时将 running 批量收敛为 interrupted。

- [x] **A3. `_persist_quizzes` 对 LLM 输出 `QuizType(q.type)` 直接转枚举，非法值 → 500**
  `plans.py:1223-1231`。generate_quiz 的 `type` 是自由字符串，LLM 写 "multiple_choice" 等非法值时 `models.QuizType(q.type)` 抛 ValueError → 未处理 500。
  修复方向：非法 type 回退 choice / 跳过该题并计 warning。

### 中

- [x] **A4. 统一错误格式覆盖不全：路由 404/405 与请求校验错误走 FastAPI 默认格式**
  `main.py:118-122`。只注册了 `fastapi.HTTPException` 处理器；`StarletteHTTPException`（404/405）与 `RequestValidationError`（422）不经 `{"error":{code,message,details}}` 统一格式，前端需处理两套错误格式。
  修复方向：同时注册两个处理器。

- [x] **A5. body 上限可被 chunked 传输绕过，且 TextIn.text / UrlIn.url 无长度上限**
  `main.py:100-112, materials.py:52-59`。`_limit_request_body` 仅在有 content-length 头时预检，chunked 放行；TextIn/UrlIn 无 max_length → /materials/text 内存 DoS 面（对比 ClipIn content 有 2MB 上限）。
  修复方向：无 Content-Length 时按流限量读取；TextIn.text/UrlIn.url 加 max_length。

- [x] **A6. 材料/测验/幻灯片 LLM 调用无预算守卫；`daily_budget_usd` 全库零引用**
  `plans.py:1021-1027, 1142-1148, 1279-1286, exports.py:176-182, core/settings.py:56`。这些端点构造的 ExecContext 均不传 budget，也无速率限制；`daily_budget_usd` 仅展示不强制。任何认证用户可无限触发 LLM 调用，费用无上限。
  修复方向：接入 BudgetTracker/日预算校验 + 每用户限流。

- [x] **A7. /regenerate 无重生成次数上限，`settings.max_regenerations` 从未被引用**
  `plans.py:377-419`。同一 plan 可无限次局部重生成，LLM 成本无上限。
  修复方向：按 plan 计重生成次数并执行上限。

- [x] **A8. /auth/login 无速率限制，且存在用户名枚举时序侧信道**
  `auth.py:37-42`。无 limiter 可在线爆破（默认管理员用户名固定 admin）；`user is None` 时短路不执行 verify_password → 响应时间差可枚举有效用户名，与文件头注释目标相悖。
  修复方向：加限流；对不存在用户做 dummy verify 拉平时序。

- [x] **A9. JWT 无吊销：重置密码后旧 token 在过期前（默认 7 天）仍有效**
  `deps.py:78-84, users.py:112-113`。resolve_user 只验签名+用户存在性。改密后已泄漏的旧 JWT 继续可用。
  修复方向：User 增加 password_changed_at，与 JWT iat 比对。

- [x] **A10. 错误消息回显服务器文件系统路径，违反自身 R35**
  `materials.py:289`（`f"单元内容文件缺失: {mat.content_md_path}"`）、`exports.py:169`（OSError 文本含绝对路径）。main.py:3/errors.py:69 声明「不回显路径等敏感信息」。
  修复方向：消息仅含 id，路径进服务端日志。

- [x] **A11. Progress 无 (plan_id, node_id) 唯一约束，先查后插竞态 → 重复行 → 该节点持续 500**
  `progress.py:72-86, models.py:264-276`。并发两个 POST /progress 同节点 → 双插；此后 `.one_or_none()` 抛 MultipleResultsFound，该节点更新端点永久 500。
  修复方向：加 UniqueConstraint + 捕获 IntegrityError 转为更新。（与 C19 同一问题）

- [x] **A12. 删除会话/用户不等任务真正终止，叠加 SQLite rowid 复用，旧任务可把结果写进新会话**
  `sessions.py:51-58, users.py:134-141, plans.py:450-477`。`cancel_running_generation` 只是 `call_soon_threadsafe(task.cancel)`，调用方立即删行提交；`_finalize` 仅靠 `db.get(Session, session_id) is None` 判断是否写库 —— 若 rowid 被新会话复用，旧任务 finalize 时把旧方案写进无关的新会话。
  修复方向：finalize 校验 trace_id 匹配后才写 plan。

### 低

- [x] **A13. /materials/clip 的 url 分支绕过 `_URL_RATE_PER_MIN` 限流** — `materials.py:323-367`。clip url 分支直接 `_store` 不过限流器，SSRF 频控被旁路。
- [x] **A14. `_load_parsed_materials` 拼接无总量上限** — `plans.py:294-303`。20 条 × 50MB 极端 ~1GB 字符串 + chunks 复制一份。
- [x] **A15. upload 解析后 `write_parsed_text` 失败无清理，原件成孤儿文件** — `materials.py:234-237`。
- [x] **A16. share/users/providers 先查后插，并发撞唯一约束 → IntegrityError 500（应为 409）** — `share.py:43-48, users.py:78-90, providers.py:94-108`。
- [x] **A17. ExportIn.format 无校验，未知格式静默走 md 分支** — `exports.py:40-41, 64-72`。应 `Literal["md","pdf","slides","ics","anki"]`。
- [x] **A18. _export_ics/_export_anki 同步 CPU 工作直接在 async 端点执行** — `exports.py:102-132`。对比 _export_pdf 用了 asyncio.to_thread。
- [x] **A19. usage.py:21-25 全表读入 Python 求和** — 应改 SQL 聚合（progress.py:152-156 已用 func.sum，不一致）。
- [x] **A20. errors.py:79-83 `_status_to_code` 取字典首个匹配，语义误导** — 任意 400 → "unsupported_format" 等错误码张冠李戴。
- [x] **A21. CORS allow_headers 缺 "X-Auth-Token"** — `main.py:96`。浏览器跨域预检拒绝该头，与 deps.py 支持的认证方式不一致。
- [x] **A22. plans.py:528-530, 538-540 `SessionLocal(); _set_state(); db.close()` 非 try/finally** — commit 异常时 Session 泄漏。
- [x] **A23. /quizzes/submit 无频率/次数限制，quiz_attempts 行无限增长** — `quizzes.py:26-49`。
- [x] **A24. 会话创建无每用户数量配额** — `sessions.py:34-42`。
- [x] **A25. /health 无认证且回显 disk_free_mb、skills_loaded 等内部信息** — `health.py:24-51`。

---

## B. 后端 agent / tools / llm 层

### 高

- [x] **B1. LLM API 瞬态错误零重试，直接炸掉整条 pipeline**
  `agent/loop.py:95-101`。修复循环只对 schema 校验失败重试；RateLimitError / APIConnectionError / 529 第一次 attempt 就逃出 → pipeline 中断返回 error。AnthropicProvider 也无 SDK 级 retry。
  修复方向：对瞬态错误指数退避重试，且不占 repair 次数。

- [x] **B2. upstream 全量广播 + 内容重复三份，prompt 无大小守卫**
  `agent/orchestrator.py:249, tools/parse_material.py:89, api/plans.py:300-307`。`_build_input` 把所有上游工具完整 data 塞进每个后续 LLM 工具；parse_material 的 data 同时含 text（≤500k chars）和 chunks（同内容再切一份）；plans.py 又把 parsed 全文放进 args → user prompt ≈ 1MB+ 且重复 2-3 次 → 超 context 直接 400 / 成本爆炸。
  修复方向：upstream 按 requires 白名单传递、text/chunks 去重、prompt 字符上限守卫。

- [x] **B3. 旧版模型 ID 前缀不匹配，成本静默记 0，预算守卫失效**
  `llm/pricing.py:29-44`。价目前缀为 `claude-opus/claude-sonnet/claude-haiku`，但 3.x 模型 ID 形如 `claude-3-5-sonnet-20241022` 不匹配 → estimate_cost 返回 0 → 预算超限永不触发。
  修复方向：补充 claude-3-* 映射或子串匹配；未命中记 warning 而非静默 0。

- [x] **B4. SSRF 防护被重定向绕过**
  `tools/verify_url.py:162-164, tools/parse_material.py:405-408`。`_is_safe_url` 只校验初始 URL，而 httpx `follow_redirects=True`：公网 URL 302 到 `http://169.254.169.254/` 或内网时自动跟随且不再过 SSRF 校验。另有 DNS rebinding TOCTOU。
  修复方向：禁用自动重定向、逐跳人工校验目标。

### 中

- [x] **B5. 不检查 stop_reason=="max_tokens"，截断输出白烧 4 次调用** — `loop.py:96-113`。截断 JSON 必然解析失败 → 走满修复重试仍降级。应直接提高 max_tokens 重试一次或立即降级。
- [x] **B6. Python 工具无超时，`Timeouts.parse` 是死配置** — `loop.py:68-73, context.py:23`。parse_material 的 whisper 转写在 ffprobe 缺失时可无限挂起。
- [x] **B7. 事件循环内同步 DNS 解析** — `verify_url.py:222, parse_material.py:397`。`socket.getaddrinfo` 阻塞调用，恶意 DNS 可卡住所有并发 SSE 流。应 to_thread。
- [x] **B8. search_resources 筛选 LLM 调用无超时** — `search_resources.py:166-172`。
- [x] **B9. search_resources 内部 LLM 调用游离于成本计量与预算之外** — `search_resources.py:82, 167`。ToolOutput 不带 metadata → budget.add(0)，花费不进预算、不进 api_usage。
- [x] **B10. 反馈循环重跑中的工具 error 被吞** — `orchestrator.py:157-170, 228-236`。重跑未到达 validate_plan 时取到旧输出（status=ok），error 检查失效，pipeline 以 "done" 返回。
- [x] **B11. validate_plan 降级默认 passed=True，未校验方案按"通过"交付** — `validate_plan.py:59-68`。
- [x] **B12. export_pdf markdown 透传 raw HTML + weasyprint 默认抓取外部资源 → 渲染期 SSRF** — `export_pdf.py:51-65`。plan 内容来自 LLM（可被 prompt-inject），渲染时服务端会请求文档中的 img/CSS URL（含内网）；title 未转义。
- [x] **B13. 新工具接入 pipeline 需改 4 处硬编码，漏改静默丢失输出** — `orchestrator.py:26-44, 259-274, export_plan.py:50-69`。PIPELINE / FEEDBACK_TARGETS / _assemble / _assemble_from_upstream 需同步改，无校验。

### 低

- [x] **B14. `_extract_json` 误判 "json" 前缀** — `loop.py:167`。
- [x] **B15. `BudgetExceeded` 死代码；exceeded() 边界用 `>` 不用 `>=`** — `budget.py:10-14, 29`。
- [x] **B16. `record_api_usage` 无调用方死代码，commit 无 rollback** — `cost.py:41-58`。
- [x] **B17. schedule_plan daily_minutes 未做正数校验** — `schedule_plan.py:54-58, 69`。负数 → 错误的 insufficient_time 警告。
- [x] **B18. orchestrator start_tool 不在 PIPELINE 时静默从头执行** — `orchestrator.py:116`。
- [x] **B19. search_resources stage_level 硬编码为 1** — `search_resources.py:64`。
- [x] **B20. registry.register 重名静默覆盖** — `registry.py:49-50`。

---

## C. 后端 core / db / alembic / main

### 高

- [x] **C1. 迁移 169af26b09e7 在含已有 users 行的库上必然失败**
  `alembic/versions/169af26b09e7_*.py:27-31`。`password_hash`、`is_admin` 为 NOT NULL 且无 server_default —— 已有行的库执行时 NOT NULL constraint failed。所有从旧单用户时代升级的库无法迁移。
  修复方向：加 server_default 后回填。

- [x] **C2. 应用启动只跑 init_db()（create_all），从不执行 alembic 迁移**
  `main.py:44`。create_all 对已存在的库不加新列，旧库直接启动后查询新列即 OperationalError。只有 scripts/start.* 会先跑 migrate；直接 uvicorn 启动则跳过。
  修复方向：lifespan 中调用 run_migrations()。

- [x] **C3. User.sessions 无级联，删用户必被 FK 阻断**
  `models.py:105, 112`。PRAGMA foreign_keys=ON 下删任何有会话的用户 → IntegrityError。ApiUsage.user_id、LlmProviderProfile.user_id 同样。
  修复方向：cascade="all, delete-orphan" 或 ondelete。

### 中

- [x] **C4. SQLite 未设 busy_timeout，并发写直接报 "database is locked"** — `db/base.py:29-35`。
- [x] **C5. 重写单元内容时残留旧拆分子文件，读回内容被污染** — `core/storage.py:128-150`。write_unit_content 从不删除上一版遗留的 .p2/.p3，read_unit_content 无条件串联所有 .pN。
- [x] **C6. 限流器 key 永不淘汰，内存无界增长** — `core/ratelimit.py:17,21`。
- [x] **C7. 全部路径为相对路径，行为依赖启动 cwd** — `settings.py:17,38-40`。非 backend/ 目录启动时读不到 .env、在错误位置新建空库，表现为"数据丢失"。
- [x] **C8. 管理员已存在时密码永不更新，改 ADMIN_PASSWORD 无效** — `core/security.py:94-99`。首次启动 admin_password 为空创建的账号后续配置密码仍无法登录。
- [x] **C9. JWT 签名密钥回退为 AUTH_TOKEN** — `core/security.py:51-54`。低熵 bearer 口令做 HS256 密钥，拿到 AUTH_TOKEN 即可伪造任意用户 JWT。
- [x] **C10. 引擎层硬编码 SQLite，阻碍未来切换 Postgres** — `db/base.py:21-35, settings.py:79-80, alembic/env.py`。PRAGMA 监听器对任何连接执行 SQLite 专有语句；无通用 database_url。
- [x] **C11. KnowledgeNode 自引用 children 无级联** — `models.py:209,215`。
- [x] **C19. Progress 缺 (plan_id, node_id) 唯一约束**（同 A11）— `models.py:264-276`。

### 低

- [x] **C12. maintenance `with sqlite3.connect(...)` 不关闭连接** — `maintenance.py:31-32`。
- [x] **C13. 维护循环启动即执行一轮，每次开机都 VACUUM** — `maintenance.py:74-76`。
- [x] **C14. settings 模块级实例化，环境变量解析错误在 import 期炸裸 ValidationError** — `settings.py:116`。
- [x] **C15. 文件日志 datefmt="%H:%M:%S" 丢失日期** — `logging.py:48`。
- [x] **C16. PBKDF2 20 万轮低于 OWASP 建议 60 万** — `security.py:21`。
- [x] **C17. CORS 允许凭证 + 用户设 `*` 等价任意站携带凭证请求（配置陷阱）** — `main.py:91-97`。
- [x] **C18. 请求体上限仅靠 Content-Length 预检，chunked 可绕过**（同 A5）— `main.py:100-112`。
- [x] **C20. share_links.created_at nullable 与 models.py 不一致** — `c7e2a9f41d35_share_links.py:34`。

---

## D. 前端（frontend/src/）

### 高

- [x] **D1. PlanDetail 局部操作失败把整页替换成无出口的裸错误块**
  `pages/PlanDetail.tsx:183-188`。`error` state 同时被初始加载与导出/分享/撤销分享使用；方案已加载后一次导出失败 → 整页消失只剩红字，无返回/重试按钮。
  修复方向：加载错误与操作错误分离；操作错误改 toast/内联 banner。

- [x] **D2. MaterialsModal 卸载清理中止的是过期 controller，进行中的 SSE 流泄漏**
  `components/MaterialsModal.tsx:88-110`。cleanup 闭包捕获第一个 AbortController，但 run() 会新建并覆盖 acRef.current；关闭弹窗只 abort 旧的，进行中的流不被取消且对已卸载组件 setState。
  修复方向：`return () => acRef.current?.abort()`。

### 中

- [x] **D3. 重生成成功后 regenLoading 永不复位，重生成区永久禁用** — `PlanDetail.tsx:159-180`。同路由组件不卸载，regenLoading 保持 true。
- [x] **D4. Learn 打卡失败导致整页被错误块替换** — `Learn.tsx:88-93`。同 D1 模式。
- [x] **D5. SSE 通道 401 不触发登录跳转** — `lib/sse.ts:46-47, lib/api.ts:198-212`。过期 token 只显示"连接失败"。
- [x] **D6. Learn/Plans/PlanDetail 数据加载无竞态防护** — 快速切换时旧响应可覆盖新数据（ProgressBoard.tsx:104-121 有 cancelled 防护，写法正确）。
- [x] **D7. JWT 明文存 localStorage；VITE_AUTH_TOKEN 打包进客户端 bundle** — `lib/auth.ts:16-18, 35-38`。设计权衡，需文档明示。
- [x] **D8. 导出按钮无 loading/disabled，可重复提交** — `PlanDetail.tsx:211-213`。
- [x] **D9. SSE `JSON.parse(ev.data)` 无保护，单条畸形事件触发不必要的重连且事件重复送达** — `lib/api.ts:449, 483, 220`。

### 低

- [x] **D10. UserManager 非法 Tailwind 类 `dark:bg-amber-9500` + 浅色模式白字浅底不可读** — `UserManager.tsx:222`。
- [x] **D11. Settings 默认 value 颜色缺 dark 变体，深色模式不可读** — `Settings.tsx:82`。
- [x] **D12. store/usage.ts 与 lib/types.ts 死代码；types.ts 的 PlanSummary 与 api.ts 字段不一致** — 易误导。
- [x] **D13. GenerateProgress 取消请求失败被静默吞掉** — `GenerateProgress.tsx:104`。
- [x] **D14. 表单边界：Home 需求描述无 maxLength；Clarify 回答无长度限制/可提交全空/直接访问渲染空表单；Learn 分钟数可提交 0** — `Home.tsx:96-102, Clarify.tsx:71-77, Learn.tsx:66-69`。
- [x] **D15. apiFetch 无超时机制，后端 hang 住时页面永远加载态** — `lib/api.ts:27-44`。
- [x] **D16. Usage/Settings 错误提示笼统误导（任何错误都显示"请确认已配置认证 token"）** — `Usage.tsx:28-30, Settings.tsx:27-31`。
- [x] **D17. Layout `getStoredUser()` 非响应式，改名后顶栏不更新** — `Layout.tsx:20`。
- [x] **D18. listPlans 手工拼接 query 字符串** — `api.ts:153-156`。

---

## E. 脚本 / CI / 配置

- [x] **E1. start.sh：venv 创建被中断后（目录存在但无 python）后续运行跳过重建直接 pip 失败** — `scripts/start.sh:13-19`。应检测 venv python 可执行而非目录存在。
- [x] **E2. start.sh 不校验 Python ≥3.11，低版本建 venv 后到运行期才报错** — `scripts/start.sh:8-10`（ps1 同，`start.ps1:8-12`）。
- [x] **E3. requirements.txt 全部依赖无版本上限** — `backend/requirements.txt`。fastapi/pydantic/anthropic 等主版本升级可破坏构建（genanki 已锁定，应统一策略：至少对关键依赖加上限）。
- [-] **E4. CI 无前端 lint / 无后端 Windows 测试矩阵** — `.github/workflows/ci.yml`（低优先级，可暂缓）。
  部分修复：后端测试已加 Windows 矩阵（ubuntu + windows）；前端 lint 不修复——项目无 eslint 配置，引入新工具链属独立决策（`tsc --noEmit` 已在 CI 的 build 步骤强制执行）。
- [x] **E5. .env.example 默认 `AUTH_TOKEN=change-me-please`，后端启动时未对弱默认值告警** — 部署者忘改即弱认证。

---

## 修复进度记录

（修复过程中发现的新问题追加在下方）

### 2026-07-18 全部修复完成（87 修 / 1 部分修复）

| 批次 | 提交 | 内容 |
| --- | --- | --- |
| 批次 1 | `96a4b57` | C1 C2 C3 C11 C20 A2（迁移默认/init_db/级联/stale running 恢复） |
| 批次 2 | `1efe3b5` | A1 A12 A11/C19 A22（并发竞态锁/trace_id 守卫/Progress 唯一约束） |
| 批次 3 | `90ec38e` | B1-B7 B10 B14 B18 A14（LLM 退避/计量/超时/SSRF 逐跳/prompt 守卫） |
| 批次 4 | `161db11` | A3-A10 A13 A15-A21 A23-A25 B16（API 层统一错误/限额/吊销/计量接入） |
| 批次 5 | `a09c122` | C4-C7 C9 C10 C12-C17（core：busy_timeout/路径锚定/JWT 密钥独立/DATABASE_URL/PBKDF2 600k 等） |
| 批次 6 | `59b07cc` | B8 B9 B11-B13 B15 B17 B19 B20（工具层：筛选超时与计量/PLAN_SECTIONS 单一来源/export_pdf SSRF 防护等） |
| 批次 7 | `1aad16c` | D1-D18 前端（错误分层/竞态防护/apiFetch 超时/SSE 401 跳转/表单边界/死代码清除等） |
| 批次 8 | `7adbf3f` | E1-E5 脚本/CI（venv 健壮性/Python 版本校验/依赖上限/Windows CI 矩阵/弱 token 告警） |

最终验证：后端 407 tests 通过（较基线 +2 新增）、ruff 全过、前端 `tsc --noEmit` 无错误、`npm run build` 成功。

### 修复过程中发现并一并处理的新问题

1. **test_db_migrate HEAD 硬编码** — 新增迁移后测试断言过期；改为 `ScriptDirectory.get_current_head()` 动态获取（批次 2 顺带）。
2. **a3b91c2e4f56 迁移在 create_all 库上重复加列** — 改为幂等（先探测列存在性）；`_REVISION_PROBES` 统一为有序 (revision, detector) 列表，修复探测顺序违反 newest-first 导致的错误 stamp（批次 4 顺带）。
3. **Timeouts.parse 若按计划设 60s 会杀死 marker PDF 解析**（内部 600s）——最终设 900s 并注释约束关系（批次 3 调整）。
4. **export_pdf 全量转义会双重转义代码块** — 改为仅转义 fenced code block 之外的 `<` `>`（批次 6 调整，附回归测试 ×2）。

