# 前后端连接审计问题集

> 生成日期：2026-11-06，基准 main 分支 commit `5851662d`（1.0.5）。
> 审计范围：`desktop/src/backend/*.ts` 全部 24 个客户端、`desktop/src/pages` 16 个页面连接层、
> `desktop/src-tauri/src/backend` 22 个域的全部命令与事件、`backend://event` 事件通道、
> 以及 README/PRODUCT/note/plan 文档声明。
> 方法：脚本机械比对（命令注册表/参数名/事件 kind 双向）+ 六个域的逐文件人工核对。

## 问题索引

| 文件 | 内容 | 数量 |
|---|---|---|
| [01-功能缺陷-P2.md](./01-功能缺陷-P2.md) | 功能不可用或行为与承诺相反 | 3 |
| [02-体验与文案-P3.md](./02-体验与文案-P3.md) | 刷新缺口、状态残留、文案与行为不符 | 14 |
| [03-待确认问题.md](./03-待确认问题.md) | 竞态/设计意图存疑，需人工判定 | 5 |

严重度定义：P1 = 阻断核心流程（本次未发现）；P2 = 功能不可用或与承诺相反；
P3 = 体验缺陷、契约残留或文案夸大，不阻断使用。

## 已验证一致面（未发现问题）

以下维度经机械比对 + 逐域核对，确认前后端一致，未列入问题：

- 命令注册：前端 188 个 invoke 命令全部在 `main.rs` 注册表中；参数名 camelCase→snake_case 零失配。
- 类型契约：各域 Rust 结构体 `rename_all="camelCase"` 与 TS 接口字段一一对应，
  Option 可空性、u32/u64/i64 与 number 对应正确（仅 `ProviderRequest.toolCalls` 一处漂移，见 02）。
- 事件监听路径：后端 116 种事件 kind 均有前端处理路径（精确匹配、前缀匹配或
  useBackendState 全量快照重同步兜底）；`state.changed`/`study.*`/`lecture.*`/`security.*`
  等 payload 字段名与前端读取一致。
- 长任务闭环：方案生成/练习/评分/讲义/Wiki/项目反馈的启动-事件驱动刷新-恢复（resume_*）链路成立；
  中断态由启动时 recover() 落盘。
- 文档核心声明：18 供应商预设、8 导出格式、四源输入、八步流水线、五维校验、JWT 七日 HS256、
  PAT `dtp_` 前缀仅存摘要、分享只读可撤销可过期、配额提交前强制、切换前停止任务、
  Web 抓取限制全套、ZIP/笔记限制、同步 24h 与保留 10 版本、强度比例防累积缩放——均有实现支撑。
- 错误码集合：前端 `authenticationErrors` 五码与后端 `reauthentication_required` 完全一致；
  启动/存储错误码全部落入可恢复的错误页。

## 已知架构背景（不属于本问题集）

- 全部 190 个命令为同步命令，在 WebView2 主线程执行（命令延迟直接冻结窗口）。
- `authenticate_access_token`、`stream_provider_chat_with_tools` 为注册但前端不直接调用的预留入口。
