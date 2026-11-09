# 69 · SQL 拼接面与前端竞态码表（第二十六轮·主审自查轴）

> 审计范围：全仓 16 处 `format!` 拼 SQL 逐处溯源；后端全部竞态类错误码与
> 前端 isCancelRace 表比对（逐码 BackendError::new 验证区分真错误码与命令名）；
> 构建产物分包结构。

## P3

### 69#1 isCancelRace 竞态码表缺 5 个真实错误码——取消/专注操作窄窗弹非静默错误

- 位置：`desktop/src/backend/client.ts:461-486`（表 19 码）↔ 后端逐码验证（BackendError::new 计数 >0 才算真码）。
- 缺失（均为真实错误码）：
  - `no_active_focus`（reports/mod.rs:372/381/467，3 处：pause/cancel/finish 条件 UPDATE 未命中）
  - `focus_active`（reports/mod.rs:342/1185，2 处：start 双开防护）
  - `no_paused_focus`（reports/mod.rs:499：resume 未命中暂停态）
  - `job_active`（materials/mod.rs:937：重复入队）
  - `request_active`（providers/mod.rs:1175、routing.rs:548/924，3 处：取消/复用窗口）
- 传递链条（以专注层为例）：① 专注层状态来自事件刷新（focusSession 快照），倒计时归零自动收尾（App.tsx remaining===0 → completeFocus）与用户手动点击完成/暂停存在并发窗口 → ② 第二次操作到达后端时条件 UPDATE（status IN running/paused）未命中 → ③ 返回 no_active_focus「当前没有进行中的专注」→ ④ 前端 isCancelRace 表不含该码 → 弹错误 toast——按 29 轮以来的竞态静默口径（no_active_plan 等 19 码同族）应静默或自愈刷新。job_active（重复入队）/request_active（取消/复用窗口）同型：UI 有 busy 守卫，但事件滞后下的双击仍可穿透到错误 toast。
- 影响范围：使用专注计时（暂停/恢复/取消/完成）、KB 文档「开始解析」、模型请求「取消」的高频操作用户；触发需状态滞后窄窗（每类每用户偶发），后果为一次不该出现的错误提示——无数据损害（操作本身幂等或已生效），与全站竞态静默口径不一致造成困惑。修复：五码补入表内（一行级）。

## 正向结论

### SQL 拼接面全部闭合

16 处 `prepare/execute(&format!` 逐处溯源：表名/列声明全部来自**编译期常量或白名单常量**——store/practice 三处 ensure_column（硬编码调用点）、community/agent/plugins 的 DELETE FROM {table}（数组字面量）、providers/review 的 {PROFILE_COLUMNS}/{CARD_COLUMNS}（代码常量）、**backup/transfer 的 PRAGMA/SELECT "{table}"（全部迭代自 archive::TABLES 67 张白名单，无 sqlite_master 动态枚举进 SQL）**——恶意备份文件无注入路径。

### 构建分包（信息）

vite 无 manualChunks；katex/remark 共享 JS 块物理落在 MediaPlayer chunk（440KB，与入口 chunk 等大）——本地 WebView 加载无性能影响，如需缩减首屏可选做 vendor 分包。main.tsx 全局引 katex CSS 为有意设计（懒加载页也渲染公式）。
