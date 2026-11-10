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

- 位置：`desktop/src-tauri/src/backend/materials/commands.rs:218`（注释「≤200MB ZIP 预检」）；`archive.rs:33`（本体上限 MAX_FILE_BYTES=50MB）与 `:9`（200MB 是解压总量 MAX_TOTAL_BYTES）；`archive.rs:42-47`（`archive.len() > MAX_ENTRIES` 在目录条目 continue（:58-60）**之前**判定）。
- 传递链条：①维护者按注释理解边界为「ZIP 本体可到 200MB」；②实际本体 >50MB 即拒（文案「大小为 1 到 50 MB」）——注释口径错位一级；③64 个文件 + 若干目录条目的合法包：len() 含目录 >64 → 整包拒绝，文案「最多包含 64 个文件」与判定（文件+目录条目数）不符；④用户按文案组织的合规包被拒且错误信息无法自解释。
- 影响范围：导入含目录结构 ZIP 或按注释排障的用户（误拒与口径误导）；纯文件 ZIP 与小包不受影响；文案/注释-行为不符，无数据损坏。
- 修复方向：上限检查移到目录 continue 之后（只数文件）；注释改为「50MB 本体 / 200MB 解压总量」双口径。

### 126#6 enqueue 不检查 accepting 闸门——stop 窗口内登记成功但永不执行

- 位置：`desktop/src-tauri/src/backend/materials/mod.rs:1056-1126`（enqueue 全程无 accepting 检查）vs `spawn_job:933`（`if !accepting { return Ok(()) }` 静默返回）。
- 传递链条：①stop_all 后、recover 前（reset 流程/关闭中）；②enqueue 仍提交 queued 行、发 material.queued 事件、返回 JobAccepted；③spawn_job 的闸门静默吞掉——任务无 worker 拉起；④前端 toast「原生解析任务已登记，进度将由后端实时推送」而资料永远停在 queued，直至 recover 重排——用户拿到与事实相反的承诺。
- 影响范围：停机窗口（reset/维护）内登记解析的用户（任务静默挂起 + 成功假象）；正常运行期（accepting=true）与恢复后（schedule_next 补起）不受影响；状态承诺失真，无数据损坏。
- 修复方向：enqueue 入口检查 accepting，拒绝并提示「维护中，稍后重试」；或 spawn 静默分支补一条事件。

### 126#7 unscheduled 过滤逐任务 EXISTS 点查（N+1）——take(500) 在 filter 之后无法减少查询

- 位置：`desktop/src-tauri/src/backend/planning/mod.rs:246-249`（tasks 全量 filter 闭包内逐条 `SELECT EXISTS(SELECT 1 FROM learning_plan_schedule_tasks WHERE task_id=?)`）；对照同函数 events 装载是单条查询（:169-177）。
- 传递链条：①每次周视图读取（get_plan_overview）；②先对全部任务（配额上限 10000）逐条 EXISTS 点查再 filter；③`.take(500)` 在 filter **之后**——前 500 个多为已排期时要继续向后扫完全量；④万任务库每次周视图 = 数千次点查（对照单条 JOIN 反例在同函数内）。
- 影响范围：任务量大的方案打开 Plan 页（每次读取的查询数线性）；小任务量方案与其余 overview 字段不受影响；纯性能，无行为错误。
- 修复方向：一次 LEFT JOIN/NOT EXISTS 集合查询替代逐条点查。

### 126#8 get_plan_overview「读命令」每次执行 IMMEDIATE 写事务——无孤儿时也拿写锁

- 位置：`desktop/src-tauri/src/backend/planning/mod.rs:150-168`（10#17/53#4 的孤儿清理 DELETE 无条件包在 `transaction_with_behavior(Immediate)` 内）。
- 传递链条：①Plan.tsx 对每个 plan.*/tasks/preferences 事件全量 refresh（:87）；②每次 refresh 进 get_plan_overview → 无条件开 IMMEDIATE 写事务执行两条 DELETE（即使删 0 行也拿了写锁）；③高频读路径与真实写路径（保存任务/强度）竞争写锁——WAL 下 deferred 升级方直接 busy；④事件密集期（生成中每步事件）读命令反复抬高写锁压力。
- 影响范围：生成/事件密集期的 Plan 页读取（写锁竞争放大 busy 概率）；孤儿本来就少的常态与其余域读命令（无写事务）不受影响——该面本身也无数据错误；性能/竞态压力面，损害为 busy 概率抬升。
- 修复方向：先 SELECT 判有孤儿再开写事务清理；或清理移出读路径（maintenance/事件驱动）。

### 126#9 update_goal 的 deadline 无「不早于今天」下限——过去日期静默接受

- 位置：`desktop/src-tauri/src/backend/planning/mod.rs:583`（`date(&input.deadline)?` 仅 Iso8601 DATE 解析）；前端 `desktop/src/pages/Plan.tsx:216` date input 无 min 属性。
- 传递链条：①用户在目标弹窗选了过去的日期（手输或误选）；②后端仅验格式不验下限，input_json 落库；③下游 regenerate 时 within_deadline=false → milestone 降 warn + fixes 提示「延后截止日」；④用户在生成结果里才发现日期早已过去——校验缺口而非部分提交故障（25#4 的先校验不覆盖日期语义）。
- 影响范围：误设过去截止日的目标更新（晚发现、多一轮生成）；格式非法日期（被拒）与未来日期不受影响；输入校验缺口，无数据损坏。
- 修复方向：后端补 `deadline >= today` 校验 + 前端 date input 补 min。

### 126#10 now_local 进程级缓存时区偏移，DST 地区跨季不更新——47#2 的遗留边界

- 位置：`desktop/src-tauri/src/backend/store.rs:37-44`（LOCAL_OFFSET `get_or_init` 进程固化）；与 127#10 为同根两症。
- 传递链条：①DST 地区（欧洲/北美）进程跨季运行（春秋切换）；②缓存的偏移过期 1 小时；③午夜附近 `now_local().date()`（周视图默认起点 planning/mod.rs:300）可判错一天；④前端总是显式传 weekStart（Plan.tsx:82）——默认路径触发面窄；无 DST 地区（中国）完全不受影响。
- 影响范围：DST 地区长驻进程 + 无参调用本地日界判定的少数路径（周视图默认起点）；显式传参路径与非 DST 时区不受影响；边界条件，损害为单日错判。
- 修复方向：偏移缓存加 TTL（如 1h）或按日期重探测；与 127#10 一并处理。

### 126#11 周历单日事件 slice(0,50) 静默截断——无「还有 N 条」提示

- 位置：`desktop/src/pages/Plan.tsx:200`（`day.events.slice(0, 50).map(...)`）；后端单日事件无上限（排期表任意多条）。
- 传递链条：①某日排期任务超过 50 条（高密度方案/补排期）；②前端渲染 slice(0,50)，其余静默丢弃；③无任何「还有 N 条」提示（对照同页待排期列表的「最多显示 500 条」明示文案）；④用户在周历上看不到当日全部安排且无从发现——遗漏的任务可能被当作不存在。
- 影响范围：单日 >50 事件的高密度方案（静默漏显）；≤50 事件的常态与其他有明示上限的列表不受影响；信息完整性，无数据损坏（数据在库可经待排期/任务列表看到）。
- 修复方向：超限时附「当日还有 N 条未显示」提示条（对齐 500 条文案范式）。

### 126#12 失败/取消终态把 progress 覆写为 0——进度条倒退丢失语境

- 位置：`desktop/src-tauri/src/backend/materials/mod.rs:987-995` 与 `:1187-1199`（transition 终态传 progress 0）；前端 `Library.tsx:408`「解析状态 … · {progress}%」与列表 ProgressBar。
- 传递链条：①解析推进到 80% 后失败/取消；②终态 UPDATE 把 materials.progress 覆写为 0；③UI 显示「解析失败 · 0%」——进度条从 80% 倒退到 0%；④用户丢失「解析到多少才失败」的语境（长文档解析失败时无法判断是入口就失败还是接近完成失败）；sync_resources 同步重写同值。
- 影响范围：解析失败/取消的资料详情展示（进度语境丢失）；成功路径（progress=100 语义正确）与队列中资料不受影响；口径问题（可能有意），损害为诊断信息损失。
- 修复方向：终态保留最后进度值（或 UI 对终态不显示百分比）。

### 126#13 资料弹窗对每个进度事件全量拉取 detail（含 ≤5MB 解析文本）

- 位置：`desktop/src/pages/Library.tsx:154-163`（任一 material.* 事件 → `backend.getMaterial(activeMaterial.id)` 拉整个资料含解析中文本）。
- 传递链条：①用户打开资料详情弹窗，资料在解析中；②每个 material.parsing 进度事件（每文档 ≤10 个）触发整页监听回调；③回调全量 getMaterial——响应含已解析部分文本（PDF 解析中文本可达数百 KB~5MB）；④MB 级 IPC ×10 次/文档——90#12 只把读取移出主线程，未减事件粒度下的重复拉取；弹窗只需进度条数字。
- 影响范围：解析大资料时打开详情弹窗的用户（重复 MB 级 IPC 与 JSON 解析）；非弹窗期与其他页面事件路径（各自只拉所需）不受影响；性能面，无功能错误。
- 修复方向：弹窗内进度事件只拉状态字段（或节流 + 解析完成才拉全文）。

## 待核

### 126#14 孤儿原件 reuse 与插入失败删除之间的窄竞态——并发同哈希导入可产生永久 missing_original

- 位置：`desktop/src-tauri/src/backend/materials/mod.rs:599-603`（reused 判定：DB 已有同哈希记录或 destination 已存在）+ `:660-662`（插入失败且无同哈希记录时 `if !reused { fs::remove_file(&destination) }`）。
- 传递链条：①线程 A 导入文件 X：rename 原件到 destination 成功、DB 插入失败（如配额超限）→ 走删除分支；②窗口内线程 B 并发导入同哈希文件：观察到 destination 哈希一致（reused=true，跳过写文件）→ B 的 DB 插入成功——记录指向 destination；③A 的 `remove_file(destination)` 执行——B 的记录永久指向已删文件（missing_original，11#15 的补写也救不回原件内容）；④25#11 只处理了 UNIQUE 撞车（已有记录）情形，该窗口无互斥。需「插入失败 + 并发同哈希」同时发生。
- 影响范围：并发导入相同内容文件且一方插入失败的毫秒级交叠（产生一条永久缺原件的记录）；串行导入与插入成功路径不受影响；待核（逻辑成立、概率极低），损害为单条记录原件丢失。
- 修复方向：删除分支改「仅当 DB 无同哈希记录时删」复查一次（或删除也入事务后置）。

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
