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

### 2. 主按钮深色悬停变深绿底配近黑字（约 2:1）且色相突变
- `styles.css:145-146`——`.btn-primary:hover` 不分主题用 pine-700；深色文字 pine-950——全站保存/生成按钮悬停瞬间难读。

### 3. 大字模式下阅读区反而小于周边正文
- `styles.css:1022-1023`——body 18px vs `.reading-article` 钉在 1.08rem（17.28px）——学习空间核心阅读面在大字模式下比其余文字更小（09#6 的新增数据点）。

### 4. 专注模式内 Ctrl+K 打开被完全遮挡的搜索模态，键盘焦点被抢进隐藏输入框
- `App.tsx:141-142` 无 focusOpen 守卫；`.overlay` z70 < `.focus-overlay` z90（不透明 pine-950）；模态 autoFocus+Modal 20ms 聚焦——用户盲打且每次击键触发全量 LIKE 搜索；Esc 可恢复。

### 5. 32 个 class + 1 个 keyframes 确认零引用（约 130 行死样式）
- 全量 599 class 逐一比对：typing/formula-block/transcript/video-*/evidence-list/practice-strategy/data-status/example-plugins/extension-card/pricing-*/provider-layout 等（styles.css:356-1318 各处）——ProviderHub 重写与讲义改 KaTeX 后的遗留；另 `.event-测评` 的"测评"枚举不可达（store.rs:827 仅四种 kind）。

### 6. 反向缺口：4 组运行时使用但零 CSS 规则的类，呈浏览器默认控件样式
- `ProviderRouting.tsx:73`（route-log-entry/route-attempt*）、`StudyRoom.tsx:206-207`（lecture-checkpoint/lecture-citations）、`MediaPlayer.tsx:47`（native-media-preview）——路由日志/原文引用按钮为无样式系统灰按钮。

### 7. `.task-row.is-done { opacity: 1 }` 为无效死规则
- `styles.css:235`——全文件无任何规则调暗未完成行，疑似"完成项淡化"旧设计残留。

## 前端性能

### 8. 今日任务列表无上限全量渲染：tasks/resources/projects 无配额（上限 10000），最坏 10,000 行 ≈ 8 万 DOM 节点
- `Dashboard.tsx:49` 全量 map 无虚拟化；行高 72px 列表高 72 万 px；每次勾选全量数组提交（叠加 05#8 冲突语义）；`Plan.tsx:153` 周历同构。

### 9. 每个后端事件全量重拉全集合快照并全树重渲染（渲染层放大，区别于已知请求层问题）
- `useBackendState.ts:38/51-57`——每事件 getAppState（全集合）→ setSnapshot 恒新对象 → App 全树 reconcile；讲义逐单元/agent 逐步期间每事件一次全量序列化+diff。

### 10. 专注计时器每秒触发全 App 重渲染
- `App.tsx:70/147-157`——focusSeconds 为 App 级 state，每 1000ms set；renderPage 全部页面无 memo——25 分钟专注 = 1500 次全页 reconcile。

### 11. 全局搜索输入 state 挂在 App 顶层，每击键重渲染整个应用
- `App.tsx:66/287`——叠加已知后端 LIKE 扫描，击键成本 = 后端扫描 + 前端全页 diff。

### 12. [弱] Agent 会话搜索每击键对 ≤50×100 消息全文 join 过滤，无预建索引
- `AgentCenter.tsx:50`——含大体积工具输出文本；规模有界故弱。

### 13. react-markdown+KaTeX 全链进主包，5 页 lazy 分包对重依赖无实际收益
- `App.tsx:6` 静态导入 StudyRoom（其静态导入 markdown/katex 链）——主包 871KB 含 KaTeX 运行时；Library 懒块仅 28KB（依赖复用主包未分割）——从不打开讲义的用户首屏也加载 KaTeX。

### 14. [待确认] 打包图标未声明（tauri.conf icon:[]），安装包/主程序可能用 Tauri 默认图标
- `tauri.conf.json:31`；icons/icon.ico 存在且 favicon/应用内品牌齐备——需实际安装验证 NSIS/exe 图标是否回落默认标志。

## plugins / maintenance / notes 运维面

### 15. [待确认] install 的"已安装"检查与失败清理跨连接竞态，可误删他人已装插件的 manifest.json
- `plugins/mod.rs:425-460`——EXISTS 检查与 INSERT 不同事务；失败清理无条件删 manifest 且不删目录——并发 install 同 id 时后者失败会把前者清单删掉（单窗口正常操作不可达，需并发 IPC）。

### 16. uninstall 先提交 DB 删除再删目录：目录删除失败时"已卸载却报错"；not_found 分支永不清理磁盘残留
- `plugins/mod.rs:578-591`——Windows 文件占用常见；残留目录无入口清理（运维只认含 manifest 的目录，见 #19）。

### 17. reload 成功提交后 record_reload 失败把"已生效的变更"报为失败；审计 generation 事后重读可记错代数
- `plugins/mod.rs:500-509/570-571`——commit 后用新连接重查 generation 而非本次写入值。

### 18. list_plugin_events 固定最新 100 条无分页，更早投递永久不可见
- `plugins/mod.rs:636`——event_count 可远超 100 且 deliveries 无保留期清理——"真实事件投递"计数与可见记录不一致。

### 19. plugins 孤儿检测只认"含 manifest.json 的目录"：无清单的残留目录与 .manifest-N.tmp 永不报告永不清理
- `maintenance/mod.rs:279-299`——install 失败遗留空目录（plugins/mod.rs:352/457-460）不在任何扫描类别。

### 20. cleanup 无数量上限无分批；CleanupResult.failures 预装扫描期 issues 把"提示性跳过"当"删除失败"展示；VACUUM 成功后 wal_checkpoint busy 使整条命令误报失败
- `maintenance/mod.rs:457/459-487/507-519`——孤儿量大时长时间占锁；"符号链接已跳过"与真失败混排（MaintenancePanel.tsx:60 同图标）；VACUUM 已完成但报"请稍后重试"并落 maintenance.failed。

### 21. 知识库体检循环内 learning::get 的任意错误直接中止整个 get_library_overview：单点坏数据使体检完全不可用
- `notes/library.rs:130`——一条方案 result_json 损坏即让"知识库检查"弹窗整体报错，四类统计全部不可见。[待确认触发需库内 JSON 已损坏]

### 22. 重复正文检测把全部笔记正文完整复制进内存 BTreeMap（理论上限约 2×10GB）
- `notes/library.rs:89-101`——合法规模 10000 条×1MB 时内存翻倍暴涨；现实规模无感。[待确认实际数据量]

## REF/ 参考源盘点（4 项，均记录性）

### 23. REF 的 dev/preview 服务器绑定 0.0.0.0 全网卡
- `REF/frontend/app/vite.config.ts:6`、package.json:7-9——若有人把 REF 当可运行项目启动，演示服务暴露局域网（desktop 已改 127.0.0.1）。

### 24. REF 三项演示能力未进入 desktop（有意裁剪，其中一项属明令禁止）
- 剪藏扩展（AGENTS 明令禁止）、"MCP 工具桥"演示插件（desktop 插件为纯声明清单）、Python 运维面板——参考源演示声称与交付面的残留差异，需用户知情确认。

### 25. REF/PRODUCT.md 自我定性（web 演示原型）与交付物（Tauri 原生）相反
- 已被外层 NOTICE/README 的"参考源非交付目录"声明消解；单独阅读 REF 文档会得到相反架构印象。

### 26. REF 不入库，唯一副本是工作区本地文件
- zip 与 app/ 互为备份；异地备份由用户自行决定（备份性观察，非缺陷）。
