# 39 · 后端深读 III（第十七轮）

> 审计范围：补第十六轮如实标注的「抽查未逐行」部分——materials 全域（mod 1827 行
> + commands + archive 防线）、knowledge 核心（引用定位/检索/问答/取消/恢复）、
> engagement（全局检索/通知/免打扰）、maintenance 全文（688 行）、startup 前 360 行、
> backend/commands.rs、export pdf/anki 结构、前端 useAgent/client.ts 常量层
> （ids/limits/status/data）、docs/architecture 引用抽验、CI/脚本/公共资产。
> 每条附传递链条与影响范围（文末）。

## 信息性

### 39#1 knowledge::search 每份资料新开一条 SQLite 连接并全文加载——N+1 连接模式

- 位置：`desktop/src-tauri/src/backend/knowledge/mod.rs:405-427`（search 对
  `materials::list` 的每份资料调 `materials::detail`）；`materials/mod.rs:95-97`
  （`fn store(&self)` 每次调用 `Store::new` 新开连接）；`store.rs:129-175`
  （每次 open：建连 + uni_lower 注册 + WAL/外键 PRAGMA + schema 指纹
  SHA-256 计算与迁移判定）
- 证据：Wiki 检索按钮一次点击 = 1 次 list 连接 + N 次 detail 连接 + N 份
  material_text 全文（单份上限 500KB）读入内存。100 份资料的库一次检索约开
  百余条连接、加载最多数十 MB 文本。按钮触发而非逐键，本地规模可接受。
- 传递链条：检索延迟随资料数线性增长（本地每次连接约 1–3ms + 文本读取）；
  无正确性影响。影响范围：大资料库用户的 Wiki 检索响应。可复用同一条连接
  或在 detail 层提供批量接口收敛。

## 深读正向结论（零缺陷记录）

| 域 | 覆盖 | 结论 |
|---|---|---|
| materials | mod.rs 全文 + commands + archive | 单解析 worker 与「先登记下一个再退役旧的」不变量成立（schedule_next 以 from_worker 豁免限额恰为实现该语义）；34#12 条件更新正确挡住「取消落终态后迟到解析线程复活」竞态（含 enqueue→排队未 spawn→cancel 的窗口）；去重/原件恢复（11#15）/并发同哈希撞 UNIQUE 不误删（25#11）均自洽；入口 32 文件上限、ZIP 64 条目/单条 50MB/总量 200MB 防线齐全 |
| knowledge | 引用/检索/问答/取消/恢复 | 引用逐字定位（大小写不敏感、存储原文匹配段而非模型原话）；64K 总预算截断带警示；sufficient 但引用全失定位即整体拒绝；恢复复用已完成的 interrupted 请求不重复计费；终态条件更新防取消竞态 |
| engagement | 全局检索/通知/免打扰 | kv 检索只匹配真实字段值 + uni_lower Unicode 折叠（31#23）；通知口径与「近 7 天」文案一致（31#29/C-10）；免打扰跨午夜区间正确 |
| maintenance | 全文 | 健康检查先行（完整性/外键不过即拒绝清理）；符号链接/深度/规范化三重删除遏制；24h 进行中宽限且删除前复查（31#32）；批次 500 + 失败不占额（B-12）+ 宽限项不计 remaining（34#18）；事件表 90 天且保留最近 2 万条裁剪 |
| startup | 目录校验/指针写入/装配 | UNC/verbatim 前缀剥离后黑名单比较（25#18）；指针原子写（tmp+rename+fsync）；失败语义分层（配置坏→拒绝打开其他工作区） |
| backend/commands | 共享外壳 + reset_application | reset 先停全部域任务再清库，文件/凭据/插件三段收尾按 DB 成败分支 |
| export | pdf.rs 字体子集器 + anki 结构 | TTF 解析全程边界检查、缺表逐类 typed error；anki 字段过 escape_html、稳定 id |
| 前端常量层 | ids/limits/status/data.ts | localId 严格递增；clampToBytes 二分+代理对清理（B-1）；状态名映射与后端状态机集合一致 |
| docs/architecture | 01/02/03 引用抽验 | 完整路径引用 7 条全部命中（含 startup.rs:27、materials mod.rs:32、knowledge mod.rs:29 与实读行号逐字一致）；tauri.mjs:24-38 存在；文档显式锚定 df63ee0f 基准，行数表随基漂移属声明范围 |
| CI/脚本/资产 | ci.yml、collect-licenses、public/icons | CI 与 Makefile/README 口径一致（钉 1.95.0、串行测试、--locked 透传、版本检查、许可收集）；favicon/icon 存在 |

## 传递链条与影响范围汇总

| # | 链条 | 用户可感知影响 |
|---|---|---|
| 39#1 | search → 每资料 detail → Store::new + 全文读取 | 大库检索延迟线性增长，无正确性影响 |
