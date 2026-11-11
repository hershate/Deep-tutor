# 137 号：Rust 健壮性横切问题集（第四十四轮）

审计基线 `f1b2688`。审计面：desktop/src-tauri/src/ 全仓横切——非测试路径 panic 面
（词法扫描剥离注释/字符串、精确排除 cfg(test) 区段，84 文件）、整数与解析、路径与文件系统、
锁与并发、SQLite 使用、进程与句柄。经子代理横切扫描 + 主审实证（两 P3 逐行复核）。
共 2 P3 + 4 信息。

## P3

### 137#1 materials 原件路径由库值无校验拼接——敌意备份/改库后删除与导出可越出工作区

- 位置：`desktop/src-tauri/src/backend/materials/mod.rs:374-375`（`fn original_path(root, sha256, kind) = root.join("originals").join(format!("{sha256}.{kind}"))` 无格式校验）；消费点 `:1401-1406`（删除：`fs::remove_file(original_path(&state.root(), &material.sha256, &material.kind.to_ascii_lowercase()))`）与 `:1426-1429`（导出：`fs::read(original_path(...))`）；`material()` 行映射 `:379` 起 `sha256/kind` 直接取库值，`original_available` 仅按该路径 `is_file()` 判定。
- 传递链条：①`materials.sha256/kind` 列可经敌意备份或外部改库植入任意串——备份导入只校验 manifest 条目的哈希格式（backup/archive.rs:666 `valid_hash`），而 transfer.rs:350 从内嵌 state.db 逐字复制 materials 行、无列值校验；②`material()` 原样读出；③`original_path` 拼出 `…/originals/..\..\目标.txt`（kind 自由，后缀约束形同虚设），`original_available` 因目标真实存在文件而为 true；④删除命令越出工作区删除该文件（导出命令则为任意位置文件读取复制）。对照：maintenance 域对同一数据源有完整防线（maintenance/mod.rs:272-283 hex64+字母数字≤12 校验，:708-717 remove_file 还做 canonicalize 包含检查）——materials 域是同一威胁模型（47#3 已把外部改库纳入）下的漏网点。
- 影响范围：受影响=导入敌意备份（或直接改库）后在 UI 删除/导出该资料的用户——工作区外任意已存在文件被静默删除或被读取复制；不受影响=正常写入路径（mod.rs:504 SUPPORTED 白名单+进程内计算哈希）；损害性质=越界删除/读取，本地单用户场景下属防御纵深缺口。
- 修复方向：`material()` 行映射或 `original_path` 入口对 sha256 施加 64 位小写 hex、kind 施加 SUPPORTED 白名单校验（镜像 maintenance/mod.rs:272-283），毒数据返回类型化错误；备份导入侧对 materials.sha256 列补 valid_hash。

### 137#2 kv→materials 一次性迁移非事务循环——崩溃窗口造成永久半应用且自锁不续跑

- 位置：`desktop/src-tauri/src/backend/materials/mod.rs:202-233`——`:214` `SELECT data FROM kv WHERE namespace='resources'` 后 `:217-224` 循环逐行 `import_metadata(conn, ...)`（各自自动提交，无包裹事务）；`:226-231` 全部成功后才写 `materials_kv_imported` 标记；`:202-208` 重跑时 `has_materials`（部分行已导入即 true）→ 直接补写标记并 return。
- 传递链条：①旧版用户库首开进入迁移（store.rs:455-461 指纹/版本不匹配触发）；②逐行自动提交；③进程在循环中途崩溃/断电；④下次打开 migrate 重入——`has_materials` 已 true，:206-212 写死标记，剩余 kv 资源永不进 materials（kv 原行仍在库中，数据本体未丢，但应用不再自愈）。
- 影响范围：受影响=「旧版 kv resources 数据+迁移中途崩溃」交集场景（窗口窄）；不受影响=新库、已迁移库、崩溃发生在标记写入后。损害性质=资料列表永久缺行。对照：store.rs:455-487 其余迁移段以幂等设计（CREATE IF NOT EXISTS+ensure_column 先探测）保证重跑收敛——本循环是唯一非幂等且以完成标记自锁的段。
- 修复方向：逐行导入与标记写入包进单个 `write_transaction()`（行数有限事务可控）；或改按 kv 行主键幂等合并（UPSERT+最后标记）使重跑安全。

## 信息

### 137#3 attempt 裸 +1 与 checked_add 并存——防御口径不一致

- `practice/mod.rs:1761`、`practice/subjective.rs:895`、`knowledge/mod.rs:824`、`agent/mod.rs:882` 均裸 `attempt + 1`；而 `learning/lecture.rs:489-493`、`learning/mod.rs:705-708` 按 25#6 用 `checked_add(1)` 并注释「release 下回绕」。i64 实际回绕需 2⁶³ 次重试不可达——仅口径不一。修复：统一 checked_add。

### 137#4 DB 值 i64→u32/u8 截断（展示面，仅外部改库可达）

- `agent/mod.rs:417/432/709`（`row.get::<_, i64>() as u32`）、`review/mod.rs:244-245`（repetitions/lapses）、`practice/mod.rs:1207`（difficulty `.max(0) as u8`）、`reports/mod.rs:1138`（比值超 255 回绕）。仅统计展示错值无 panic；浮点转整型 Rust 1.45+ 饱和、NaN→0。修复：按需 saturating_cast 语义核对。

### 137#5 agent 侧 atomic_write 双 rename 失败时目标短暂缺失且吞错

- `agent/tools.rs:262-267`：第二步 rename 失败后回滚 rename 也失败时 `let _ =` 吞错，原内容留存 `.previous`（可手动恢复，注释已声明取舍）；materials 侧同型（materials/mod.rs:1506-1518）已把双失败显式并入用户错误消息。修复：agent 侧对齐。

### 137#6 只读连接无 busy_timeout

- `store.rs:156`（with_accounts 配额缓存连接）与 `backup/archive.rs:413`（导出读账户库）直接 `Connection::open` 未设 busy_timeout。主库 WAL 读者不阻塞，仅 journal_mode 切换等瞬时窗口可能报错；with_accounts 缓存失败还有弃连重开重试（:148-154）。实害极低，补 `pragma_update busy_timeout` 归一口径。

## panic 面清单结论（任务横切结论，免后续全量复查）

非测试代码命中 **28 处**（含已立案 133#5 的 main.rs:441）：
- **a 类 锁中毒 unwrap：0 处**——生产代码锁获取全部 `map_err` 转类型化错误或 `unwrap_or_else(into_inner)`（sync/scheduler.rs:92/133、reveal.rs:13-18、commands.rs:216-217、store.rs:145-147/263-266、guard.rs:265-268）。
- **b 类 用户输入可达：0 处**。
- **c 类 逻辑不变量（当前不可达）：25 处**——6 处 resume unwrap（agent/mod.rs:887、knowledge/mod.rs:827、lecture.rs:496、practice/mod.rs:1764、subjective.rs:897）由 `completed_chat(None)=Ok(false)`（providers/mod.rs:1046-1049）单点语义保证；transfer.rs 8 处 expect 由 read_tables 必插/:851 is_array/:1117 present 过滤保证；review/mod.rs:450/460/493 由 :411 四值白名单保证；validate.rs:101/117 由 ids/indegree 同源+Kahn 每边恰一次递减保证；anthropic.rs:56 由 is_array 守卫；4 处 unreachable!()（anthropic:351/gemini:315/openai:967/responses:378）位于 loop{} 内全路径 return 之后；main.rs:178 位于无限 for 后；security/mod.rs:264/270 由构造守卫保证。
- **d 类 第三方返回值：1 处**——agent/mod.rs:82 `serde_json::to_string(&str)` 对合法 UTF-8 切片（char_boundary 已保证）不可能失败。

## 其他横切面结论（均无新立案）

- **整数与解析**：37 处 parse 全部失败安全（.ok()/?/map_err）；变量边界切片全部锚定 ASCII find/char_indices/is_char_boundary 循环（notes/mod.rs:38-44、web/mod.rs:332-338/364-370、learning/actions.rs:8-17、html.rs:213-263、pipeline.rs:181-192、protocol.rs:39-57）；无符号减法全有守卫；pdf.rs:82 切片由 :42-45 构造保证界内。
- **路径**：agent/tools.rs:167-216（组件白名单+canonicalize 包含+symlink 拒绝+扩展名白名单）、backup/archive.rs:194-208（拒非 Normal 组件/绝对路径）、mod.rs:69-88 lexical_absolute+:55-64 reject_data_root_destination、sharing/mod.rs:440-445（parse 门控非 UTF-8 跳过）均验证通过；26 处 `let _ = fs::…` 均为清理/回退/诊断路径有依据（实质风险即 137#1，语义缺口即 137#5）。
- **锁与并发**：54 个 async fn 的 await 全部 spawn_blocking 型（无 MutexGuard 跨 await）；锁序全局一致（WORKSPACE_SWITCH → SecurityState.connection → BackendState.store → 静态锁，无反向获取）；security/mod.rs:109-128 spawn 强制 'static+Send；scheduler.rs:118-131 join 持锁已论证无死锁。
- **SQLite**：21 处 format! 拼 SQL 标识符全部来自常量；敌意备份列名注入被 transfer.rs:172-181（源列须目标 schema 子集，08#9）阻断；事务创建/提交逐文件平衡（26 文件 157 处）；早退依赖 Transaction Drop 回滚（正确）；主连接统一 WAL+busy_timeout 10s+foreign_keys ON。
- **进程与句柄**：`Command::new` 全仓 0 处；std::process 仅 2 处 exit(2)（单实例二实例退出）+5 处 process::id() 临时命名；临时文件全部模式化（.tmp/.download-N/.upload-N/.previous-N/.restore-N）配错误路径回收+维护孤儿扫描兜底。

## 核实通过项

1. 28 处 panic 命中逐条定性完成（证据链见上），无一锁中毒、无一用户输入可达。
2. 已立案项现状核实：133#5 main.rs:441 仍在；127#9 catch_unwind 现存 5 域（knowledge/mod.rs:470、practice/subjective.rs:255、main.rs:58、agent/mod.rs:556、learning/lecture.rs:333）；130#9 相关 fail() 现存 knowledge/mod.rs:719——未重复立案。
3. 备份导入三重防线（manifest 哈希格式 archive.rs:666、表集合 :641、列子集 transfer.rs:172-181）有效——137#1 是列值层而非 schema 层，已单独立案说明。
4. Windows 保留名（CON/PRN 等）：Component::Normal 不拦但写入失败走 ? 类型化错误，无 panic 面。
5. 审计过程中子代理曾在 src 下误建 panic_hits.txt，已即刻删除，工作树经 git status 确认与基线一致。
