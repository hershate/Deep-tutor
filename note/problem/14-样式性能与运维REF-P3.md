# P3 样式性能、插件运维与 REF 盘点问题（26 项）

第五轮对 styles.css 全量、前端性能/资产、plugins/maintenance/notes 内部、
REF/ 参考源的审计发现。验证通过未列入：styles 选择器同属性覆盖冲突（零）、
!important 滥用（仅 5 处合理）、媒体查询实质矛盾（零）、z-index 其余嵌套组合、
favicon 链路、插件 dispatch 幂等/set_enabled 代数语义/清单原子写/8 个 skill 资产、
外键检查消费、remove 路径防护、WAL 字节数、体检四类检查口径/关联清理闭环、
Integrations/Library 字段消费、REF 无敏感文件不入 git。

## styles.css 与视觉

### 1. Agent 发送按钮深色下近白字配亮底（对比约 1.7:1），图标几乎不可见
- `styles.css:1188`——`.agent-composer > div > button` 未列入深色覆盖清单（:1025 只覆盖导师面板发送键）；深色 accent-strong=亮黄绿配 paper-50≈白。
- 传递链条：触发（用户切换深色主题后查看/使用 Agent 会话发送按钮）→ 根因（`styles.css:1188` 该按钮固定 color:var(--paper-50)，未被 `styles.css:1025` 的深色覆盖清单收录）→ 传导（深色下 --accent-strong=oklch(0.87 0.12 130) 亮黄绿（styles.css:56）作底，paper-50≈白（oklch 0.985，styles.css:10）浮其上）→ 症状（发送键图标对比约 1.7:1 几乎不可见，仅键盘聚焦描边可辨）。
- 影响范围：功能面（Agent 助手页会话发送按钮）；触发条件（需深色主题，明色主题不命中）；受损面（可用性/可读性：核心发送操作视觉不可辨）。

### 2. 主按钮深色悬停变深绿底配近黑字（约 2:1）且色相突变
- `styles.css:145-146`——`.btn-primary:hover` 不分主题用 pine-700；深色文字 pine-950——全站保存/生成按钮悬停瞬间难读。
- 传递链条：触发（深色主题下悬停任一主按钮）→ 根因（`styles.css:146` hover 无条件换 pine-700 底色（oklch 0.45，:6），而 `styles.css:145` 深色文字仍 pine-950（oklch 0.27，:4））→ 传导（底与字亮度差极小且从亮黄绿底突变深绿底）→ 症状（悬停瞬间对比约 2:1 文字难读，色相跳变刺眼）。
- 影响范围：功能面（全站 .btn-primary：保存/生成/确认等按钮与各模态）；触发条件（深色主题+悬停即命中，默认明色不触发）；受损面（可读性：确认操作的悬停反馈瞬间失效）。

### 3. 大字模式下阅读区反而小于周边正文
- `styles.css:1022-1023`——body 18px vs `.reading-article` 钉在 1.08rem（17.28px）——学习空间核心阅读面在大字模式下比其余文字更小（09#6 的新增数据点）。
- 传递链条：触发（设置开启大字模式，`App.tsx:136` 给 html 加 large-text 类）→ 根因（`styles.css:1023` `.large-text .reading-article` 钉 1.08rem，rem 只随根字号 16px 计算，不继承 `styles.css:1022` 的 body 18px）→ 传导（阅读区正文 17.28px，周边 UI 文字随 body 到 18px）→ 症状（核心阅读面反而比周边正文小 0.72px，放大诉求被反向削弱）。
- 影响范围：功能面（学习空间阅读区讲义正文）；触发条件（需开启大字模式，默认关闭不命中）；受损面（可读性：弱视用户的字号补偿在最需要处失效）。

### 4. 专注模式内 Ctrl+K 打开被完全遮挡的搜索模态，键盘焦点被抢进隐藏输入框
- `App.tsx:141-142` 无 focusOpen 守卫；`.overlay` z70 < `.focus-overlay` z90（不透明 pine-950）；模态 autoFocus+Modal 20ms 聚焦——用户盲打且每次击键触发全量 LIKE 搜索；Esc 可恢复。
- 复核修正：搜索输入有 120ms 防抖（`App.tsx:97`，query 非空时），连续击键会合并为停顿后的一次 LIKE 搜索，非严格"每次击键"一次；焦点被抢与盲打仍成立。
- 传递链条：触发（专注模式运行中按 Ctrl+K）→ 根因（`App.tsx:142` 键处理不检查 focusOpen 直接 setModal('search')）→ 传导（模态 `.overlay` z70（styles.css:190）被不透明 `.focus-overlay` z90（styles.css:978）完全盖住；输入框 autoFocus（App.tsx:287）+ Modal 20ms 聚焦（UI.tsx:50）把焦点抢进隐藏输入框）→ 症状（用户盲打无视觉反馈，输入内容进搜索框并触发搜索；Esc（App.tsx:143）可退出专注恢复）。
- 影响范围：功能面（专注模式期间的全局搜索模态与键盘焦点）；触发条件（需进入专注模式后按 Ctrl+K，默认使用不命中）；受损面（可用性：键盘焦点被劫持进不可见输入）。

### 5. 32 个 class + 1 个 keyframes 确认零引用（约 130 行死样式）
- 全量 599 class 逐一比对：typing/formula-block/transcript/video-*/evidence-list/practice-strategy/data-status/example-plugins/extension-card/pricing-*/provider-layout 等（styles.css:356-1318 各处）——ProviderHub 重写与讲义改 KaTeX 后的遗留；另 `.event-测评` 的"测评"枚举不可达（store.rs:827 仅四种 kind）。
- 传递链条：触发（任一页面加载 styles.css，恒定发生无需用户操作）→ 根因（上述类在 desktop/src 全部 TSX/TS 中零引用（本次复核抽查其中 11 类均 0 引用）；`.event-测评` 因 `store.rs:827-828` kind 白名单仅 学习/练习/复习/项目 而 Plan.tsx:153 的 `event-${kind}` 永不生成）→ 传导（约 130 行死规则随全站样式表（约 1900 行）解析分发）→ 症状（无视觉异常，浏览器白白解析死规则，维护者易误读为活样式）。
- 影响范围：功能面（styles.css 全站加载，无具体页面症状）；触发条件（默认使用即 100% 携带，但无运行时可见后果）；受损面（性能极轻+可维护性：选择器噪音与误导）。

### 6. 反向缺口：4 组运行时使用但零 CSS 规则的类，呈浏览器默认控件样式
- `ProviderRouting.tsx:73`（route-log-entry/route-attempt*）、`StudyRoom.tsx:206-207`（lecture-checkpoint/lecture-citations）、`MediaPlayer.tsx:47`（native-media-preview）——路由日志/原文引用按钮为无样式系统灰按钮。
- 传递链条：触发（用户打开模型供应商的路由日志、讲义检查点/原文引用、媒体预览）→ 根因（`ProviderRouting.tsx:73` 的 route-log-entry/route-attempt-list/route-attempt、`StudyRoom.tsx:206-207` 的 lecture-checkpoint/lecture-citations、`MediaPlayer.tsx:47` 的 native-media-preview 在 styles.css 中 0 处规则（复核确认））→ 传导（浏览器回落默认控件样式）→ 症状（路由日志条目与引用按钮呈系统灰按钮，与整体视觉明显断裂）。
- 影响范围：功能面（ProviderHub 路由日志、学习空间讲义检查点与引用列表、资料媒体预览块）；触发条件（默认使用即命中，功能可用仅外观缺失）；受损面（可用性/视觉一致性：呈未完成的原生默认外观）。

### 7. `.task-row.is-done { opacity: 1 }` 为无效死规则
- `styles.css:235`——全文件无任何规则调暗未完成行，疑似"完成项淡化"旧设计残留。
- 传递链条：触发（用户勾选完成今日任务，Dashboard.tsx:49 加 is-done 类）→ 根因（`styles.css:235` 把 is-done 行 opacity 置 1，但全文件无任何未完成行降透明度的规则可覆盖）→ 传导（该声明恒为无操作，完成态仅靠 :236-238 的划线/灰字/勾选样式表达）→ 症状（零视觉差异，纯死代码）。
- 影响范围：功能面（今日任务列表样式）；触发条件（默认使用即命中但无任何可见后果）；受损面（可维护性：一条暗示"存在调暗设计"的误导性死规则）。

## 前端性能

### 8. 今日任务列表无上限全量渲染：tasks/resources/projects 无配额（上限 10000），最坏 10,000 行 ≈ 8 万 DOM 节点
- `Dashboard.tsx:49` 全量 map 无虚拟化；行高 72px 列表高 72 万 px；每次勾选全量数组提交（叠加 05#8 冲突语义）；`Plan.tsx:153` 周历同构。
- 传递链条：触发（tasks 集合累积至千条乃至上限 10,000 条（`store.rs:781` 配额）后打开今日页）→ 根因（`Dashboard.tsx:49` tasks.map 一次性渲染全部行，无虚拟化/分页）→ 传导（每行 min-height 72px（styles.css:227），1 万行≈8 万 DOM 节点、列表高 72 万 px；每次勾选 `Dashboard.tsx:40` 整表 map 后全量数组 replaceCollection 提交；`Plan.tsx:153` 周历 day.events 同构全渲染）→ 症状（首帧与每次勾选长卡顿，滚动严重掉帧）。
- 影响范围：功能面（今日页任务列表、学习计划周历与待排期列表）；触发条件（默认使用百条内无感，任务量达千级以上明显恶化，上限 10,000）；受损面（性能：渲染与提交均 O(全量)，大集合时交互迟滞）。

### 9. 每个后端事件全量重拉全集合快照并全树重渲染（渲染层放大，区别于已知请求层问题）
- `useBackendState.ts:38/51-57`——每事件 getAppState（全集合）→ setSnapshot 恒新对象 → App 全树 reconcile；讲义逐单元/agent 逐步期间每事件一次全量序列化+diff。
- 传递链条：触发（任一后端事件到达：讲义逐单元保存、agent 逐步输出、复习到期间隔轮询等多事件源）→ 根因（`useBackendState.ts:51-57` 每次事件后拉取 eventsSince 再 getAppState 全集合快照，`:38` setSnapshot 收到的恒为新对象）→ 传导（App 状态更新触发全树 reconcile，renderPage 各页面（App.tsx:232-251）均无 memo；agent 一次多步运行即数十次"全量序列化+全树 diff"）→ 症状（事件密集期整页反复重渲染，点击与输入出现可感延迟）。
- 影响范围：功能面（App 全部页面与 tasks/notes/resources/projects/preferences 全集合）；触发条件（默认使用即命中，讲义生成/agent 运行/批量复习时放大最明显）；受损面（性能：渲染成本 O(集合规模×事件数) 放大）。

### 10. 专注计时器每秒触发全 App 重渲染
- `App.tsx:70/147-157`——focusSeconds 为 App 级 state，每 1000ms set；renderPage 全部页面无 memo——25 分钟专注 = 1500 次全页 reconcile。
- 传递链条：触发（用户在专注模式开始计时（25 分钟会话））→ 根因（`App.tsx:70` focusSeconds 为 App 顶层 state，`App.tsx:147-157` 每 1000ms setInterval set 一次）→ 传导（App 每秒重渲染，`App.tsx:232-251` renderPage 内全部页面组件无 memo 全量 reconcile，25 分钟≈1500 次）→ 症状（专注期间整个前台页面每秒 diff 一次，叠加其他交互持续耗电卡顿）。
- 影响范围：功能面（专注计时运行期间的整个应用前台页面）；触发条件（需启动专注计时，默认使用不命中）；受损面（性能：恒定每秒全树重渲染，低端机可感）。

### 11. 全局搜索输入 state 挂在 App 顶层，每击键重渲染整个应用
- `App.tsx:66/287`——叠加已知后端 LIKE 扫描，击键成本 = 后端扫描 + 前端全页 diff。
- 传递链条：触发（用户在全局搜索框（Ctrl+K 或顶栏入口）输入）→ 根因（`App.tsx:66` query 挂在 App 顶层 state，`App.tsx:287` 输入框每次击键 setQuery）→ 传导（每击键一次全树 reconcile；停顿 120ms（App.tsx:97 防抖）再叠加一次后端全库 LIKE 扫描）→ 症状（击键成本=前端全页 diff+一次全库扫描，库大时输入明显迟滞）。
- 影响范围：功能面（全局搜索模态及当前渲染中的页面）；触发条件（默认使用即命中，笔记/材料越多后端扫描越重）；受损面（性能：输入路径同时放大前端渲染与后端扫描）。

### 12. [弱] Agent 会话搜索每击键对 ≤50×100 消息全文 join 过滤，无预建索引
- `AgentCenter.tsx:50`——含大体积工具输出文本；规模有界故弱。
- 传递链条：触发（用户在 Agent 工作区会话搜索框输入）→ 根因（`AgentCenter.tsx:50` filteredSessions 每次把每会话全部消息 content join 成长串再 includes 过滤，无预建索引）→ 传导（规模有界：会话≤50（agent/mod.rs:437 LIMIT 50）、单会话消息≤100（agent/mod.rs:332 LIMIT 100），但文本含大体积工具输出，每次击键全量重算 O(总会话字符数)）→ 症状（多会话长历史时搜索框轻微卡顿，过滤结果仍正确）。
- 影响范围：功能面（Agent 助手页会话列表过滤）；触发条件（默认使用即命中，但 50×100 上限封顶故影响弱）；受损面（性能：每击键全量字符串拼接扫描，轻量可感）。

### 13. react-markdown+KaTeX 全链进主包，5 页 lazy 分包对重依赖无实际收益
- `App.tsx:6` 静态导入 StudyRoom（其静态导入 markdown/katex 链）——主包 871KB 含 KaTeX 运行时；Library 懒块仅 28KB（依赖复用主包未分割）——从不打开讲义的用户首屏也加载 KaTeX。
- 传递链条：触发（应用启动加载首屏主包，任何用户必经）→ 根因（`App.tsx:6` 静态导入 StudyRoom，其 `StudyRoom.tsx:3-6` 静态拉入 react-markdown/remark-gfm/remark-math/rehype-katex 链）→ 传导（KaTeX 运行时与全部字体资产进主包：实测 dist/assets/index-*.js 871,291 字节；5 页 lazy（App.tsx:32-36）中 Library 块仅 27,885 字节，重依赖未随懒块分割）→ 症状（从不打开讲义的用户首屏也要下载解析约 871KB 主包，启动时间与内存占用抬高）。
- 影响范围：功能面（所有用户的首屏加载；讲义渲染仅 StudyRoom 使用）；触发条件（默认使用 100% 命中）；受损面（性能：首屏体积与启动耗时恒定多付 KaTeX 成本）。

### 14. [待确认] 打包图标未声明（tauri.conf icon:[]），安装包/主程序可能用 Tauri 默认图标
- `tauri.conf.json:31`；icons/icon.ico 存在且 favicon/应用内品牌齐备——需实际安装验证 NSIS/exe 图标是否回落默认标志。
- 传递链条：触发（若执行 make build 打 NSIS 安装包并安装）→ 根因（`src-tauri/tauri.conf.json:31` bundle.icon 为空数组，打包器无图标输入；src-tauri/icons/icon.ico 虽存在但未被配置引用）→ 传导（安装器与主程序 exe 的图标资源可能回落 Tauri 默认标志）→ 症状（桌面/开始菜单/exe 呈默认图标而非品牌图标，需实装验证）。
- 影响范围：功能面（NSIS 安装包与主程序 exe 图标，运行时功能无损）；触发条件（仅打包发布场景命中）；受损面（品牌一致性轻微受损）。判定倾向：很可能命中默认回落，补 "icon": ["icons/icon.ico"] 后实装验证即可闭环。

## plugins / maintenance / notes 运维面

### 15. [待确认] install 的"已安装"检查与失败清理跨连接竞态，可误删他人已装插件的 manifest.json
- `plugins/mod.rs:425-460`——EXISTS 检查与 INSERT 不同事务；失败清理无条件删 manifest 且不删目录——并发 install 同 id 时后者失败会把前者清单删掉（单窗口正常操作不可达，需并发 IPC）。
- 传递链条：触发（若并发发起两个同 id 插件的 install IPC）→ 根因（`plugins/mod.rs:426-436` EXISTS 检查在事务外，`:439` 才开事务 INSERT）→ 传导（两个调用都能通过存在性检查；后失败方在 `:457-460` 无条件 remove_file(manifest.json) 且不删目录，把先成功方已落盘的清单删掉）→ 症状（先成功方插件"数据库已装但磁盘无清单"，下次 reload 报 plugin_manifest_missing（:532-537））。
- 影响范围：功能面（插件 install 命令与 Integrations 页安装入口）；触发条件（单窗口正常操作不可达，需并发 IPC 双 install 同 id）；受损面（数据安全：可误删他人已装插件的清单文件）。判定倾向：机制经代码复核属实，但 UI 串行调用下现实触发概率极低，低优先级修复。

### 16. uninstall 先提交 DB 删除再删目录：目录删除失败时"已卸载却报错"；not_found 分支永不清理磁盘残留
- `plugins/mod.rs:578-591`——Windows 文件占用常见；残留目录无入口清理（运维只认含 manifest 的目录，见 #19）。
- 传递链条：触发（用户卸载插件时目录被占用/删除失败——Windows 常见，或重复卸载）→ 根因（`plugins/mod.rs:584` 先提交 DB 删除，`:586-589` 之后才 remove_dir_all；`:580-581` not_found 分支直接 return 不碰磁盘）→ 传导（前者 DB 已无记录但磁盘删除抛错返回失败，重试即 not_found；无 manifest 的残留目录不被运维孤儿检测识别（maintenance/mod.rs:287 只认含 manifest.json 的目录））→ 症状（"已卸载却报错"的状态矛盾，残留目录永久无清理入口）。
- 影响范围：功能面（插件卸载命令及后续体检/cleanup 运维）；触发条件（需目录删除失败（文件占用）或重复卸载，Windows 下较易命中）；受损面（可用性+磁盘卫生：状态矛盾与不可清理残留）。

### 17. reload 成功提交后 record_reload 失败把"已生效的变更"报为失败；审计 generation 事后重读可记错代数
- `plugins/mod.rs:500-509/570-571`——commit 后用新连接重查 generation 而非本次写入值。
- 传递链条：触发（用户 reload 插件：主事务成功但记录阶段遇错；或并发 reload 同一插件）→ 根因（`plugins/mod.rs:570-571` 主更新 commit 后才调 record_reload，其 `:500` 另开连接、`:502-509` 事后重查 generation 而非用 `:559` 本次写入的 generation+1）→ 传导（记录失败时 reload 返回 Err 但变更已生效；并发下可能读到他人递增后的代数写入 plugin_reloads）→ 症状（UI 报"重载失败"而配置实际已更新，审计表代数可能记错）。
- 影响范围：功能面（插件 reload 命令与 plugin_reloads 审计记录）；触发条件（需记录阶段 DB 忙/错或并发 reload，正常单操作多不命中）；受损面（可用性+审计准确性：结果与反馈不一致）。

### 18. list_plugin_events 固定最新 100 条无分页，更早投递永久不可见
- `plugins/mod.rs:636`——event_count 可远超 100 且 deliveries 无保留期清理——"真实事件投递"计数与可见记录不一致。
- 传递链条：触发（插件投递累计超过 100 条后用户打开插件事件页）→ 根因（`plugins/mod.rs:636` 固定 ORDER BY event_id DESC LIMIT 100，无分页/游标参数）→ 传导（plugin_deliveries 只增不删（全库无 DELETE 该表语句，dispatch 于 :619 仅插入），而 plugin_row 的 event_count（:387-391）统计全量行数）→ 症状（计数与可见记录永久不一致，第 100 条之前的更早投递无任何入口可见）。
- 影响范围：功能面（Integrations 页插件事件列表与运维排查取证）；触发条件（投递量>100，活跃插件长期运行必然命中）；受损面（可运维性：历史事件投递不可追溯）。

### 19. plugins 孤儿检测只认"含 manifest.json 的目录"：无清单的残留目录与 .manifest-N.tmp 永不报告永不清理
- `maintenance/mod.rs:279-299`——install 失败遗留空目录（plugins/mod.rs:352/457-460）不在任何扫描类别。
- 传递链条：触发（install 中途失败留下空插件目录或 .manifest-N.tmp 残留）→ 根因（`maintenance/mod.rs:281` 只检查目录下 manifest.json，`:287` 仅当 `!installed && path.is_file()` 才列为孤儿候选）→ 传导（无清单的目录与 tmp 文件不进 plugins/tempdirs 任何扫描类别，scan 与 cleanup 基于同一扫描结果）→ 症状（残留永不报告、永不清理，随失败次数线性累积）。
- 影响范围：功能面（数据体检孤儿扫描与 cleanup 清理白名单）；触发条件（需 install 失败遗留，正常安装不命中；一旦命中即永久滞留）；受损面（数据卫生/磁盘：运维面存在盲区残留）。

### 20. cleanup 无数量上限无分批；CleanupResult.failures 预装扫描期 issues 把"提示性跳过"当"删除失败"展示；VACUUM 成功后 wal_checkpoint busy 使整条命令误报失败
- `maintenance/mod.rs:457/459-487/507-519`——孤儿量大时长时间占锁；"符号链接已跳过"与真失败混排（MaintenancePanel.tsx:60 同图标）；VACUUM 已完成但报"请稍后重试"并落 maintenance.failed。
- 传递链条：触发（用户执行"预演清理→确认清理"或"整理数据库"）→ 根因（`maintenance/mod.rs:459-487` 单循环删除全部孤儿无上限无分批；`:457` failures 预装入扫描期 issues；`maintenance/mod.rs:507` VACUUM 已完成后 `:508-519` wal_checkpoint busy 即整体返回 Err，经 emit_result（:374-382）落 maintenance.failed）→ 传导（孤儿量大时长占库锁阻塞其他命令；跳过类提示与真删除失败混入同一 failures 列表；VACUUM 实际成功却报错）→ 症状（清理期间界面阻塞，结果面板把"跳过"当"失败"同图标警示（MaintenancePanel.tsx:60），整理数据库假失败误导重试）。
- 影响范围：功能面（设置-数据运维的清理与整理数据库命令及 maintenance.* 事件审计）；触发条件（孤儿量大才占锁可感；含符号链接/清单告警或检查点被占即误报，默认小库不命中）；受损面（性能+可用性：占锁与假失败误导运维判断）。

### 21. 知识库体检循环内 learning::get 的任意错误直接中止整个 get_library_overview：单点坏数据使体检完全不可用
- `notes/library.rs:130`——一条方案 result_json 损坏即让"知识库检查"弹窗整体报错，四类统计全部不可见。[待确认触发需库内 JSON 已损坏]
- 传递链条：触发（若库内某方案 result_json 已损坏/不可解析，用户打开知识库体检）→ 根因（`notes/library.rs:130` 循环内 learning::get 的任意错误用 `?` 直接上抛）→ 传导（get_library_overview 整体失败返回，笔记/批注/关联/重复四类统计一个都不产出）→ 症状（"知识库检查"弹窗整体报错，其余健康数据全部不可见）。
- 影响范围：功能面（知识库体检整个功能）；触发条件（需库内方案 JSON 已损坏才触发，正常数据不命中）；受损面（可用性：单点坏数据令体检完全不可用）。判定倾向：机制属实，应把单条失败降级为 issue 而非中止，待构造损坏数据验证。

### 22. 重复正文检测把全部笔记正文完整复制进内存 BTreeMap（理论上限约 2×10GB）
- `notes/library.rs:89-101`——合法规模 10000 条×1MB 时内存翻倍暴涨；现实规模无感。[待确认实际数据量]
- 传递链条：触发（用户触发知识库体检且笔记正文总量大）→ 根因（`notes/library.rs:93` 每条笔记 content .to_string() 复制，`:101` 存入 contents BTreeMap 供重复检测）→ 传导（合法配额 10,000 条（store.rs:781）×单条上限 1MB（store.rs:786-791）时，集合本体之外再驻留约 10GB 复制正文，峰值内存≈双份）→ 症状（极端规模内存翻倍暴涨可能 OOM，现实百条级规模无感）。
- 影响范围：功能面（知识库体检的重复正文检查）；触发条件（需笔记数×正文体积逼近配额上限，日常规模不命中）；受损面（性能/资源：极端规模内存翻倍）。判定倾向：机制属实但现实数据量达不到，观察即可、不必改。

## REF/ 参考源盘点（4 项，均记录性）

### 23. REF 的 dev/preview 服务器绑定 0.0.0.0 全网卡
- `REF/frontend/app/vite.config.ts:6`、package.json:7-9——若有人把 REF 当可运行项目启动，演示服务暴露局域网（desktop 已改 127.0.0.1）。
- 传递链条：触发（若有人把 REF 当可运行项目，在其目录执行 npm run dev/preview）→ 根因（`REF/frontend/app/vite.config.ts:6` server.host '0.0.0.0'，package.json:7/9 两脚本同样 --host 0.0.0.0）→ 传导（dev/preview 服务监听全部网卡，同网段任意设备可达）→ 症状（演示页面暴露局域网）。
- 影响范围：功能面（仅 REF 参考源的开发/预览脚本；desktop 侧 vite.config.ts、package.json:9 与 tauri.conf.json:8 均已绑定 127.0.0.1）；触发条件（需有人手动在 REF/ 下启动服务才成立，交付物无此执行路径）；受损面（安全暴露面——当前不构成缺陷：条件链不成立即无后果）。

### 24. REF 三项演示能力未进入 desktop（有意裁剪，其中一项属明令禁止）
- 剪藏扩展（AGENTS 明令禁止）、"MCP 工具桥"演示插件（desktop 插件为纯声明清单）、Python 运维面板——参考源演示声称与交付面的残留差异，需用户知情确认。
- 传递链条：触发（若有人阅读 REF 演示页（Integrations.tsx:8/35/44）并据此预期 desktop 同名能力）→ 根因（REF 演示含 MCP 工具桥插件、Python 插件安装与运维面板、浏览器剪藏扩展下载三项声称）→ 传导（desktop 插件仅纯声明清单+订阅（plugins/mod.rs:441 只存 manifest_json/subscriptions），无 Python/扩展宿主；剪藏扩展属 AGENTS 明令禁止项）→ 症状（参考源声称与交付面能力不对齐，产生预期落差）。
- 影响范围：功能面（无运行时功能面，仅文档/预期管理）；触发条件（需有人以 REF 为能力依据才产生误解）；受损面（认知一致性——当前不构成缺陷：裁剪有意且剪藏为禁止项，但需用户知情确认）。

### 25. REF/PRODUCT.md 自我定性（web 演示原型）与交付物（Tauri 原生）相反
- 已被外层 NOTICE/README 的"参考源非交付目录"声明消解；单独阅读 REF 文档会得到相反架构印象。
- 传递链条：触发（若有人绕过外层声明、单独打开 `REF/frontend/app/PRODUCT.md`（Platform: web、React+Vite 栈）了解架构）→ 根因（该文档自我定性停留在 web 演示原型，未随 Tauri 化更新）→ 传导（与实际交付（src-tauri/tauri.conf.json 的 Tauri 2 原生应用）相反；外层 NOTICE:21 与 README:63 已声明 REF 为参考源非交付目录）→ 症状（单独读 REF 文档者得到"web 原型"的错误架构印象）。
- 影响范围：功能面（仅 REF 文档阅读场景，无运行时功能面）；触发条件（需有人绕过外层声明单独读 REF 文档）；受损面（认知一致性——当前不构成缺陷：外层声明已消解）。

### 26. REF 不入库，唯一副本是工作区本地文件
- zip 与 app/ 互为备份；异地备份由用户自行决定（备份性观察，非缺陷）。
- 传递链条：触发（若工作区磁盘损坏/目录误删，且用户未自备异地备份）→ 根因（`.gitignore:11` `/REF` 使 REF 不入版本库，zip（REF/frontend/DeepTutor.zip）与 app/ 目录仅在同一磁盘互为备份）→ 传导（git 历史不含 REF 任何内容，无法从仓库恢复）→ 症状（单盘两副本可能同灾同失）。
- 影响范围：功能面（无运行时功能面，仅资产保管）；触发条件（需工作区丢失且未自备异地备份才成立，日常不命中）；受损面（资产安全——当前不构成缺陷：备份策略由用户自行决定，记录性观察）。
