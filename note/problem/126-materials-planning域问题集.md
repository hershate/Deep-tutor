# 126 号：materials / planning 两域全量首读问题集（第四十二轮）

审计基线 `3da53213`。审计面：materials（导入四通道/原件/解析队列/删除/
ZIP 包）+ planning（周视图/强度/目标/排期）首次整体逐行深读 + Library/Plan
契约核对（此前各轮只修单点）。经子代理逐文件探查 + 主审逐条实证。
共 4 P3 + 8 信息 + 1 待核。

## P3

### 126#1 解析取消的最后竞态窗口未关闭——终态 UPDATE 无状态条件，取消可被覆写为 parsed/degraded（取消-终态竞态的第三个漏网域）

- 位置：`desktop/src-tauri/src/backend/materials/mod.rs:882-917`（`if cancel.load(...)` 检查一次 → transition("parsing",85) → 终态 `UPDATE materials SET status=?2 … WHERE id=?1` **无状态条件**（changed==0 只挡资料已删除）+ `UPDATE material_jobs SET status='done' WHERE id=?2` 同样无条件）；对照 cancel 自身 `WHERE … AND status IN ('queued','parsing','cancelling')`（:1164-1174）。56#1 只修反方向（终态后晚到的取消），34#12 只保护 parsing 翻转。
- 传递链条：①解析的最后一次 cancel 标志检查（:882）到终态提交（:917）之间的毫秒窗；②用户点取消 → cancel 事务把 job/materials 置 cancelling 并回「已请求取消，等待原生任务写入终态」（Library.tsx:227）；③run_job 的无守卫 UPDATE 把 materials 覆写为 parsed/degraded、job 置 done——取消意图被静默吞掉，与 1.1.8 修掉的 lecture（119#2）/projects（119#3）完全同族，materials 是第三个未对齐 98#5 口径的域。
- 影响范围：资料解析「取消提交与解析完成」毫秒交叠窗口（DB 终态为完成而非已取消，取消按钮语义失效）；流式中途取消（有取消检查）与终态后取消（56#1 已防）不受影响；内容真实（解析结果有效），受损的是取消语义与三域口径一致性。
- 修复方向：终态 UPDATE 补 `AND status IN ('queued','parsing')` 守卫；cancelling 行映射终态 cancelled（结果照存口径自定——解析文本可保留）；补「取消交叠不覆写」回归测试。

### 126#2 degraded 终态的事件 kind 与 payload status 恒为 "parsed" 且不带 warning——事件流消费方看到的是失真状态

- 位置：`mod.rs:893-916`（DB 按 `if parsed.warning.is_some() { "degraded" }` 写库，随后 `Store::event(&tx, "material.parsed", json!({… "status": "parsed", "progress": 100}))` 硬编码、无 warning 字段）。
- 传递链条：①部分解析（warning 存在）→ DB status='degraded'；②事件 kind 固定 material.parsed、payload.status 固定 "parsed"；③events 表、插件 dispatch、任何直接消费 payload 的下游看到的都是「已完整解析」——与 DB 真实状态、前端状态映射（Library.tsx:22「部分解析」）不一致；④前端各页靠收到事件后重拉规避（不用 payload.status），影响面收敛在事件流/插件消费方。
- 影响范围：订阅 material.* 的声明式插件与事件回放对 degraded 资料的感知（恒报 parsed）；前端重拉路径与 DB 状态不受影响；事件契约失真，无功能损坏。
- 修复方向：事件 kind/payload 按真实状态生成（`material.{status}` 与 transition 对齐），payload 补 warning 摘要；回归测试断言 degraded 终态事件。

### 126#3 删除确认弹窗文案承诺「引用会失效」，后端实际拒绝删除被引用资料——描述的行为与真实行为相反

- 位置：`desktop/src/pages/Library.tsx:403`（「将同时删除资料记录与可用原件文件，已生成的知识引用会失效；此操作不可恢复」）vs `mod.rs:1293-1305`（in_use 命中 learning_plan_materials/knowledge_session_materials/project_versions 即 `material_in_use`「该资料已被…引用，不能删除其原文证据」——65#6 口径）。
- 传递链条：①被引用资料的用户点删除 → 确认弹窗描述「引用会失效」；②点确认 → 后端拒绝（material_in_use 错误 toast）；③用户读到的是与确认文案相反的结果——确认层的「破坏范围告知」整体失真（既没有真的删，也没有失效引用）。
- 影响范围：删除被方案/Wiki/项目引用资料的全部用户（确认描述与结果相反）；未被引用资料的删除（文案其余部分准确）不受影响；纯文案-行为不符，数据无损（拒绝删除本身是正确防护）。
- 修复方向：文案改为「若资料已被学习方案、Wiki 问答或项目版本引用，将无法删除」；或删除前预检 in_use 并在弹窗内即时告知。

### 126#4 ZIP 资料包导入内存峰值约 250MB——zip 本体整读 + 全部条目解压驻留后才开始逐条落盘

- 位置：`desktop/src-tauri/src/backend/materials/archive.rs:39-40`（`std::fs::read` 整读 ≤50MB）+ `:99-114`（全部条目解压进 `entries: Vec<(String,String,Vec<u8>)>`，总量上限 MAX_TOTAL_BYTES=200MB）+ `:125-133`（之后才逐条 stage_bytes）；对照同域 media.rs:144-156 已确立「流式不整读」先例（E-3/46#2）。
- 传递链条：①用户导入满额 ZIP（50MB 本体、200MB 解压量）；②read_archive 一次性返回整个 entries 向量；③峰值 = 50MB 原文 + 200MB 解压同时驻留 ≈ 250MB；④44#1 只把这段移出主线程未降峰值——低端机器导入即内存尖峰。
- 影响范围：导入大 ZIP 资料包的用户（一次性 ~250MB 内存峰值）；单文件导入（流式已控）与 media 原件不受影响；资源面，无数据损坏。
- 修复方向：逐条流式处理（读一条→校验→stage→释放）；或至少逐条解压落盘后立即 drop。

## 信息

### 126#5 ZIP 上限口径两处失真——注释把「解压总量 200MB」写成「ZIP 本体」；「64 个文件」按含目录条目数判定

- 位置：`materials/commands.rs:218`（注释「≤200MB ZIP 预检」——实际本体上限 50MB（archive.rs:33/MAX_FILE_BYTES），200MB 是解压总量 MAX_TOTAL_BYTES:9）；`archive.rs:42-47`（`archive.len() > MAX_ENTRIES` 在目录条目被 continue（:58-60）**之前**判定——64 文件+目录的合法包被拒，文案「最多包含 64 个文件」与判定口径不符）。

### 126#6 enqueue 不检查 accepting 闸门——stop 窗口内登记成功但永不执行

- 位置：`mod.rs:1056-1126`（enqueue 全程无 accepting 检查）vs `spawn_job:933`（`if !accepting { return Ok(()) }` 静默吞）。stop_all 后 reset 流程中 enqueue 仍提交 queued 行、发 material.queued 事件、返回 JobAccepted——前端 toast「已登记」而任务挂起直至 recover。触发场景窄（停机窗口）。

### 126#7 unscheduled 过滤逐任务 EXISTS 点查（N+1）——take(500) 在 filter 之后无法减少查询

- 位置：`planning/mod.rs:246-249`（tasks 全量 filter 内逐条 `SELECT EXISTS(… schedule_tasks WHERE task_id=?)`）；任务配额 10000 时最坏数千次点查；对照同函数 events 装载是单条查询（:169-177）。每次周视图读取执行。

### 126#8 get_plan_overview「读命令」每次执行 IMMEDIATE 写事务——无孤儿时也拿写锁

- 位置：`planning/mod.rs:150-168`（10#17/53#4 的孤儿清理 DELETE 无条件包在 Immediate 事务内）。Plan.tsx 对每个 plan.*/tasks 事件全量 refresh——高频读与真实写路径竞争写锁（WAL 下 deferred 升级方 busy）。可改先 SELECT 判断有孤儿再开写事务。

### 126#9 update_goal 的 deadline 无「不早于今天」下限——过去日期静默接受

- 位置：`planning/mod.rs:583`（仅 Iso8601 DATE 解析）；前端 Plan.tsx:216 date input 无 min。过去日期落库后 within_deadline=false → 生成时降级提示「延后截止日」——校验缺口而非部分提交故障。

### 126#10 now_local 进程级缓存时区偏移，DST 地区跨季不更新——47#2 的遗留边界

- 位置：`store.rs:37-44`（LOCAL_OFFSET get_or_init 进程固化）+ B 区姊妹条（127 号 now_local 探测失败项）。DST 切换后 offset 过期 1 小时，午夜附近周视图默认起点（planning/mod.rs:300）可判错一天；无 DST 地区（中国）不受影响。前端总是显式传 weekStart，默认路径触发面窄。

### 126#11 周历单日事件 slice(0,50) 静默截断——无「还有 N 条」提示

- 位置：`desktop/src/pages/Plan.tsx:200`（`day.events.slice(0, 50)`）；后端单日事件无上限。对照待排期列表有「最多显示 500 条」明示。多任务方案的单日溢出用户少一天若干事件而不自知。

### 126#12 失败/取消终态把 progress 覆写为 0——进度条倒退丢失语境

- 位置：`mod.rs:987-995` 与 `:1187-1199`（transition 终态传 progress 0）。解析到 80% 失败后 UI 显示「解析失败 · 0%」。可能是有意设计，列为口径问题。

### 126#13 资料弹窗对每个进度事件全量拉取 detail（含 ≤5MB 解析文本）

- 位置：`desktop/src/pages/Library.tsx:154-163`（任一 material.* 事件 → getMaterial(activeMaterial.id) 整个资料含解析文本）。PDF 解析期间 ≤10 个进度事件各触发一次 MB 级 IPC；90#12 只移了主线程未减重复拉取。可节流或进度事件只拉状态。

## 待核

### 126#14 孤儿原件 reuse 与插入失败删除之间的窄竞态——并发同哈希导入可产生永久 missing_original

- 位置：`mod.rs:599-603`（reused 判定）+ `:660-662`（插入失败且无同哈希记录时 `if !reused { fs::remove_file(destination) }`）。线程 A rename 后插入失败准备删除 destination；线程 B 恰在 A 的 rename 后、删除前观察到哈希一致（reused=true 跳过写文件）并成功建记录——B 的记录指向被 A 删除的文件。25#11 只处理 UNIQUE 撞车情形。需插入失败+并发同哈希同时发生，概率极低。

## 核实通过项（免后续复查）

- **单工作线程与闸门**：MAX_PARSE_WORKERS=1 + active 检查 + schedule_next 单补起（测试断言 active 恒 1、取消后队列排空）；job_active 三层（预检+事务内 31#45+map）；stop 后不自动续跑、恢复后继续且不复活取消（各测试在案）。
- **进度口径**：10→35→35-85 逐页 5%→85→100；sync_resources 10% 节流（53#10 一致）；每文档 ≤10 事件。
- **失败重试**：attempt=max+1、UNIQUE 防撞、前端重新解析按钮。
- **哈希/去重/级联**：originals/{sha256}.{kind}；导入查重；11#15 原件丢失补写；25#11 撞车不删原件；34#12 迟到解析不覆盖取消；恢复二次哈希校验；text/jobs FK CASCADE + foreign_keys=ON；被引用友好拒绝（65#6）；project_versions SET NULL。
- **originalAvailable 语义**：实时探测文件存在；metadata_only 恒 false（测试）。
- **导出原件**：数据根拒绝+哈希复核+原子替换回滚（99#4/59#1/59#2/70#3/109#6）+reveal 登记。
- **50MB/32 文件/边界**：MAX_FILE_BYTES 全链一致（单文件/ZIP 条目/媒体/前端文案）；32 分批前后端一致。
- **周界数学**：monday 周一定义、114#5 checked 防 panic、排他上界、前端 currentMonday T12:00 防 DST。
- **强度缩放**：52#1 不套钳制、scaled_daily 单独钳界、77#12 ±1 漂移有注释（测试断言 600→冲刺 720）。
- **替代安排**：基于真实事件逐条缩放（测试 16/20/24）；52#2 先乘后除+终值钳制。
- **unscheduled 500/adopted-first/update_goal 拒改已采用**：前后端一致（各有测试）。
- **契约**：planning 五命令/materials 12 命令参数与返回 camelCase 全对上；material.*/plan.* 事件 kind 前缀消费一致。
- **文本导入边界**：标题 200B/正文 1-500KB/无 NUL；网页 197B 预钳制+256KiB 截断→warning 传递（10#25 COALESCE 保留）。
- **配额同事务**（25#18/31#42）；**媒体不解析**（三处 lowercase 一致）；**重 IO 全移后台+principal**（import/get/detail/export/info/chunk/verify/archive）；**单通道导入内存受控**（50MB 单份 Vec 复用）；**kv→materials 迁移一次性**（meta 标记）。
