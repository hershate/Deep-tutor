# Deep-tutor 架构文档

**分析基准**：main 分支滚动演进（不钉单一 commit；`文件:行号` 引用以撰写时的 HEAD 为准，随开发可能移位）
**分析范围**：`desktop/`（Tauri 2 应用全量；口径为 src/ 与 src-tauri/src/ 下全部源码文件。规模与命令数等量化口径随开发演进，权威口径以 `note/14-Tauri原生后端覆盖清单.md` 为准）
**分析模式**：全量静态分析，未运行应用

## 项目快照

- **项目**：知舟 Deep Tutor —— 个人学习操作系统桌面应用
- **技术栈**：Rust（Tauri 2 壳 + 原生后端 + rusqlite）、React 18 + TypeScript 5.6 + Vite 5
- **持久化**：SQLite（WAL，根库=账户 + 每用户工作区库），文件区存原件/产物
- **规模**：191 个 Tauri 命令、21 个后端域模块（backend/ 域目录）+ backend 根（commands/store/error/random/mod）、16 个页面、25 个前端客户端文件（src/backend/*.ts）、195 个后端测试（串行全绿）
- **CI**：GitHub Actions windows-latest（前端构建先行 + fmt + 原生测试串行 + 版本校验 + NSIS 安装包；许可证收集在 NSIS 步骤的 beforeBuildCommand 内执行；push:main / PR / 手动触发）
- **分支**：main（单线演进，细粒度中文提交）

## 文档目录

| 文档 | 内容 | 图表 |
|---|---|---|
| [01 架构分析](01-architecture.md) | 分层与模块清单、横切层（错误/存储/守卫/事件）、各域数据结构与 SQLite 表、模块依赖图、前端架构、命令注册总览 | 进程总览、双库结构、守卫判定、用户切换时序、模块依赖 共 5 图 |
| [02 运行原理](02-operation-principles.md) | 启动序列、schema 指纹迁移、事件与 Last-Event-ID、八步方案流水线、提供商流式与路由熔断、材料解析队列、Agent 工具循环、判分与复习算法、状态机汇总、并发模型 | 启动时序与目录决策、Store 打开链、事件写路径、八步流水线、直连流式时序、路由候选循环、熔断状态机、材料导入与解析队列、Agent 工具循环、抓取校验、判分分流、复习调度、备份导出/导入、分享时序、同步保留、通用任务状态机、停止与重置、前端启动与实时流 共 23 图 |
| [03 工作流分析](03-workflow.md) | 构建/CI/测试策略、学习闭环业务流与决策链、数据生命周期、异常恢复矩阵、安全工作流、页面×命令矩阵 | CI 管线、学习闭环、材料决策、方案生命周期、变式闭环、项目反馈、专注状态机、引用校验、预约提醒时序、数据生命周期、密钥生命周期 共 11 图 |

## 核心发现

1. **单进程全原生**：WebView 零业务逻辑，全部 I/O（SQLite/文件/HTTP/密钥库）在 Rust 侧执行；前端唯一事件通道 `backend://event` 与命令名和注册表完全一致。
2. **统一任务生命周期**：11 类长任务实体共用同一模式——SQLite status 列 + AtomicBool 取消注册表 + `providers::completed_chat` 请求复用 + 重启 `recover`（running→interrupted、cancelling→cancelled、不自动重跑）。
3. **终态先落盘再发送**：所有事件经 `Store::event` 在写事务内进 outbox 表，commit 后才 emit；断线续接靠 `get_app_state` 全量快照校准（游标取快照 `lastEventId`，38#2 后不再逐页翻 `events_since`）+ 任务层请求复用，不重复执行或计费。
4. **安全以结构 enforcement**：SSRF/DNS pin/重定向逐跳校验、密钥仅入 keyring、备份 secure_delete 排除凭据、分享令牌只存哈希、配额提交前强制——均可在具体函数定位。
5. **算法可定位**：八步流水线（learning/pipeline.rs execute_index:527）、SM-2 简化调度（review/mod.rs:424 起）、贪心排期（learning/validate.rs:206）、BFS 补学路径（learning/knowledge.rs:115）、熔断状态机（providers/routing.rs:626 起）。

本次按约定未生成 AI 替代方案与 Skill 蓝图。
