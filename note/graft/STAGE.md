---
owner: @esengine
backup: @SivanCola
status: active
reviewed: 2026-10-15
---

# STAGE.md — Deep Tutor → Reasonix 嫁接进度跟踪

> 本文件已纳入 git 跟踪，只保留当前状态；历史细节（S0–S8 阶段日志、八轮性能优化、
> 桌面导师修复、决策日志）见首笔快照：`git log --follow -- note/graft/STAGE.md`。
> note/ 其余内容（00-design.md 设计文档、report/ 报告、probe/ 探针）仍不跟踪，只在本地。

## 当前状态（2026-10-15）

**嫁接已完成并提交**：S1–S8 六层提交 efb0a1e…f4f3ccc（studio 分支，未推送），23 项 Feature
Checklist 全 ✅，Deep Tutor 语义逐条对位移植。架构与映射决策见 note/graft/00-design.md，
用户侧文档 docs/TUTOR.md。

**后续工作也已入库**（2026-10-15，细粒度八笔）：

- docs 7a9565d34（导师文档对现状刷新）+ STAGE 跟踪两笔 6a15570c9/9edafb614
- chore(studio) 68be5cf55：感知层注释去 Deep-tutor 措辞
- feat(studio) 36ff47c32：#tutor 深链 + TUI /tutor
- feat(tutor) 62eecb8cc：web 检索换 Reasonix 统一方案 + 拨号时 SSRF 审查
- perf(tutor) 6d3fc9da0：八轮优化 + benchmark/（SearchAll -87% / SessionsList -80% /
  Epub -61% / PDF -48%，报告在 note/report/perf/README.md）
- fix(studio) ec29a8635：桌面导师双层 bug——TutorSheet 端口接错（machinePort 的
  /host/settings 前缀使全部 /tutor/* 404，hub.tutorPort() 无基座端口修复）与空库存
  null 列表致 React 白屏（serve 层 tutorList + 前端 list() 容错）

**实机验证轮（2026-10-15 晚，安装版 + CDP 9448，四笔细粒度）**：

- fix(tutor) 14ad818e2：空库 kb/export 500 → 哨兵 ErrKbExportEmpty + 400
  tutor.kb_export_empty + 测试 + TUTOR.md 行（实测发现）
- docs 397b4e44f：TUTOR.md 备份格式勘误（单 JSON 文件，非 zip）
- fix(tutor) 4e85a69ad：错题本/错题队列条目补 answer 字段（重练答错时 UI 能显示
  正确答案；实测发现）
- fix(studio) c07148de4：复习空态补"再取一次最新数据"刷新按钮（挂载期间错题在
  别处产生时不再死胡同；实测发现）

验证覆盖（探针 note/probe/cdp-*.mjs，全部走真实 UI/页面源）：

- 免费路径 CRUD：笔记 / KB / 搜索 / 备份
- 面板压力：开关×10、分区扫×2、边缘搜索×5，零控制台异常
- 真实生成管线：表单→SSE 八工具→done→方案落库 4 阶段/14 主题/6 天 20 任务/
  校验全过→详情自动打开→首页与方案页可见
- 学习闭环：测验 10 题生成+UI 判分；故意错题判 false 含解析；错题重练判对→
  错题本清空；节点掌握度+间隔重复 k1→10-18 / k2→10-29；打卡 25 分钟+统计/连胜/日志
- 用量：模型 opencode（订阅型费率卡 0 价）token 34,508 已计量、cost=0 故台账不记
  ——机制有单测覆盖，非缺陷
- 重打包复验（build:host → electron-builder → 手工签名核验 → zip 重建 → robocopy
  部署）：kb/export 400、wrong-queue 带 answer、空态刷新按钮全过，存储跨重装保留

**打包**：dist/ 内有已签名的 windows-amd64 安装包 / 便携 zip / CLI 三目标；
源码分享包 dist/R-Tutor-src-studio-20261015.zip。

**产品翻转（2026-10-15 深夜，用户目标：前端以 Deep Tutor 功能为准）**：

- SPA（Studio 与 `reasonix web` 同源）从 Agent 优先翻转为学习工具优先：窗口默认
  落在导师面，Tutor 升格为根 surface，不再是浮层 sheet
- Agent 的完整界面收缩为导师页头的「聊天」页（App `surface: "tutor" | "chat"`）；
  tutor 面隐藏 Nav/Sidebar/RailToggle（精简），风格全沿用 Reasonix tokens
- `#tutor`/`#chat` 深链二选一，重载默认回导师面；设置浮层在 tutor 面经
  usePaneForSheets 照常取端口
- 顺手清掉存量欠账：c07148de4 的 `tutor.review-refresh` 未注册进 actions.ts
  （HEAD 上 actions.test.ts 即红）
- 验证：tsc 0、vitest 548 文件/4667 用例全绿、build、census:verify 金样不变

**前端全覆盖对账轮（2026-10-15 深夜续，route↔port↔UI 三张对账表 + 实机复验）**：

- feat(studio) 7c8fea744：对账后补齐的 UI——资料暂存行（文件行逐份解析模式
  full/fast/none、URL 行随目标发送、可移除）、首页学习数据卡（十二周热力图 +
  分方案进度米条）、复习总览（今日到期/错题总数/按方案错题直达该方案测验页）、
  测验统计与错题重练判分；actions.ts 补 mat-*/wrong-* 注册，EN 全量
- fix(studio) c86775b2f：内核 tutor DELETE 全 204 无体，端口层 del0 的 res.json()
  空体解析抛错——服务端已删、UI 报错行不消（实测发现；空体 settle undefined +
  2 单测，插件卸载的 JSON 形态保留）
- fix(studio) f201c9154：Tutor.css 幻影 token——--line/--ink/--ink-dim/
  --accent-soft/--bad/--good 无一定义，边框落 currentColor、热力图空格透明、
  原生输入框 UA 白底成"深色页面上的洞"（实测截图发现）→ 真词汇（--border/
  --text/--muted/--accent-wash + 文字用 *-ink）+ 字段配方（--raised/--text/
  --focus，:where 保页面覆盖力）
- 验证方法：mock OpenAI SSE（8199）+ config.toml 种子 provider + REASONIX_HOME
  隔离 + headless Edge CDP 17 检查零控制台异常 + 截图视觉复核（探针
  note/probe/cdp-ui-tour.mjs；目标页先 about:blank 再导航，防旧包缓存竞速）
- 发现未改（设计如此）：`reasonix web` 首跑向导是死胡同——Onboarding 走 grants
  门控的 /providers/*，仅桌面 host 调 AllowProviderEdit；web 循环回环只开
  /provider-setup 而 Onboarding 不调。安全取舍不动；web 首跑 seed config.toml
- IAB（ZCode 内置浏览器）宿主 rAF 不触发、Web Animations 冻结于 0，intro 的
  arrive() 永不完成，本应用验证不可用；headless Edge CDP 替代
- 闸门：tsc 0 / vitest 548 文件 4673 用例 / build / census 金样不变 / make
  check+lint clean（repolint 2697 零加宽）

**文档对齐轮（2026-10-15 深夜三，/goal「按实际代码更新全部文档」）**：

- 以翻转后产品形态为纲复核全部 docs/ 43 份 + 根 README±zh 等；改动七份、余下复核无需改
- README±zh 定位翻转：自学工具为主、Agent 为聊天页；30 秒上手改导师流程
- STUDIO_PARITY 导师行改"窗口首页"（原"侧栏按钮浮层"已不实）；CLI±zh /tutor 行同口径
  （TUI 无导师界面，命令只打印位置）
- GUIDE±zh 加窗口导向段 + web 首跑向导死胡同说明（provider.editing_disabled，seed 绕过）
- 顺手 GUIDE/CLI 四份补 owner 头（doc-owner 基线 -4，-update 收紧 2697→2693）
- 复核无需改：TUTOR/SPEC/CONFIG_PATHS/CONTRIBUTING/ROADMAP/MIGRATING/
  STUDIO_RELEASE/STUDIO_SHELL_BOUNDARIES/desktop±/REASONIX/AGENTS/CLAUDE/
  SECURITY/USAGE_CATALOG/BILLING
- 闸门 make check+lint 绿

**性能优化第 9 轮（2026-10-16 凌晨，/goal 持续性能优化，新面：前端首载）**：

- 前八轮全在 Go 侧（见 note/report/perf/README.md）；本轮换到 SPA 首屏
- feat：聊天面树原样搬 ChatPage.tsx（props 解构、JSX 逐字不变、hook 留 App），
  App 以 Settings 同款「随壳预取 + lazy」挂载——导师首屏不解析聊天面代码
- 量化（benchmark/frontend_bundle_bench.mjs，确定性）：eager JS 1309.6→883.4KB
  （-32.6%，gzip -31.1%）、index 单块 781→355KB（-54.6%）、聊天面 406KB 转预取懒块
- 验证：tsc 0 / vitest 548×4673 全绿（1 例同步查询改等懒块）/ census 金样不变 /
  CDP 实机 17 检查全绿（聊天翻转、#tutor 回返、零控制台异常）
- 坑位：`reasonix web` 的 -addr 而非 -addr/-port 混写；remote/ 令牌文件取最新要按
  mtime（ls -t），字典序会拿到旧 pid 的令牌 → assets 401 → 卡「正在连接内核…」

**性能优化第 10 轮（2026-10-16 凌晨，/goal 持续性能优化，面：i18n 按语言拆分）**：

- zh 恒等（中文即 key），只有 en 需要目录；语言会话期固定（切换=adopt 整窗
  reload）→ 目录可首帧前并行拉取，zh 窗口根本不拉
- perf：i18n/index.ts 去顶层 import EN，load(fixed) 仅 en 动态 import（目录记忆
  化，重复 boot 同步还原）；main.tsx loadLang(bootLang()) 与 pick() 一起
  Promise.all 后首渲染。eager JS 883.4→570.5KB（对基线累计 -56.4%，gzip -58.9%），
  en 目录 313KB 转懒块
- 测试侧与生产同款：显式 en 站点 await load(boot())；testsetup 每文件 preload
  目录进记忆（不动当前字典，未 boot 文件仍恒等 zh）——jsdom navigator 为英文的
  隐式 en 套件（modelpicker/effort_catalog）无需各自加载
- 验证：tsc 0 / vitest 548×4673 全绿 / CDP 实机：zh 默认 0 次 en-*.js 请求；
  内核 config language=en 时 adopt 落英文、目录恰拉一次；en 下 #chat 懒载正常
- 坑位：CDP 里手改 localStorage rx-lang 会被 adopt() 按内核 config 弹回——验证
  EN 要改 REASONIX_HOME 的 config.toml [desktop] language 并重启 web；#chat 哈希
  仅挂载时消费，翻转要带哈希整页导航而非 hashchange

**性能优化第 11 轮（2026-10-16 凌晨，/goal 持续性能优化，面：少打开的面出壳）**：

- 第 10 轮后 index 里仍坐着四块少打开的整面：反馈两 body、手机配对弹层、
  首跑引导 ×2
- perf：薄框架留壳（Feedback 对话框框架/Chrome 标题栏），重 body 走 Settings
  同款「随壳预取 + lazy」；首跑引导的回退就是闸门已画的 data-run="idle" 空帧。
  eager JS 570.5→488.3KB（对基线累计 -62.7%，gzip -64.8%）、index 353.8→273.9KB
- 验证：tsc 0 / vitest 548×4673 全绿（零用例改动）/ CDP 复走 zh+chat 全绿
- 有意不做：prefsnav（目录表，壳内多处读）；CSS 按面拆分（闸门读 app.css ?raw）

**统一前端（2026-10-16，/goal 聊天面与学习面完全隔离 → 一个窗口，用户三答定形）**：

- 形态：聊天壳收编学习（Nav 轨 + 会话栏 + 面板条常驻）；默认落学习；
  侧栏「学习方案」行点击直达该方案视图（走 tutorPort 基座口）
- 实现：ChatPage.tsx 删除；新 Cols.tsx 常驻壳；学习面 = `.pane-tutor`
  钉住第一签（TUTOR_PANE，无关闭/改名/右键，「关其他」不含它）；聊天
  Pane 仍随壳预取 + lazy；#chat → 恢复上次激活面板，其余默认学习；
  reloadPanes 兜底：TUTOR 永有效、"" → 首面板、失效 → 首面板 ?? TUTOR
- 竞态修复：Nav（eager）的 files 脉冲可先于懒 Pane 挂载到达 → useOnBump
  加 replay 形参（挂载前到达的计数=最早请求），App 在 takeover 自增时清零
  该面板 filesRequests（重绑不重放）
- 坑：.pane 选择器撞学习面（测试的 textarea 查询误中）→ 学习面独立类；
  测试间 sessionStorage rx-active-pane 串味 → testsetup 逐用例清
- 坑：ZCode 内置浏览器 goto 的 fragment 后置补写，#chat 冷启动后地址栏
  会残留（应用侧一次性消费/清除经 location.reload 路径验证正确，非缺陷）
- 红线：浏览器测试一律 ZCode 内置浏览器，勿开外部 Edge（用户两次明示）
- 代价：eager JS 488.3→575.6KB（+17.9%，gzip 152.8→178.2），对原始基线
  仍 -56.0%；见 note/report/perf/README.md 后记
- 验证：tsc 0 / vitest 548×4673 全绿 / build+census / repolint 2693 零加宽
  （学习方案 CSS 放 Tutor.css，studio.css 撑回闸内）/ make lint 0 issues /
  内置浏览器实机八项（默认学习、方案行直达、钉签切换、聊天按钮、导轨
  学习导师、#chat/#tutor/裸地址、零错误横幅）

**统一二期（2026-10-16，/goal「界面还是不统一」四答：双导航/风格/标签条默认/布局）**：

- 学习面内导航（学习目标/我的方案/复习/笔记/知识库/用量）收进侧栏
  「学习」分区（TUTOR_NAV 导出，Sidebar 渲染，aria-current 随
  tutorView，点击=focusPane+setView），置于「会话」块之前
- 学习面头部换聊天面同款 .chrome/.crumb/.thbtn 真词汇：同 class 同 CSS
  即同观感，桌面端顺带获得标题栏拖拽区；tut-nav/tut-hd 样式删除
- 面板标签条默认显示：prefs 判定 `!== "off"`（旧用户显式关不掉的保留
  off），外观开关语义不变；session-tabs 套件改写为默认开
- 代价：eager 576.8KB（+1.2，头部/导航换位）；vitest 548×4673 两轮全绿；
  repolint 2693 零加宽；census 零新动作（tutor.nav 复用）
- IAB 实机（内置浏览器，红线）：默认学习面+chrome 头；侧栏三段（学习/
  学习方案/会话）；标签条默认两条（删键重载证默认）；复习入口三联动
  （聚焦/切页/面包屑+aria-current）；聊天 thbtn 切面学习面保持挂载；零
  errbar。IAB 三坑入记忆（见 browser 记忆文件）
- 安装版同步重打包部署（electron-builder 假签名坑：手动 vendor signtool
  /a /tr 补签后 robocopy /E 到 F:\Program Files\Reasonix Studio）

**前端穷举审计（2026-10-16，/goal 穷举所有交互、坏逻辑立刻修；内置浏览器红线）**：

- 前端四处：QuizPanel 判分回写读陈旧 verdicts 且把已选未判计入（改为
  next-map 显式传入 + 主测验判后锁选项）；Review 错题模式 quizOffset 钳 0
  重复首题（mode 感知 quizAt）；Kb「用所选生成方案」只跳视图不起引擎
  （内核只播种，现补 engine.startGenerate 带文档名目标）；Home 步骤序号
  0 基与热力图未来日 title「-1 分钟」
- PlanDetail 白屏：降级方案 knowledge_map.nodes/schedule/topics 是 Go nil
  切片 → JSON null，页面 .length 崩整树（?? [] 兜底 + 回归测试）
- 内核 clarify 死循环：execUnderstandIntent 只传 TimeFields，Answers 不随
  行 → 已答轮 ValidateIntent 仍 needs_clarify 重问（args.Answers 复制；
  pipeline_test 改生产形状先红后绿）
- 零运行时学习面 503：导师路由只挂运行时 mux，routeDefault 无运行时即
  拒，关最后一个聊天面板学习面全灭。tutorShared 本就是进程级共享 →
  hub 持 tutorSrv 空壳 Server 把导师路由挂 hub mux（hub_test 回归；实机
  关 r1 后 /tutor/* 全 200、/status 仍 503、整页刷新学习面完整）
- 既有闸门破损补齐：14ad818e2 加的 tutor.kb_export_empty 一直缺双语文案
  （serve parity 测试红）→ kernel.ts + en_kernel.ts 补
- IAB 实机：两轮完整生成（表单发起 + 知识库所选发起）经 clarify 强推
  落库；方案六标签页 + 降级渲染；测验生成/参考答案；学习中心打卡回写
  实涨；笔记增改查；搜索三分类命中与跳转；用量；壳层（聊天按钮、钉签
  切换/关闭、#chat/#tutor 入口一次性消费、侧栏 aria-current、零运行时
  重载）；重新生成出 v2；全程零 errbar
- IAB 工作法（rAF 停转连带）：开机动画 canvas 遮罩 evaluate 移除；
  Playwright click 的稳定性检查必超时 → locator.evaluate(el.click)；
  受控输入用原生 setter 派 input 事件
- 闸门：tsc 0 / vitest 548×4677 / build+census 一致 / tutor -race 23.9s /
  serve 全量仅上述 parity 一红（已修）/ make lint 0 / make check 2693
  零加宽（三段超 essay 预算注释收紧至 3 行）
- 记录为非缺陷：用量「0 次生成」＝mock 零成本不计账（service.go 仅
  totalCost>0 记账的成本账本设计）；加载后改 hash 不响应＝入口一次性
  消费设计；GET /tutor/progress 非路由，routeDefault 对未知路径 503 为
  既有行为；release_unit_test.go 在 HEAD 即 gofmt 不齐（非本轮引入）

**弹窗根除与揭幕看门狗（2026-10-16 深夜，/goal 去弹窗+排查一直加载；内置浏览器红线）**：

- 「一直加载不出来」两根因：① 三处 window.confirm（KB 删文档/笔记删除/
  取消生成）在内置浏览器里原生对话框无法显示，JS 线程阻塞在自己调用上
  ——上轮两次「页面冻结」即此（KB 删除、误点笔记删除）；② boot 编舞全在
  首帧 rAF 内启动，rAF 不来 intro.held 永不 resolve，arrive() 对 held 又
  无兜底且再设一道裸 rAF 闸，加载屏永盖页面
- 修一（弹窗→覆盖层）：useConfirm 页内覆盖层（body portal + Escape +
  焦点落在拒绝钮 + 卸载补答 false），三调用点换装，回归测试三条（取消
  保行/确认才删/生成取消）；文案全复用既有双语键，零新目录条目
- 修二（boot 看门狗）：intro 首帧 700ms 看门狗走既有 skip 路径落定字标；
  arrive() 帧等待与 400ms 兜底竞速；calm 分支同样受看门狗保护
- 笔记关闭修复入库：close 不再经 open(null) 变成空白新建表单（3e6a8b631）
- 实机（覆盖层与揭幕）：boot 自动揭幕（无需手工移除 #boot）；KB 删文档
  覆盖层两路 + 服务端 documents:[]；笔记删除两路 + 服务端 []；生成取消
  费用提示覆盖层
- 实机（数据流）：添加网页成功（example.com 抓取解析 174 字）与内网地址
  拒绝（SSRF 防护正确）；导出 ZIP 200 且 zip 含 index.md；导出 Markdown；
  只读分享建/链接页渲染/撤销后 not-found 页
- 事故红线补录：浏览器级 tab.reload() 在内置浏览器宿主内越界曾直接杀死
  ZCode 进程——此后仅页面级 playwright 动作，禁 tab.* 控制类 API（记忆
  已更新）
- 记录为非缺陷：分享页无 cookie 401＝token 模式 host binding 设计
  （tutor_share.go 注释明示），公开性靠无鉴权部署 + 192 位令牌不可猜
- 闸门：tsc 0 / vitest 548 全绿（tutor 套 20/20）/ build+census 一致 /
  make lint 0（顺带把上轮 STAGE 超宽 IAB 条目拆行）/ make check 2693
  零加宽；提交 3e6a8b631 + 覆盖层 + 2b122ec85 + 本记录

## 闸门口径（复核用）

make lint（golangci 0 issues + repolint clean 2693 零加宽）；make check；tutor 全包
（-race 约 23s）；serve 全量（约 640s）；boot 用 -timeout 25m（本机 AV 慢，默认 10m 会假
警铃）；前端 tsc + vitest（548 文件）。大套件勿并发跑。

**本机已知假警铃（2026-10-15 夜实测，基线 f02046adb 无本地改动同样复现）**：

- ① t.TempDir() 清理竞态——unlinkat 报 "directory is not empty"，AV/索引器对临时
  目录持有 delete-pending 句柄所致。测试本体 0.02–0.03s 全过、随机分布在不同测试
  （SharedPlanView*/SlidesSpec/ShareOrphan 等），格率约 3/10，重跑即绿，与代码无关
- ② 高负载下 TestServiceGenerateAttachAndTerminalReplay 偶发 "stream stalled"
  （10s 时序门），独占重跑即绿。判定失败先看失败行是不是 TempDir cleanup/时序门
