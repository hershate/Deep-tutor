# 119 号：security 域全量深读 + learning 生成链/ projects 反馈闭环问题集（第四十轮）

审计基线 `00688fcb`。审计面：security 域（mod/guard/commands）首次整体逐行
深读；learning 生成链（pipeline/validate/knowledge/lecture/actions/mod）按业务
流整体走读；projects 反馈闭环补深。经子代理探查 + 主审逐条实证。共 1 P2 + 2 P3 + 1 信息。

## P2

### 119#1 登出「重启不会自动恢复」的承诺跨进程失效——logged_out 仅进程内存，重启后以首个管理员静默自动登录；成员登出+重启等同静默提权

- 位置：`desktop/src-tauri/src/backend/security/mod.rs:805-812`（`initialize`：current() 报 login_required 且 `!logged_out` 时以 `ORDER BY id LIMIT 1` 的首个活跃管理员 `issue()`）；`:251-253`（字段注释自述「直到显式登录（login / 令牌认证）或**应用重启**」——后端自知重启即失守）；`:280` 等（`logged_out: AtomicBool` 仅在 new/for_tests* 以 false 构造，migrate 无对应表/列，全仓无持久化写入点）；`startup.rs:416/:443`（每进程新建 SecurityState 后 assemble 即调 initialize_session）；前端承诺 `desktop/src/components/SecurityPanel.tsx:131`（「撤销当前 JWT；退出后需重新选择本地用户，**重启应用不会自动恢复**」）。
- 传递链条：①用户点「退出当前会话」→ logout() 撤销当前用户全部会话（issue 的全局撤旧不变量保证此刻全库零活跃会话）并置进程内 logged_out=true；②用户依界面文案预期「重启后需重新选择用户」（登出防的正是离机后他人重开）而重启应用；③新进程 SecurityState::new（logged_out=false，无持久化）→ initialize_session → initialize → current() 报 login_required 且 logged_out==false → 自动以首个管理员签发新会话 → 前端 bootstrap 直接成功，登录门不出现。④终态一：单管理员部署，重启后静默回到已登录状态，承诺落空；终态二：多用户部署，**成员登出后重启，进程会话变成首个管理员**——可见管理员数据、可建用户/PAT/改配额（103#8 注释自述要防的「成员身份静默提权」同类，触发点从 keyring 槽丢失换成进程重启）。证伪记录：tests.rs:242 `logout_revokes_session_until_explicit_login_and_never_relogins_silently` 只覆盖同进程下一条命令不重登，无跨进程用例。
- 影响范围：所有执行过 logout 的安装（文案欺骗成立）；存在成员账户的部署（权限边界被跨越）；单用户自用场景实害有限（重启即回登录态是产品曾经的行为，问题在于现文案承诺了相反语义并引入多用户）。
- 修复方向：任选其一并保持文案一致——(a) logged-out 状态持久化到账户库（meta 行），显式 login/PAT 认证时清除；(b) 自动签发条件收紧为「auth_sessions 全表为空」的真首启（登出后留有已撤销行，即不再自动签发）；(c) 仅改文案不消除提权，不推荐单独采用。

## P3

### 119#2 讲义整段完成的终态落库无活跃态守卫、循环后也无取消复查——用户取消可被静默覆写为 done

- 位置：`desktop/src-tauri/src/backend/learning/lecture.rs:722-724`（`UPDATE learning_lectures SET status='done' … WHERE plan_id=?3 AND stage_id=?4`——无 `AND status IN ('running','cancelling')` 守卫）；`:708-721`（单元循环的取消检查只在每单元开头，最后一个单元完成后、终态事务前无复查）。对照同族既定口径：pipeline.rs:690（循环后有 `cancelled(state, id)?`）、learning/mod.rs:843-852（complete_plan 带 `status IN ('running','cancelling')` 守卫且 cancelling 映射终态 cancelled，98#5）、practice/mod.rs:983（完成 UPDATE 限定 `status='generating'`，changed==0 抛 cancelled 丢弃结果）。
- 传递链条：①最后一个单元的模型请求完成（或 run 进入时全部单元已 done 逐个 continue），worker 处于最后一次 flag 检查之后、终态提交之前；②用户点「取消生成」→ lecture::cancel 置 flag、`UPDATE … SET status='cancelling' WHERE status='running'` 命中、发 lecture.cancel_requested；③worker 的无守卫 UPDATE 把 cancelling 覆写为 done，落版本行、发 lecture.completed；④StudyRoom 弹窗：用户刚点取消、状态从「正在取消」翻成「已生成」，版本历史多一行——取消意图被静默吞掉。现有测试只覆盖流式中途取消（cancel_chat 中止请求路径），未覆盖此窗口。
- 影响范围：所有已采用方案的讲义生成（含 force 重生成、中断后续跑收尾）的取消尾部窗口。
- 修复方向：终态 UPDATE 加 `AND status IN ('running','cancelling')` 守卫并对 cancelling 映射终态 cancelled（镜像 98#5），或循环后终态事务前补一次取消复查；版本行与 lecture.completed 事件随之只在真完成时落。

### 119#3 projects complete() 把 cancelling 也视为可完成——三个生成域中唯它「结果赢」，与 learning/practice 的取消优先口径相反

- 位置：`desktop/src-tauri/src/backend/projects/mod.rs:408`（`UPDATE project_versions SET status='done', feedback_json=… WHERE id=?3 AND status IN ('running','cancelling')`）；对照同文件 fail() :435（含 cancelling 但映射为 cancelled）、learning/mod.rs:846-850（cancelling→终态 cancelled，结果照存）、practice/mod.rs:983-990（cancelling 视为已取消、结果回滚）。
- 传递链条：①evaluate() 的模型请求返回 done，:507 `cancelled(state, id)?` 复查时取消尚未提交；②用户点「请求取消」→ cancel() 提交 status='cancelling' + cancel_requested=1，发 project.feedback_cancel_requested；③evaluate() 紧接着调 complete()，守卫集合含 cancelling → changed==1 → 覆写为 done 并写 feedback_json、发 project.feedback_completed；④Projects.tsx：用户取消后评价反而「已完成」并展示三档分数——结果本身是真的，但终态取决于毫秒级时序。
- 影响范围：单个评价版本的终态与事件语义；取消按钮后的状态翻转。
- 修复方向：对齐 learning 口径——complete() 守卫收紧为 `status='running'`（或 cancelling 时照存 feedback_json 但终态记 cancelled）；与 fail() 的映射方向一致并在注释固化口径。

## 信息

### 119#4 配额资源 materials 的错误文案「KB 文档」与安全面板标签「资料」不一致——注释宣称的对齐不成立

- 位置：`desktop/src-tauri/src/backend/store.rs:118`（`"materials" => "KB 文档"`）与 `:114` 注释（「与 SecurityPanel quotaNames 对齐」）vs `desktop/src/components/SecurityPanel.tsx:9`（`materials: '资料'`）。
- 传递链条：①管理员把名为「资料」的资源上限设为 N；②超限写入报「KB 文档配额已用尽：当前 N，上限 M」；③用户在设置面板找不到叫「KB 文档」的条目——错误与设置项无法关联。其余四资源（Agent 会话/笔记/社区讨论/Agent 记忆）两侧一致。
- 影响范围：仅 materials 一个资源的文案；无行为错误。
- 修复方向：统一字串（建议以「资料」为准改 store.rs:118），同步修正 :114 注释。

## 核实通过项（免后续复查）

- **security 单活动会话与切换状态机**：issue() 撤旧会话+keyring 槽清理（失败路径删新槽）；logout 撤当前用户全部会话（tests 实证）；switch 失败回滚 store 与 STORE_OWNER 镜像（90#11 含锁中毒恢复）；96#1 签发原子性（keyring 在库变更前、六写同事务）；login 失败恢复 logged_out 原值。
- **PAT 路径**：34#2 锁序（先释放账户锁再取 WS 写锁）、31#34 exclusive()、换人 stop_all+调度停机+签发后 with_user 重启；哈希存储/吊销/停用联检；明文 Zeroizing。
- **JWT**：exp/iat 窗口三重拒绝（660-670）、HMAC 标准向量、累积差常量时间比对（652-659）、base64url 规范形校验、DB expires_at 等值复核。
- **keyring 槽生命周期**：创建/清理四路径容忍口径一致；session_credentials_unavailable 独立码不入自动签发分支、overview 容忍列表已含（111#1 修复在位+测试在案）。
- **guard 白名单与 principal**：完全免门仅 5 条存储恢复命令；免会话过 ensure_ready 4 条；RAII Reset/ScopedBuilder 传播/async 派生（guard_tests 实证不落 user1）；reauthentication_required 6 码与前端 authenticationErrors 6 码全等；overview 容忍列表与实际认证态码全量对齐。
- **用户/工作区**：user_workspace_path 重定向与非法拒绝、两用户 DB+原件隔离（workspace_tests）、user1 首迁复制（WAL checkpoint/写锁/暂存改名）、删除用户保留数据与面板文案一致、cannot_disable_current/last_admin 双守卫、同名 NOCASE 查重。
- **配额调用面**：5 资源×生产写入口全覆盖（sessions→agent:349；notes→store:763/notes:291/knowledge:331/projects:695；materials→materials:624 唯一导入落库点；threads→community:460/503；agent_memory→tools:817 含 key_exists 防误计）；B-5 幂等补种；总览与 ensure 同口径。
- **锁序**：WORKSPACE_SWITCH 四使用点全链「WS 锁→账户互斥→store 互斥」无 ABBA；logout 不持 WS 写锁的不对称在单窗口前端下无可达终点（记录在案）。
- **resume_background/stop_all**：十域 recover 同冷启动序、闸门复位有测试；八域并行 panic 不放大失败不短路。
- **learning 生成链主体**：八步编排/artifacts 双写/31#5 与 98#6 断电窗口/begin_step 复用配对恒一致（attempt 递增必新造 request）/routing stream_chat 幂等缓存闭环；降级判定（passed && warnings.is_empty()）与告警集合一致；B-4/10#12/10#16 重试策略实现相符；贪心排期日负荷/末日边界/within_deadline 口径；五维校验分级与汇总；knowledge 掌握度加权+脏数据过滤+BFS 正确性+环安全；lecture 单元状态机与版本语义+753/842 双路径事件一致；actions 采纳幂等+31#19 倒序联动；plan 状态机全部写点枚举无非法转换。
- **projects 反馈闭环**：版本号 IMMEDIATE 内 MAX+1、attempt 递增新造请求（recover 复用链闭合）、31#13 双检关闭并发双 running、recover 逐条落终态、cancel 容忍 no_active_request、变式/互评不与版本表关联（唯一持久关联为迭代笔记）。
- **前端契约抽查**（行为不符类）：PlanGenerator/Plan/StudyRoom/KnowledgeMap/Projects 的状态集合、camelCase 字段、事件名、regeneration 限额公式、resume 可用集、force 语义全部与后端一致。
- **点名两项复验（基线 00688fcb）**：全库无 `Command::new`/`process::Command`；windows_subsystem 在位；39/40 轮改动未新增子进程路径。
