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
- 链条（以专注层为例）：App.tsx:250/258 的 pause/resume/cancel/complete 在状态滞后窗口（如倒计时归零自动收尾与手动点击并发）→ 弹「当前没有进行中的专注」错误 toast——按 29 轮以来的竞态静默口径应静默或自愈刷新。job_active/request_active 同型（UI 有 busy 守卫但事件滞后双击仍可露）。
- 修复：五码补入 isCancelRace 表。

## 正向结论

### SQL 拼接面全部闭合

16 处 `prepare/execute(&format!` 逐处溯源：表名/列声明全部来自**编译期常量或白名单常量**——store/practice 三处 ensure_column（硬编码调用点）、community/agent/plugins 的 DELETE FROM {table}（数组字面量）、providers/review 的 {PROFILE_COLUMNS}/{CARD_COLUMNS}（代码常量）、**backup/transfer 的 PRAGMA/SELECT "{table}"（全部迭代自 archive::TABLES 67 张白名单，无 sqlite_master 动态枚举进 SQL）**——恶意备份文件无注入路径。

### 构建分包（信息）

vite 无 manualChunks；katex/remark 共享 JS 块物理落在 MediaPlayer chunk（440KB，与入口 chunk 等大）——本地 WebView 加载无性能影响，如需缩减首屏可选做 vendor 分包。main.tsx 全局引 katex CSS 为有意设计（懒加载页也渲染公式）。
