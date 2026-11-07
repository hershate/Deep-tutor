# 问题集（第十六、十七轮全量审计）

> 第十五轮问题（34–36 号）已全部修复并删除（35b8511e）。
> **第十六轮**：十五轮修复之后的全仓复审。原计划 5 路并行子审计因运行环境
> 凭证不可用全部失败，改由主审独立完成——契约四轴机械比对 + 十六轮修复
> 回归 diff 逐 hunk 审查 + 前端 16 页面/17 组件全量通读（本轮重点：
> **UI 操作人性化**，应用户型审视）+ 后端少审域抽查 + 文档/配置对齐复验。
> **第十七轮**：补深读——第十六轮如实标注「抽查未逐行」的后端大文件
> （materials/knowledge/engagement/maintenance/startup/commands/export）
> 逐行通读 + 前端常量层 + docs/architecture 引用抽验 + CI/脚本/资产。

## 基线（审计起点，全部真实执行）

| 门禁 | 结果 |
|---|---|
| `cargo test --release --locked -- --test-threads=1` | 194/194 通过（42.46s） |
| rustfmt（gnu 工具链直跑全目录 `--check`） | 干净 |
| `node scripts/check-versions.mjs` | 五处 1.1.0 |
| `cd desktop && npm run build` | 通过（3.69s） |

基准 commit：`070852e4`（十五轮收口后的全仓文档对齐）。

## 机械契约复验结论（第十六轮，零失配）

- **命令轴**：`generate_handler` 注册 187 个；前端 185 个自有 invoke 全部已注册
  （含 `export_practice_mistakes`——首轮脚本误报死于嵌套泛型正则，实际经
  `practice.ts:115` 由 Practice 页调用）；`fetch_webpage` 为唯一无 UI 调用命令，
  `web.ts:4-6` 有文档豁免注记（保留给 import_webpage 复用通道）。**零未注册调用、零意外死命令。**
- **事件轴**：十五轮零失配基线（7aa97838）以来的增量 diff 仅引入
  `"app.db"`（测试夹具文件名）与 `"workspace.read"`（工具名，非事件）两个字符串，
  无新事件 kind；前端监听增量仅 UsagePanel 35#1 精确终态清单一处，清单与
  providers 实际发射的 8 个终态 kind（chat/route × completed/failed/cancelled/interrupted）逐一对上。
- **serde 轴**：抽查十五轮修复涉及的 `TokenUsage`（`openai.rs:19-24`
  `rename_all="camelCase"`）三字段与 `client.ts:102/166/230` 逐字段一致（35#10 修复自洽）。
- **capabilities 轴**：授权恰为 `core:event` + `dialog:allow-open/save` 四项，
  与实际 `plugin:dialog|open/save` 调用一一对应，无冗余无缺漏。

## 后端抽查正向结论（本轮新增核验）

- `web/mod.rs:78-144` SSRF：DNS 预解析 + `resolve_to_addrs` 钉死 IP（防 rebinding）、
  凭据/密钥查询参数整段拒绝、私网/保留地址拦截、重定向手动跟随且逐跳重新解析、限深 3。
- `security/mod.rs:591-616` JWT：alg 钉 HS256、常量时间比对（08#5）、exp/iat 窗口校验。
- 动态 SQL（community/agent/plugins 的 `DELETE FROM {table}`、`count()`、backup 导入）全部来自代码内白名单常量，无用户输入拼接。
- `reject_data_root_destination` 二道遏制覆盖 export/backup/materials/reports/sync/sharing 六个写盘域 commands。
- `sync/scheduler.rs` 调度器启停持锁全程（E-1 注释与实现一致）；`random.rs` Windows 侧 BCryptGenRandom 系统首选 RNG，语义正确。

## 文档/配置复验正向结论

- README（根/desktop）构建命令与 Makefile/CI 逐条一致；目录表全部条目存在
  （docs/architecture、plan/README.md、.githooks/pre-push、.github/workflows/ci.yml）。
- DESIGN.md 基线值抽验：最小字号 12px（styles.css 全树最小值恰为 12）、
  行高 1.85、248px 侧栏、64px 顶栏、字体栈逐字一致。
- 版本五处 1.1.0（check-versions 门禁）；`note/release/1.1.0.md` 在位。

## 问题分布

| 文件 | P1 | P2 | P3 | 信息性 | 主题 |
|---|---|---|---|---|---|
| 37-前端交互人性化III.md | 0 | 0 | 5 | 4 | 破坏性操作确认漏网、拖放开启无处理器、无「在资源管理器中显示」、导出方式不一致等 |
| 38-状态引擎与写路径III.md | 0 | 0 | 2 | 2 | 写口径漏改族 17 处、sync() 冗余事件翻页、死代码与缩进残留 |
| 39-后端深读III.md | 0 | 0 | 0 | 1 | knowledge::search N+1 连接；materials/knowledge/engagement/maintenance/startup 等深读零缺陷正向结论 |
| 合计 | **0** | **0** | **7** | **7** | 另有契约四轴/后端抽查/文档三组正向结论在案 |

两轮均无 P1/P2：十五轮修复（c3895ffe/878b345b）经逐 hunk 回归审查未发现新引入的
高危缺陷；第十七轮对剩余后端大文件的逐行深读亦零缺陷。以下 14 个条目
（含 38#1 内 17 处点位）以 P3 与信息性为主，
集中在「同一类约定修了大部分但漏了边角」的漏网族。

## 处置状态

- 本文件集只记录问题，不修改代码。
- 每条附传递链条与影响范围，便于后续判定。
