# Deep-tutor 全项目问题审查

本轮审查覆盖 69 个切片（主切片 55 个、补审 14 个），切片阶段累计记录 239 条原始发现，经跨切片合并去重与证伪剔除后定稿 167 条，编号 P-001 至 P-167 连续无缺号。定稿条目全部完成代码级复核，未复核 0 条；按严重度计，高 11 条、中 61 条、低 95 条。另有 15 条疑似问题经复核证伪，不计入定稿数，逐条列于文末附录。

条目编号在定稿时统一分配为 `P-xxx`；同一问题被多个切片独立发现的合并为同一条，合并来源记录在该条的「合并来源」节。各分组文件内的元信息行格式存在「严重度：高/中/低」与「severity: high/medium/low」两种写法，本索引统一归一为高/中/低。

## 严重度与状态分布

| 严重度 | 已复核 | 未复核 | 合计 |
| --- | --- | --- | --- |
| 高 | 11 | 0 | 11 |
| 中 | 61 | 0 | 61 |
| 低 | 95 | 0 | 95 |
| 合计 | 167 | 0 | 167 |

| 分组文件 | 编号范围 | 条数 | 高 | 中 | 低 |
| --- | --- | --- | --- | --- | --- |
| [01-初次使用与新手旅程.md](./01-初次使用与新手旅程.md) | P-001、P-047–P-057、P-100、P-136、P-140–P-146 | 21 | 1 | 11 | 9 |
| [02-前端显示与布局.md](./02-前端显示与布局.md) | P-039–P-046、P-058、P-107–P-135 | 38 | 0 | 9 | 29 |
| [03-前后端与事件契约.md](./03-前后端与事件契约.md) | P-005–P-008、P-024、P-028、P-034–P-038、P-103–P-106 | 15 | 4 | 7 | 4 |
| [04-后端业务逻辑.md](./04-后端业务逻辑.md) | P-002、P-013、P-015、P-017、P-020、P-023、P-025–P-027、P-033、P-080、P-082–P-096、P-101、P-139 | 28 | 1 | 9 | 18 |
| [05-状态机取消与恢复.md](./05-状态机取消与恢复.md) | P-009、P-012、P-014、P-016、P-018–P-019、P-030–P-032、P-079、P-081、P-097–P-098、P-137–P-138 | 15 | 1 | 8 | 6 |
| [06-安全与数据边界.md](./06-安全与数据边界.md) | P-003–P-004、P-010、P-021–P-022、P-060–P-062、P-099、P-102 | 10 | 3 | 5 | 2 |
| [07-性能与响应性.md](./07-性能与响应性.md) | P-011、P-063–P-074、P-161–P-164 | 17 | 1 | 10 | 6 |
| [08-无障碍.md](./08-无障碍.md) | P-075–P-077、P-147、P-165–P-167 | 7 | 0 | 0 | 7 |
| [09-文档与构建配置.md](./09-文档与构建配置.md) | P-029、P-059、P-078、P-148–P-160 | 16 | 0 | 2 | 14 |

## 分组文件说明

| 分组文件 | 收录范围 |
| --- | --- |
| [01-初次使用与新手旅程.md](./01-初次使用与新手旅程.md) | 首次运行提示、页面导航与空态、引导路径与无供应商场景下的死路。 |
| [02-前端显示与布局.md](./02-前端显示与布局.md) | 渲染结果（残留文本、溢出、遮挡）、文案口径、网格与浮层定位。 |
| [03-前后端与事件契约.md](./03-前后端与事件契约.md) | 命令参数与返回字段、事件命名空间与载荷标识、前后端消费面的一致性。 |
| [04-后端业务逻辑.md](./04-后端业务逻辑.md) | 输入校验、数值与配额计算、事务边界、级联清理与数据一致性。 |
| [05-状态机取消与恢复.md](./05-状态机取消与恢复.md) | 终态守卫、取消竞态、重启恢复、请求复用与悬挂行清理。 |
| [06-安全与数据边界.md](./06-安全与数据边界.md) | 遮蔽面泄露、导入导出边界、路径遏制与跨用户、跨工作区隔离。 |
| [07-性能与响应性.md](./07-性能与响应性.md) | 主线程同步 IO、同步命令的执行位置、事件刷新风暴与全量重拉开销。 |
| [08-无障碍.md](./08-无障碍.md) | 可访问名、对比度、焦点管理与读屏语义。 |
| [09-文档与构建配置.md](./09-文档与构建配置.md) | 文档统计口径、构建产物清理、许可随包收集，以及文档与实现的差异。 |

## 检查范围

### 审查流水线

```mermaid
flowchart TD
  A["切片审查 69 个<br/>主切片 55 · 补审 14"] --> B["原始记录 239 条"]
  B --> C["跨切片合并去重"]
  C --> D["证伪剔除 15 条"]
  D --> E["定稿 167 条<br/>编号 P-001…P-167"]
  E --> F["九个分组文件"]
  F --> G["严重度标注<br/>高 11 · 中 61 · 低 95"]
  G --> H["全部完成代码级复核<br/>未复核 0 条"]
```

### 切片清单概要

主切片 55 个：

- 后端 27 个——装配与通用存储、启动与数据目录、安全与会话、方案流水线（文件组一、二）、计划页与强度调整、练习与考试（文件组一、二）、间隔复习、项目成果与量规、Wiki 引用问答、Agent 工具循环、材料导入解析与媒体、笔记与知识库检查、网页检索与抓取、供应商（文件组一、二、三）、WebDAV/S3 配置同步、全量备份、只读分享、数据运维、多格式导出、本机社区、声明式插件、搜索通知与首页、专注打卡与学习报告。
- 前端 16 个——方案生成向导、计划页与首页、学习空间、知识地图与复习、练习与考试、资料库与 Wiki、项目与报告页、Agent 中心与全局导师、社区与集成页、模型接入中心、设置（目录/备份/安全）、设置（同步/运维/导出/媒体）、只读分享页、应用骨架与页面守卫、全局状态与 IPC 客户端、样式系统与基础组件。
- 横切与旅程 12 个——状态机与恢复（学习域、支撑域）、事件与刷新契约全链路、主线程阻塞专项、错误呈现与死路、安全边界横切、数据生命周期、无供应商裸新手、完整学习闭环、恢复迁移与多用户、无障碍与键盘、页面布局复核（第二意见）。

补审 14 个——缺口补审批次 12 个（对象为 `desktop/node_modules/` 下第三方包说明文档）、文档·决策与实现一致 1 个、构建·打包与配置 1 个。

### 覆盖核算

| 切片类别 | 切片数 | 完整阅读文件数 | 负责文件数 |
| --- | --- | --- | --- |
| 后端 | 27 | 135 | 136 |
| 前端 | 16 | 43 | 43 |
| 横切与旅程 | 12 | 0 | 0 |
| 文档 | 1 | 42 | 137 |
| 构建 | 1 | 14 | 14 |
| 补审 | 12 | 94 | 0 |
| 合计 | 69 | 328 | 330 |

结论：切片声明的负责文件合计 330 个，其中 234 个在所属切片内完整逐行阅读，占 70.9%；未满读的 96 个为两处——「方案流水线（文件组一）」的 1 个负责文件，「文档·决策与实现一致」切片的 95 个第三方依赖 README（该切片仅对短文件全文阅读，长文件读开头，属上游模板文档，不含项目决策）。另有 12 个补审批次以「负责文件数 0」的口径通读 94 个第三方包说明文件。完整阅读合计 328 个。横切与旅程类切片按约定不单独计文件数，其阅读量体现在所属后端与前端切片的计数中。

### 失败切片

无失败切片：69 个切片的执行状态标记均为未失败。

多数切片为只读静态审查，未运行规定门禁的全量套件，只在切片内运行与本切片相关的过滤子集；未运行项统一记入「未覆盖项」。

### 切片阶段实跑的检查

以下命令在各切片阶段实际执行（对应切片的检查记录），本轮索引撰写未重跑：

| 命令 | 结果 |
| --- | --- |
| `cargo test --release --locked security::` | 19 passed |
| `cargo test --release --locked practice::` | 27 passed |
| `cargo test --release --locked providers::` | 45 passed |
| `cargo test --release --locked agent::` | 18 passed |
| `cargo test --release --locked materials::` | 14 passed |
| `cargo test --release --locked notes::` | 9 passed |
| `cargo test --release --locked maintenance::` | 9 passed |
| `cargo test --release --locked export::` | 9 passed |
| `cargo test --release --locked reports::` | 5 passed |
| `cargo test --release --locked knowledge::` | 4 passed |
| `cargo test --release --locked community::` | 4 passed |
| `cargo test --release --locked plugins::` | 4 passed |
| `cargo test --release --locked review::` | 2 passed |
| `cargo test --release --locked sync::` | 10 passed |
| `cargo test --release --locked backend::sync` | 10 passed |
| `cargo test --release --locked restart_after_logout_does_not_silently_relogin` | 1 passed |
| `cargo test --release --locked no_material_plan_runs_through_adoption_and_lecture_generation` | 1 passed |
| `cargo test --release --locked a_missing_default_directory_is_created_without_a_dialog` | 1 passed |
| `cargo fmt --all -- --check` | 通过 |
| `npx tsc --noEmit`、`./node_modules/.bin/tsc -b` | 退出码 0 |
| `npm audit --omit=dev && npm audit` | found 0 vulnerabilities |
| `npm run build` | 通过 |
| `bash scripts/dev-smoke.sh` | 冒烟通过 |
| `node scripts/check-versions.mjs` | 6 处版本一致（1.1.16） |

另有两个一次性探针：esbuild `transformSync` 验证 JSX 行内注释的渲染路径（两处注释成为字符串字面量子节点），KaTeX 渲染管线计时（600 个公式 1010.6 ms，KaTeX 占比 72%–83%）；探针脚本写在仓库 `TEMP/` 下并已删除。

## 已排除的疑似问题（证伪清单）

下列 15 条在切片阶段被提出，经复核未构成用户可见缺陷，不计入 167 条定稿数。编号沿用切片阶段编号。

| 原编号 | 标题 | 证伪理由 |
| --- | --- | --- |
| 后端-项目-3 | 删除带 running 评价的项目时版本行被级联删除但不终止对应模型请求，评价继续执行至完成（消耗供应商计费/配额）后结果被静默丢弃 | 前端没有删除项目的入口：`projects` 集合的三处写入（desktop/src/pages/Projects.tsx:120、130、140）只做里程碑勾选与追加，删除只存在于集合替换的 GC（desktop/src-tauri/src/backend/store.rs:939），用户无法触发该组合。 |
| 后端-运维-6 | migration-tmp-wal 崩溃残留不在任何运维扫描面，永久滞留且体检不可见 | 迁移每次执行都清扫 staged 主文件与其 `-wal` 两种残留（desktop/src-tauri/src/backend/store.rs:328-334），残留不会永久滞留。 |
| 后端-导出-1 | APKG 构建临时数据库写入应用数据根内的 exports 目录，目录与崩溃残留无任何清理 | 临时库落在工作区 `exports` 目录，运维扫描按 `anki-*.db` 且超宽限期纳入清理候选（desktop/src-tauri/src/backend/maintenance/mod.rs:463-479，测试见 maintenance/tests.rs:106、139、184、196），进程内残留由 `TempDatabase` 的 Drop 删除（desktop/src-tauri/src/backend/export/anki.rs:16-22）。 |
| 后端-社区-2 | 房间行与侧栏下一次学习的标题/主题缺少 overflow-wrap 规则，长无空格串（URL、base64、错误日志）横向溢出容器 | `overflow-wrap` 为继承属性，body 级 `body { overflow-wrap: anywhere }`（desktop/src/styles.css:2040）覆盖全部后代，长无空格串在内容盒内折行。 |
| 前端-方案生成器-3 | 资料来源 URL 无折行约束，长 URL 溢出暂存列表并压到状态徽标/删除按钮 | 资料行文本容器为 `min-width:0` 的 grid（desktop/src/styles.css:1128），URL 所在 `small` 继承 body 级 `overflow-wrap:anywhere`（styles.css:2040）折行，状态徽标另有 `max-width:120px` 与省略号（styles.css:1132），不压到删除按钮。 |
| 前端-练习套件-5 | deferred（统一反馈）进行期主观题评分完成后显示 hints 语义的错误文案 | deferred 模式下后端按设计在交卷前置空 `score`/`correct`/`feedback`，「评分已保存，按当前设置仅显示提示」如实描述该状态（desktop/src/components/SubjectivePractice.tsx:161），统一显示口径由同页「答案已保存」分支给出，属措辞而非语义错误。 |
| 前端-练习套件-6 | 错题本标题等容器的长动态文本缺 overflow-wrap 约束（147 系列修复清单遗漏） | body 级 `overflow-wrap:anywhere`（desktop/src/styles.css:2040）为继承式全局兜底，147 系列之外无需逐容器补规则。 |
| 前端-资料库与Wiki-3 | 网页资料的 meta 行含完整 URL，但该 span 无 overflow-wrap 约束，长 URL 连续串撑破资源行 | 同上：body 级声明继承覆盖，长 URL 在内容盒内折行。 |
| 前端-资料库与Wiki-5 | 问题标题、历史标题与资料详情标题容器缺少 overflow-wrap，长无空格串（URL/哈希/base64）溢出 | 同上；另有容器以 `overflow:hidden` + 省略号截断（如 styles.css:1475 的 `.wiki-docs > label strong`），不产生横向溢出。 |
| 前端-项目与报告-6 | 里程碑标题、反馈总结/建议、方案目标、打卡笔记等容器缺 overflow-wrap 约束，长无空格串撑破布局 | body 级声明继承覆盖；页面主容器另有 `min-width:0`，摘要段为 `overflow:hidden` + 行数截断（desktop/src/styles.css:829、836、838）。 |
| 前端-Agent界面-4 | 批准产物提升后任务中心不刷新：agent.artifact_promoted 不匹配刷新过滤且 done 讲义缓存不失效 | 任务中心刷新过滤为「前缀 `plan.`/`agent.`/`lecture.` + 后缀 started/completed/failed/cancelled/interrupted/degraded」（desktop/src/components/AgentTaskCenter.tsx:60），产物提升所在的工具调用完成后发出 `agent.tool_completed`，运行终态另有 `agent.run_completed`，均在过滤集合内；done 讲义缓存按 `planId:stageId` 缓存讲义版本，产物提升不产生讲义版本，缓存无需失效。 |
| 前端-设置核心-4 | 导入预演弹窗的备份文件名无折行约束，长文件名横向溢出 560px 弹窗 | body 级 `overflow-wrap:anywhere`（desktop/src/styles.css:2040）继承覆盖弹窗内文本。 |
| 前端-设置核心-5 | 用户名/令牌名长无空格串缺少 min-width:0 与 overflow-wrap，窄窗口下文字溢出行卡片压到按钮 | body 级声明继承覆盖长串折行；徽标与按钮另有 `max-width:100%` 与 `min-width:0`（desktop/src/styles.css:2046-2047）。 |
| 横切-页面布局复核-3 | 窄窗（<699px）下第一步标签「辅助资料（可选）」nowrap 溢出 126px 固定宽按钮，与相邻步骤文字重叠 | 窄窗下步进条改为 `display:flex` + `overflow-x:auto` 的横向滚动条、按钮 `flex:0 0 126px`（desktop/src/styles.css:1709-1710）；按 12px 字号与 9 个全角字符计算，标签溢出量约 36px，落在相邻按钮左内边距内，未覆盖其序号圆点与文字；原结论为按字号与固定宽度的像素推算，未经真实渲染验证。 |
| 补审-12-3 | 单元卡在 running 且所属讲义已是终态时，恢复生成会复用同一个 request_id，命中缓存的失败请求，形成无法自愈的无限失败循环 | 单元与讲义行同事务推进，失败与取消都以 `status='running'` 守卫收口（desktop/src-tauri/src/backend/learning/lecture.rs:529、554），重启恢复对 running/cancelling 讲义同时改写单元为 interrupted/cancelled（lecture.rs:879-885）；中断单元仅在上一请求确实完成时才复用 request_id，否则 attempt+1 生成新 request_id（lecture.rs:485-497），不存在反复命中同一失败缓存的循环。 |

## 未覆盖项

1. 未启动应用与真实窗口：布局、溢出、对齐问题基于 CSS/JSX 结构静态推断，未做真实渲染验证（项目规则禁止前台 UI 自动化、模拟键鼠与截屏）。
2. 未使用真实模型供应商、真实 WebDAV/S3 服务与真实媒体编解码播放；相关结论以代码与本机回环协议测试为据。
3. 未运行 cargo 测试、npm 构建等重型命令（只读审查，避免与门禁/CI 重复）；构建与测试状态以 make check / CI 为准。
4. 性能未测量（启动耗时、SQL 查询计划、内存、安装包体积）；性能类发现均为静态推断。
5. REF/ 为用户提供的参考源、非交付物，未审查；dist/、node_modules/、src-tauri/target/、src-tauri/gen/、src-tauri/licenses/、TEMP/ 为可重建产物，未审查；.video_agent/ 为未跟踪目录，未审查；skill 资产的 README/templates 按 NOTICE 声明为存档不进产物，未逐字审查（SKILL.md 已审）。

说明：第 3 条指全量套件未在审查切片内运行；切片阶段实际运行的过滤子集与轻量门禁命令见「切片阶段实跑的检查」。
