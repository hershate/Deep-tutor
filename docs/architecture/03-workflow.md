# 03 · 工作流分析

> 基准：main 分支 commit `cf2cb36c`。引用形如 `文件:行号`，相对仓库根（`desktop/` 内引用会显式标注）。

## 1. 开发与构建工作流

| 入口 | 命令 | 实际动作 |
|---|---|---|
| Makefile:22 | `make lint` | `cargo fmt --all -- --check`（desktop/src-tauri）+ `npm run build`（tsc -b && vite build） |
| Makefile:25 | `make test` | `cargo test --release --locked -- --test-threads=1`（desktop/src-tauri） |
| Makefile:28 | `make check` | lint + test |
| Makefile:18 | `make build` | `npm run tauri build` → tauri.mjs 复制唯一 `*_{version}_*-setup.exe` 到根 `dist/` 并双向 SHA-256 校验（`desktop/scripts/tauri.mjs:24-38`） |
| Makefile:30 | `make hooks` | `git config core.hooksPath .githooks`；pre-push 执行 `make check`（.githooks/pre-push:3） |

本地 Rust 工具链复用：`F:\DevCache\toolchains\1.95-x86_64-pc-windows-msvc`，`CARGO_HOME=F:\DevCache\cargo`、`RUSTUP_HOME=F:\DevCache\rustup`（AGENTS.md 约定）。

CI（.github/workflows/ci.yml）：PR + 手动触发；windows-latest；Node 24（npm cache 以 desktop/package-lock.json 为键）→ `npm ci` → 前端构建 → `cargo fmt --check` → 原生测试（同 --test-threads=1）→ NSIS 安装包 → 上传构件 `deep-tutor-windows-nsis`（`if-no-files-found: error`）。无部署阶段。

```mermaid
flowchart LR
    A[PR 或手动触发] --> B[npm ci Node 24]
    B --> C[npm run build tsc + vite]
    C --> D[cargo fmt --check]
    D --> E[cargo test --release --locked 串行]
    E --> F[npm run tauri build NSIS]
    F --> G[上传安装包构件 缺失即失败]
    H[pre-push hook] --> I[make check = lint + test]
```

发布节奏证据：dist/ 下 1.0.0→1.0.4 五个安装包；版本号三处同步（package.json、tauri.conf.json、Cargo.toml 均为 1.0.4）。

## 2. 测试策略

- 组织：每域 `tests.rs` 与实现同目录；关键域拆分（providers: protocol_tests/test_support、security: guard_tests/workspace_tests、practice: exam_tests/subjective_tests/variant_tests、sync: versions_tests）。共 112 个 .rs 文件中约 1/3 为测试。
- 运行方式：`--release --locked -- --test-threads=1` 串行，避免 SQLite 文件与全局锁（STORE_OPEN_LOCK、ACTIVE）竞争。
- 测试基建：
  - `tauri::test::MockRuntime` + `mock_builder` 驱动真实命令 IPC（startup.rs:613-671 `mock_app/ipc` 辅助函数）。
  - 本机回环 TestServer 线程模拟 LLM/WebDAV/S3 协议（providers/test_support.rs:35-70），断言真实 HTTP 行为而非 mock 返回值。
  - `MemoryCredentials`（providers/mod.rs:151）替代系统密钥库。
  - `defer` 布尔关闭真实线程 spawn（learning/mod.rs:372、materials、sync 同型），使流水线测试同步化。
  - MIGRATION_RUNS 计数器验证指纹迁移幂等（store.rs:20-26,1162-1185）。
- 覆盖重点（抽样断言）：恢复不重复调上游（learning/tests.rs:439-499 server 计数不变；subjective_tests.rs:230）；事件 outbox 分页与重启续接（store.rs:1126-1159）；备份 ID 映射与重复导入保留双方（backup/tests.rs:235-242,423,494）；同步回读校验与保留恰 10 个（sync/tests.rs:223-225,396-430；versions_tests.rs:133-135）；并发 grade 仅一次生效（review/mod.rs:768）；两用户文件隔离（security/workspace_tests.rs:70-74）。
- 环境边界：真实供应商、真实媒体编解码播放、交互安装为独立验收项，协议夹具不替代（plan/README.md 约定；无界面安装验证走 `--verify-native` + `DEEPTUTOR_VERIFY_DATA_DIR`，main.rs:14-72）。

## 3. 业务工作流：学习闭环

```mermaid
flowchart TD
    A[导入材料/网页/ZIP] --> B[解析队列单线程]
    B --> C[四源输入启动方案]
    C --> D[八步流水线生成]
    D --> E[采纳 adopt 生成任务与项目]
    E --> F[讲义逐单元生成]
    F --> G[节点针对练习/模拟考]
    G --> H[错题变式]
    H --> I[间隔复习卡片]
    I --> J[学习报告与专注记录]
    J --> K[项目成果与量规反馈]
    K --> L[备份/分享/同步]
```

### 3.1 材料进入（Materials）
决策链（materials/mod.rs:408-544）：

```mermaid
flowchart TD
    A[导入文件 网页文本 ZIP 包] --> B{路径 canonicalize 成功且为普通文件?}
    B -->|否| C[invalid_material]
    B -->|是| D{非空 50MB 内 文件名 500B 内 扩展名 19 种白名单?}
    D -->|否| C
    D -->|是| E{魔数匹配? PDF office zip txt md 媒体签名}
    E -->|否| C
    E -->|是| F{sha256 已存在?}
    F -->|是| G[deduplicated true 不占配额]
    F -->|否| H{materials 配额内?}
    H -->|否| I[quota_exceeded 未写入]
    H -->|是| J[原件 originals 哈希命名 + staged + 事件]
    J --> K[进入解析队列]
```

### 3.2 方案生成与采纳（Learning/Planning）
- 启动前置：材料 1-8 份已解析、daily_minutes 10-600、路由存在（learning/mod.rs:281-346）。
- 采纳 `adopt_learning_plan`（learning/actions.rs:19）：幂等（已有 adoptions 直接返回）；创建 project + 每 `learning_plan_schedule_tasks` 一条 tasks 集合任务 + `plan.adopted`。
- 目标修改 `update_learning_goal`（planning/mod.rs:431）：进入原生再生成（受 `regeneration_limit=2` 限制，actions.rs:147）；**已采纳方案不原地重跑**——`regenerate`（actions.rs:121）scope 2..=6 局部重跑，旧版本存 `learning_plan_versions`，version+1。
- 强度调整 `apply_plan_intensity`（planning/mod.rs:313）：factor 稳妥 0.8/标准 1.0/冲刺 1.2（:259）；`round(m×target/current)` 下限 15 分钟（:271）——按比例避免往返切换累积缩放；乐观锁 preferences/tasks revision（:329-342）。

方案生命周期：

```mermaid
flowchart TD
    A[start_learning_plan 四源输入] --> B[八步流水线]
    B --> C{status done 或 degraded?}
    C -->|是| D[adopt_learning_plan 幂等]
    D --> E[建 project + 每排期一条任务 + adoptions]
    E --> F[讲义逐单元生成 版本递增]
    F --> G{目标修改?}
    G -->|是| H[regenerate 局部重跑 存旧版本 version 加 1 受上限 2 约束]
    G -->|否| I{强度调整?}
    H --> I
    I -->|是| J[比例缩放 round m 乘 target 除 current 下限 15 分钟]
    I -->|否| K[保持既有排期]
    J --> L[同步 schedule entries 与 tasks duration 乐观锁 revision]
```

错题变式闭环：

```mermaid
flowchart TD
    A[答错或未答] --> B[practice_mistakes upsert wrong_count 加一]
    B --> C[以错题为源生成变式]
    C --> D{源是真实错题 同 plan 同题型 且非 exam?}
    D -->|否| E[拒绝]
    D -->|是| F[prompt 追加改变情境不得复制]
    F --> G[校验拒绝与原题相同 prompt]
    G --> H{变式作答}
    H -->|答对| I[原子置原错题 mastered 1 并记录变式题号]
    H -->|答错| J[原错题 mastered 回 0 可再变式]
```

### 3.3 练习与复习（Practice/Review）
- 出题范围约束：nodeId 必须属于方案图谱、所选阶段且属变式 node_ids（practice/mod.rs:591-614）；生成 prompt 与题目校验均受该范围约束（:816-844）；恢复任务不扩大范围。
- 错题变式：`variant_of` 只接受真实错题且同 plan 同题型（variants.rs:65-78）；prompt 追加"改变情境不得复制"（mod.rs:424）；生成校验拒绝与原题同 prompt（:797-799）；答对后原子置原错题 mastered（variants.rs:106-133）。
- 复习到期：`next_review_card` 取 `due_at<=now` 最旧（review/mod.rs:361）；grade 公式见 02 §10。

### 3.4 项目成果（Projects）
提交 `submit_project_version`（projects/mod.rs:278）：content 1-100000B 无 NUL；可选材料须已解析；每项目单反馈任务（事务内复查 :313）；INSERT 'running' + **commit 后才 emit** `project.feedback_started`（:329-335）；量规由模型按概念应用/实现质量/验证与反思 0-100 评分（:443-448），`validate_feedback`（:351）限分≤100、summary≤6000、建议 1-5 条；不声称执行代码。恢复：completed_chat 为真直接重放 evaluate，否则 interrupted（:571-602）。迭代笔记写入真实 notes 集合（:607-648）。

```mermaid
flowchart TD
    A[submit_project_version] --> B[content 1 到 100000B 无 NUL 可选材料已解析]
    B --> C{该项目已有反馈任务?}
    C -->|是| D[拒绝]
    C -->|否| E[INSERT running 事务 commit 后才 emit started]
    E --> F[线程 project-feedback-id]
    F --> G[量规三分 0 到 100 prompt 2048 tokens]
    G --> H{validate_feedback 分数与建议有效?}
    H -->|是| I[done + feedback_json + completed 事件]
    H -->|否| J[failed 不伪造评价]
    I --> K[迭代想法写入真实 notes]
    J --> L[用户 resume 或修改后重提]
```

### 3.5 报告（Reports）
只聚合真实记录：专注实际秒数（running 时实时推导 `now-started_at`，pause 冻结，resume 改写 started_at=now-actual 保持时钟连续，reports/mod.rs:226-244,394,417-420）；完成任务保留计划分钟并区分；掌握度来自 `learning_node_evidence` AVG；无证据不填示例趋势；建议采纳一次提交（:914）；CSV/报告导出经后端（:982）。

专注会话时钟语义：

```mermaid
stateDiagram-v2
    [*] --> running: start 60 到 14400 秒 单活跃
    running --> paused: pause 冻结 actual_seconds 写 paused_at
    paused --> running: resume started_at 改写为 now 减 actual
    running --> completed: complete 落盘实际秒数
    running --> cancelled: cancel
```

### 3.6 知识库与 Wiki（Knowledge/Notes）
- 问答 `ask_knowledge`（knowledge/mod.rs:454）：1-8 份已解析资料为 scope；每份截 16000 字符（MAX_SOURCE_CHARS :24）标记 truncated；系统提示强约束只用提供资料；answer≤30000B；**引用双重校验**：material_id 必须在 scope 白名单（`citation_out_of_scope` :602）+ quote 必须能在资料原文 `text.find` 定位（`citation_mismatch` :182，得 char_start/char_end/line）；`UPDATE ... WHERE status='running'` 防取消后写入（:612-617）。

引用双重校验：

```mermaid
flowchart TD
    A[ask_knowledge 1 到 8 份已解析资料] --> B[每份截 16000 字符 标记 truncated]
    B --> C[系统提示强约束只用提供资料]
    C --> D[stream_chat 4096]
    D --> E{answer 30000B 内? sufficient 则 citations 非空?}
    E -->|否| F[校验失败]
    E -->|是| G{material_id 在 scope 白名单?}
    G -->|否| H[citation_out_of_scope]
    G -->|是| I{text.find 定位 quote 原文}
    I -->|找不到| J[citation_mismatch]
    I -->|找到| K[落 char_start char_end line]
    K --> L[UPDATE WHERE status running 防取消后写入]
```

- 知识关联：笔记↔节点映射只来自学习阶段批注（learning_annotations）；删除笔记同步清理（store.rs:517）。
- 知识库体检 `get_library_overview`（notes/library.rs:67）：未关联自由笔记、完全相同正文、缺失原件、解析失败四类真实项；支持打开对应记录；不自动删除。

### 3.7 社区与提醒（Community）
预约 `reserve`（community/mod.rs:719）：房间须 scheduled 未过期、小组须已加入、`remind_at=max(start-15min,now)`；前端轮询 `claim_community_reminders`（:832）逐条置 delivered_at 并发 `community.reminder_due`；取消预约/房间同步删提醒（:789-792,821-825）。

```mermaid
sequenceDiagram
    participant U as 用户
    participant R as reserve 预约
    participant RM as community_reminders
    participant FE as 前端轮询
    U->>R: 预约房间 scheduled 未过期 已入组
    R->>RM: remind_at 为开始前 15 分钟与现在的较大值
    FE->>RM: claim_community_reminders 轮询
    RM-->>FE: 到期且未投递的提醒
    RM->>RM: 逐条置 delivered_at
    RM-->>FE: community.reminder_due 事件
    Note over RM: 取消预约或房间同步删除提醒
```

## 4. 数据生命周期工作流

```mermaid
flowchart LR
    I[导入原件 originals] --> P[解析文本 material_text]
    P --> U[方案/讲义/练习/问答消费]
    U --> E[事件 events 表]
    B[备份 .dtbackup] --> R[追加导入 ID 映射]
    S[同步 versions 远端] --> V[恢复选真实历史对象]
    M[运维清理白名单孤儿]
```

- 备份排除项：auth_sessions、access_tokens、share_index（secure_delete 后 VACUUM，backup/archive.rs:392-398）；系统凭据本就不在库中（keyring）。
- 导入追加语义：冲突 ID 保留双方（materials 按 sha256 复用）；已有 preferences/routing 不覆盖（transfer.rs:862-866）；agent_memory 重名加"· 导入 n"后缀（:900-914）。
- 同步只含脱敏供应商档案与任务路由，不含学习备份（sync/mod.rs:525-554）；恢复必须选择真实历史对象（versions.rs:31 校验路径属本配置 versions/ 目录）。
- 数据目录切换：不搬移不删除旧数据；目录配置不作为备份中的绝对路径恢复（startup.rs:194-243；plan/README.md 约定）。

## 5. 异常恢复矩阵

| 失败点 | 处理 | 恢复路径 | 证据 |
|---|---|---|---|
| 启动目录配置损坏 | typed error storage_config_invalid，不开错工作区 | 主窗口内恢复默认/重选/重试 | startup.rs:557-569,724-747 |
| 数据库损坏 | Store::root 拒绝打开（storage_failed） | 用户指定目录级恢复；维护页 integrity_check | startup.rs:571-601 |
| 数据库降级 | user_version>1 拒绝（unsupported_schema） | 升级应用 | store.rs:135-141 |
| 写锁竞争 | busy_timeout 10s → database_busy | 前端提示重试 | store.rs:134；error.rs:38 |
| 模型步骤中断 | 步骤 interrupted + request_id 持久化 | resume 复用 completed_chat，不重调上游 | learning/mod.rs:655-668 |
| 提供商连续失败 | 熔断 open 60s | half_open 试探恢复 | routing.rs:626-714 |
| 全部候选失败 | all_providers_unavailable | 换供应商/调路由 | routing.rs:1082 |
| 预算超限 | ensure_budget 拒绝（session/daily USD） | 用户调预算设置 | usage.rs:426 |
| 解析线程崩溃 | catch_unwind → failed + internal_error | 重新 enqueue | materials/mod.rs:862,897 |
| 同步回读不一致 | sync_verify_failed，任务失败 | 重试上传 | versions.rs:196-203 |
| 备份导入中途失败 | 单事务回滚 + 删除已装文件 | 修正后重新导入 | transfer.rs:1121-1125 |
| 应用重置 | 先停任务；DB 成功后才删原件/凭据，失败保留备份 | reset_application | backend/commands.rs:82-110 |
| 用户切换工作区 | 写锁停 7 子系统 → 换库 → 失败回滚 | 守卫 switch | security/guard.rs:49-127 |

## 6. 安全工作流

密钥生命周期：

```mermaid
flowchart LR
    A[一次性表单输入] --> B[keyring set 系统密钥库]
    B --> C[库与日志仅出现 has_key 布尔]
    C --> D[导出配置 reject_sensitive 拒绝凭据字段]
    C --> E[同步上传载荷不含 apiKey 测试断言]
    C --> F[重置时 purge_credentials 全量清除]
```

- 密钥流：一次性表单 → `CredentialBackend.set`（keyring）→ 库/日志/导出/同步只出现 `has_key` 布尔；导出配置 `reject_sensitive`（providers/sync_config.rs:30）拒绝凭据字段；上传载荷测试断言无 apiKey。
- 鉴权流：guard 五层（02 §1；security/guard.rs:132-183）；停用/删除用户即吊销全部会话与 PAT 并删 keyring 条目（security/mod.rs:939-953,1004-1006）。
- 配额流：四资源（notes 500/materials 200/sessions 200/threads 500 默认，security/mod.rs:135）提交前强制；拒绝写入不影响其他用户（store.rs:284-329）。
- 分享流：令牌只存哈希；哈希索引仅暴露给只读定位；快照带工作区 ID 与事件代次（sharing/mod.rs:129-136）。
- 网络流：全部出站经 `public_address`/DNS pin/禁代理绕过/重定向逐跳校验（providers/openai.rs:103,243-254；web/mod.rs:71-181）。

## 7. 前端页面 × 命令矩阵（摘要）

| 页面 | 主命令 | 监听事件前缀 |
|---|---|---|
| Dashboard | get_dashboard | state.changed/focus./plan./practice./review. |
| PlanGenerator | start/cancel/resume/regenerate/adopt、import_material_*、import_webpage | material./plan./provider.chat_delta |
| Plan | get_plan_overview、apply_plan_intensity、update_learning_goal、replace_collection(tasks) | plan./state.changed/security.session_started |
| StudyRoom | get_lecture、generate_lecture、set_reading_progress、save_study_note | lecture./study.progress_changed/agent.artifact_promoted |
| KnowledgeMap | get_knowledge_overview/path、add_knowledge_task | plan.adopted/study.progress_changed |
| AgentCenter | send_agent_message、approve_agent_request（useAgent） | agent./provider.chat_delta/route_attempt |
| Practice | generate/submit_practice_answer/submit_exam | practice./provider.chat_delta |
| Review | next/grade_review_card | review./plan. |
| Library | saveDraft（防抖）、import_notes、get_library_overview | material./study.note_saved/state.changed |
| Projects | submit_project_version、cancel/resume_feedback | project./provider.chat_delta |
| Reports | get_learning_report/calendar、focus 全套 | focus./study.checkin/practice./review./plan. |
| Community | get_community_overview、post/reply/reserve、claim_community_reminders | community. |
| Integrations | 插件全套 | plugin. |
| ProviderHub | 供应商/路由/熔断/日志、stream_provider_chat | 经 refresh |
| Settings | 学习档案、setPreferences | security.session_started/state.changed |
| SharedPlan | read_shared_plan、read_shared_plan_package | 无 |

组件归属：DataDirectoryPanel/BackupPanel/SecurityPanel/SyncPanel/MaintenancePanel/UsagePanel/ExportCenter/SharePlanDialog/MediaPlayer/WikiPanel/ExamWorkspace/SubjectivePractice/TutorPanel/AgentTaskCenter（components/）。

## 8. 命令与事件契约一致性

前端 24 个客户端文件调用的命令名与 main.rs:75-264 注册表逐一核对一致；事件通道唯一（`backend://event`），事件体 `{id,kind,payload,createdAt}` 双端一致；参数 camelCase 与 Tauri 默认映射一致（backend/commands.rs:230-236 IPC 测试同形验证）。
