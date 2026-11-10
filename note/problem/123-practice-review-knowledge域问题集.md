# 123 号：practice / review / knowledge 三域全量首读问题集（第四十一轮）

审计基线 `8875ee55`。审计面：practice（mod/exams/subjective/variants + tests
三件）、review、knowledge 三域首次整体逐行深读 + Practice/Review/KnowledgeMap/
ExamWorkspace/SubjectivePractice 契约核对（此前各轮只修单点）。经子代理逐文件
探查 + 主审逐条实证。共 2 P3 + 4 信息 + 1 待核（设计）。

## P3

### 123#1 模拟考试进行中 set.score 逐题实时累计并随 detail/submit 响应返回——「交卷后统一反馈」承诺被分数通道旁路，逐题 diff 即可推断对错

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:1030`（`set_row` 仅对 `feedback_mode=="hints"` 遮蔽 set 级分数：`score: (feedback_mode != "hints").then(...)`）；`mod.rs:1326`（每次客观题提交后 `UPDATE practice_sets SET score = (SELECT COALESCE(SUM(score)…) WHERE status='done')` 无条件累计）；`subjective.rs:511` 评分完成同口径更新；考试强制 deferred（`mod.rs:584-589`「模拟考试必须在交卷后统一反馈」）。
- 传递链条：①模拟考试逐题提交；②每次 submit 后 practice_sets.score 更新为已判分答案累计得分；③detail()（get_practice_set）与 AnswerResult.set 原样返回该分数；④考生逐题提交后 diff 两次响应的 set.score——客观题 0/1 跳变即推断每题对错，与「交卷后统一反馈」直接矛盾。同文件已有两个同族旁路被修：98#1（考试进行中错题本泄露解析 mod.rs:1420-1426）、101#4（逐题 evidence 经掌握度泄露 :1305-1308）——修复立场均为「数据在响应载荷在场即泄露面，不能只看 UI 不渲染」（ExamWorkspace.tsx:47 的 percentage 确实只在 completed 块渲染，但载荷在场）。exam_tests.rs:110-114 只断言 answer.correct==None 与 answers 空，未覆盖 set.score。
- 影响范围：所有模拟考试的进行中阶段（逐题 diff 响应即可推断对错，绕过 deferred 设计）；hints 模式（已遮蔽）与已完成考试（本应公布）不受影响；无数据损坏，受损的是考试遮蔽语义与成绩有效性（自我作弊面）。
- 修复方向：`mode=='exam' && status!='completed'` 时 set.score/max_score 置 None（与 hints 遮蔽同型）；补「进行中 detail 不含分数」的回归测试。

### 123#2 practice 生成 prompt 无跨资料总预算——8 份 × 12000 字 = 96000 字符单请求，knowledge 域 10#23 已定的 64k 总闸口径未覆盖

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:416-422`（`source.text.chars().take(12_000)` 逐份截断，全部资料入 prompt）；对照 `knowledge/mod.rs:25-27` `MAX_TOTAL_SOURCE_CHARS = 64_000`（10#23 认定「8 份 × 16000 字 ≈ 12.8 万字符可超模型上下文窗口且无引导」并设总闸）+ `:601-611` 逐份扣减预算实现。
- 传递链条：①方案最多 8 份资料（learning/mod.rs:303 上限）；②practice 生成把每份前 12000 字全部拼入 prompt；③96000 字符 ≈ 明显超 64k 既定口径，且无截断提示引导模型；④同风险面（上下文溢出/静默截断/质量退化）在 practice 域未收敛——主观评分 prompt（subjective.rs:406-414）量级小可接受。
- 影响范围：挂载多份长资料的方案生成练习/考试（单请求 96k 字符，超模型窗口时请求失败或质量退化且无提示）；≤64k 的常规资料组合与 knowledge 问答（有总闸）不受影响；资源与质量面，无数据损坏。
- 修复方向：复用 knowledge 的总预算模式（逐份扣减 + truncated 提示），上限对齐 64_000。

## 信息

### 123#3 「请求了多种类别时每类至少 1 道」仅为提示词约束——kinds 元数据可虚标，侧栏「题型：单选、填空」与实际不符

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:430`（prompt 文案「每种类别至少 1 道，除非题目总数少于类别数」）vs `:878-882`（代码校验仅 `!input.kinds.contains(&question.kind)` 子集检查）；`practice_sets.kinds_json` 按请求记录。
- 传递链条：①请求 ["choice","fill"]；②模型全返回 choice——子集检查通过、建集成功；③kinds_json 仍记 ["choice","fill"]；④Practice.tsx:262 侧栏与完成页按元数据显示「题型：单选、填空」——与实际题型不符（SubjectivePractice 空态有「以模型实际生成为准」对冲，Practice 侧栏没有）；测试无「每类至少一题」断言（mock 恰好按类别返回）。
- 影响范围：模型未按提示覆盖全部请求类别的生成（元数据虚标误导侧栏展示）；模型守约的生成与单类别请求不受影响；纯元数据准确性。
- 修复方向：建集时按实际题目回填 kinds_json（或删提示词承诺 + 侧栏加「以实际生成为准」）。

### 123#4 主观评分无并发上限——整卷主观题提交即按题扇出线程/连接/模型请求

- 位置：`desktop/src-tauri/src/backend/practice/subjective.rs:196-203`（submit 内 active.insert + launch）、`:221-271`（launch 逐 answer spawn `practice-grade-{id}` 线程，每线程 `state.store()` 新连接 + 一次 max_tokens 4096 请求）；`grading_active` 无界 HashMap。
- 传递链条：①20 题全主观试卷快速连续提交；②并发 20 个评分线程、20 条 SQLite 连接、20 个并发模型请求；③生成域每 set 单 worker 的口径在此不适用；④stop() 的 15 秒等待窗（:658-670）在大量并发评分时可能超时报 grading_active，连带 reset/停机路径失败。
- 影响范围：大卷主观题快速提交的会话（资源尖峰 + 停机路径可能超时）；少量主观题或逐题提交不受影响；资源面观察，非确定性故障。
- 修复方向：评分扇出加信号量（如同时 ≤4）或改单 worker 队列；stop 等待窗按活跃数放宽。

### 123#5 ExamWorkspace 交卷按钮 pending 口径窄于后端——评分 failed/interrupted 时按钮亮着可点但必失败

- 位置：`desktop/src/pages/ExamWorkspace.tsx:43`（`pending = ...['grading','cancelling'].includes(status)`）+ `:221`（`disabled={pending || busy}`）vs `desktop/src-tauri/src/backend/practice/exams.rs:107`（后端 pending 统计 `status != 'done'`，含 failed/cancelled/interrupted）。
- 传递链条：①主观题评分 failed/interrupted；②前端 pending=false → 交卷按钮可点；③点击 → 后端 `grading_in_progress` 报错（类型化横幅兜底，题面上本有「恢复评分」入口）——交互噪音。
- 影响范围：评分异常态下点交卷的用户（一次必败请求+横幅）；正常评分中（两端口径一致）不受影响；纯交互一致性。
- 修复方向：前端 pending 改 `status != 'done'` 同口径（或按后端返回的 grading_in_progress 状态置灰）。

### 123#6 变式生成无每错题/每日次数上限——UI 固定 count 3 可对同一错题无限次点击

- 位置：`desktop/src-tauri/src/backend/practice/mod.rs:511`（count 1..=20 与普通练习同界）+ `desktop/src/pages/Practice.tsx:203-219`（每次点击一个 8192 tokens、最多 96k 字 prompt 的模型请求）。
- 传递链条：①错题面「生成变式」按钮无冷却/计数；②连续点击 N 次 = N 个独立模型请求；③单请求预算有界、但无频次闸——资源面观察（对照：方案域局部重生成有配额公式）。
- 影响范围：高频点击变式生成的用户（模型请求数线性增长）；单次生成有界（count 1..=20、单请求预算封顶）与常规练习生成不受影响；纯资源频次面，无功能损坏。
- 修复方向：按错题加每日上限（对齐局部重生成配额口径）或 UI 防抖。

## 待核（设计权衡）

### 123#7 主观评分永久失败的考试无法交卷——作答不可重答/不可弃，缺终局出口

- 位置：`desktop/src-tauri/src/backend/practice/exams.rs:106-115`（`pending = COUNT(status != 'done') > 0 → grading_in_progress`）+ `subjective.rs:167-179`（非 done 状态重复提交报 answer_already_submitted）+ `mod.rs:1092-1105`（23#3 惰性收卷同样跳过 pending>0）。
- 传递链条：①某主观题模型反复返回无效评分（invalid_model_output）→ answer 永停 failed；②不能重答（question_id 已占用）、无删除作答命令、到期惰性收卷跳过 pending；③考试无法交卷——「请完成或恢复评分后再交卷；不会伪造评分结果」表明是有意取舍，但缺「放弃本题按 0 分交卷」类终局出口。
- 影响范围：模型持续失败的主观题考试（死锁在未交卷态）；模型正常的评分不受影响；列待核——需产品确认是否接受该权衡。
- 修复方向：提供「放弃该题按 0 分收卷」的显式出口（用户主动选择，不违反不伪造承诺）。

## 核实通过项（免后续复查）

- **98#5 取消口径三域一致**：生成终态 UPDATE 带活跃态守卫，cancelling 时 changed=0 → Err → 事务回滚题目整体丢弃（mod.rs:982-991）；主观评分 `AND status='grading'`（subjective.rs:468-477）；knowledge `AND status='running'`（:702-708）；三个 fail_* 均 `status IN (活跃态,'cancelling')` 分流正确；流返回后的二次取消检查齐全（各有测试）。
- **状态机与恢复闭环（112#1）**：三处 recover 活跃态守卫 + changed==0 跳过；interrupted→cancelled 事件成对；resume 仅 `interrupted && completed_chat` 复用原 request_id/attempt 否则 +1（不重复请求测试在案）。
- **掌握度与 BFS**：行级过滤（score∈[0,max] 且 max>0）+ SUM/SUM 加权（89#3 一致）；「每个掌握度都有证据」成立；BFS 确定性排序、环/未知节点安全、类型化报错。
- **SM-2 与文案**：again 清零/hard/good/easy 系数、ease 收敛、36500 封顶（47#3）；Review.tsx 提示系数逐一推演不冲突；「40 秒/张」「保持率口径」与 stats_with 逐字对上；到期日界 47#2 本地日界全链。
- **hints/考试遮蔽族**（除 123#1 分数通道）：set 级遮蔽、逐题换 hint、错题本双路径守卫、evidence 跳过、掌握事件不下发、错题面前批量收卷、evidence 延迟统一补写幂等、计时惰性起算原子置位（98#1/101#4/47#1/13#24/23#3 各有测试）。
- **跨集合查重（98#2/101#6/101#7）**：负约束入 prompt、CSPRNG 批次标识、seen 排除、写事务内前缀复核、变式全等单独报错。
- **掌握传播链（03#13-25）**：variant_of 上溯、环守卫、手动标记清除（二级变式测试在案）。
- **knowledge 问答流**：64k 总预算 + truncated 警告、坏引用剔除不废整答、materials 同事务 FK 防误删、五命令幂等与活跃守卫齐全。
- **前后端契约**：practice 16/review 9/knowledge 8 命令与 TS 一一对应（camelCase 转换正确）；字段与 serde rename 逐一对上；事件前缀与 KnowledgeMap 硬编码 kind 全为真实发射；分页上限（history 50/mistakes 500/sessions 100/review 200/搜索截断 40）两侧一致。
- **评分口径（77#11）**：rubric 2-6 项、points 总和 1、单点 (0,1]、1e-6 舍入、总分 clamp 兜尾差；无效评分不入 evidence/错题本（测试在案）。
- **防御性输入**：count 1..=20、题型白名单、exam 参数互斥、节点/阶段双重范围校验、主观 30k 字节、毒数据类型化不 panic、中文全角归一。
- **前端守卫族**：51#1/76#2/76#8/120#5 序号守卫、28#2/76#3 草稿持有、32#14 直播错误不冒泡、61#2 倒计时以后端为基准、19#1/68#2/68#6 键盘处理全部在位。
