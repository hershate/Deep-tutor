# 125 号：providers 域全量逐行深读问题集（第四十二轮）

审计基线 `3da53213`。审计面：providers 域全部 15 文件约 8400 行实现
（mod/openai/anthropic/gemini/responses/protocol/routing/usage/tools/
sync_config/commands）首次整体逐行深读 + ProviderHub/provider.ts 契约核对
（此前各轮只修过单点，密度从 08#4 到 109#11）。经子代理逐文件探查 + 主审
逐条实证。共 2 P3 + 7 信息 + 3 待核。

## P3

### 125#1 共享 SSE 泵的空闲预算被「任意 data 行」豁免——Anthropic/Gemini/Responses 三协议存在无时间上限的挂起窗口，与 OpenAI 私有泵口径不一致

- 位置：`desktop/src-tauri/src/backend/providers/protocol.rs:359`（`has_event_line = has_event_line || line_is_data_event(&pending[cursor..end])`——:207-217 把任何携带 data 载荷的行含 `data: {}`/`data: {"type":"ping"}` 都视为「有产出」）+ `:371-380`（`if !has_event_line { idle_bytes += … }`——有 data 行即清零）；对照 `openai.rs:1294-1303` 私有泵用 `produced_bytes()` 前后对比判定空闲（累计 1MB 无产出即 invalid_response 终止）。
- 传递链条：①故障/恶意端点对三协议持续发送零产出 data 行（心跳/空 JSON）；②`line_is_data_event` 判定为有事件 → idle_bytes 恒清零；③chunk 持续到达又不断刷新 last_chunk → 120s chunk_timeout 永不触发；④worker 无限期滞留（1MB 文本/256KB 参数/64KB 行上限均不触及）——仅能靠用户取消或 stop 15s 失败收尾；OpenAI Chat 私有路径不受影响（两套泵口径不一致，55#1 洪泛家族在共享泵留缺口）。
- 影响范围：使用 Anthropic/Gemini/Responses 协议且端点行为异常（持续零产出 data 行）的流式请求（worker 无限滞留、任务不可自愈）；正常端点与 OpenAI Chat 私有路径不受影响；资源占用面，无数据损坏。
- 修复方向：共享泵空闲判定改用产出对比（镜像 openai.rs 的 produced_bytes 口径）或对 data 行按载荷有效性（delta 非空/usage 事件）分类；补零产出 data 流的回归测试。

### 125#2 ProviderHub 手工 JSON 导入逐条 upsert、失败无快照回滚——94#2 在 sync 恢复路径修掉的「部分新档案」在同域前端路径原样存在

- 位置：`desktop/src/pages/ProviderHub.tsx:325-334`（`for (const profile of importPreview) await backend.upsertProviderProfile(profile)`）；对照 `sync_config.rs:158-173`（94#2 快照注释自述「恢复/导入逐档案 upsert 各自提交…留下部分新+部分旧」）与 `:351-373`（快照+strict 回滚）——WebDAV/S3 恢复路径有完整机制，手工 JSON 导入完全绕开。
- 传递链条：①用户导入含 N 个档案的脱敏 JSON 并确认；②逐条 upsert 各自提交，第 K 条失败（invalid_endpoint/provider_in_use/credential_error）；③前 K-1 条已落库，catch 只 notify 不回滚；④库中留下部分新档案且无撤销入口——与 sync 导入的保护不对等，用户只能手工逐个删除。
- 影响范围：手工 JSON 导入中途失败的导入者（部分档案残留无回滚）；全量成功与 sync 恢复路径（有快照）不受影响；配置状态混乱面，无数据损坏。
- 修复方向：前端循环改先全量预演（已有 importPreview 校验链）+ 后端批量事务命令（或复用 sync_config 快照机制）；至少失败时提示已导入条数与删除入口。

## 信息

### 125#3 同 request_id 重试永远命中缓存终态——含 all_providers_unavailable，熔断冷却后重试的用户预期与行为相悖

- 位置：`routing.rs:1015-1029`（attempt=1 行存在且 task/provider/model 一致 → 返回 latest_chat；:1007-1014 的 existing 查询不筛 `upstream_request_id IS NOT NULL`，circuit_open 跳过行也触发）。首次全部候选熔断后 attempts 表留 attempt=1 行，同 id 再调用永远返回缓存失败——即使 60s 冷却已过。现网调用方失败后都换新 id（前端 UUID、agent/learning 仅对 done 复用），实际影响低；行为与「熔断冷却后重试」预期不符，值得注释/文案言明。

### 125#4 本地无 Key 时仍发送空值鉴权头——个别网关对空头返回 401

- 位置：`anthropic.rs:132-138`（`x-api-key: `）、`gemini.rs:147-153`（`x-goog-api-key: `）、`protocol.rs:555-575`（list_models 同）。`key.unwrap_or("")` 后无条件 append header。本地模型允许无凭据，但空字符串头仍发送；个别网关按「空 key」处理而非「无头」。

### 125#5 finish_reason 词汇归一不完整——「展示层三协议一致」（99#10）未完全达成

- 位置：`anthropic.rs:283-288`（end_turn/stop_sequence 原样保留）、`gemini.rs:256-263`（STOP 原样保留）；protocol_tests.rs:393 断言 `finish_reason == "STOP"` 即实证。OpenAI 的 stop 与两协议的 end_turn/STOP 在字段中三种拼法并存，仅展示语义不一致。

### 125#6 docs §5「每个 delta 双写」描述过时——55#10 已改为 4KB/150ms 缓冲合并

- 位置：docs/architecture/02 §5 第 6 条 vs `mod.rs:1253-1313`（flush 闭包双阈值）。events 行数与 delta 数不再一一对应；恢复重建语义不变，纯文档滞后。

### 125#7 每次请求新建 reqwest Client + list() 逐档案读 keyring——无跨请求连接复用

- 位置：`openai.rs:284-308`（两个 builder 每次 build，连接池/TLS 会话随对象丢弃，同供应商高频调用每次完整握手；DNS 钉扎要求每请求解析在安全上正确，但 Client 可按 (host,钉扎地址,kind) 缓存）；`mod.rs:385/415-426`（list() 对每行 profile 做一次 keyring 读取，ProviderHub 每条 provider.* 事件触发 refresh→list）。性能观察项。

### 125#8 运行中请求每次轮询全量重放 delta 事件重建输出

- 位置：`mod.rs:1017-1038`（status != done 且 output 为空时从 events 按序拼接全部 delta——O(输出长度)/次）；前端 150ms 节流拉取 getProviderChat 各触发一次。有表达式索引查询不慢，55#10 已压事件数至 ~7/s，长输出下重复 CPU/IO；终态后 output 非空短路。

### 125#9 models 列表项不做 display_safe 校验——109#11 注入面未对齐

- 位置：`mod.rs:480-489`（models 仅查非空与 ≤200 字符）vs `:443-454`（name/default_model/note 均过 display_safe）。列表单项可含 U+202E/bidi 伪装字符，经 /models 回填或导入后在 UI 下拉与请求体原样出现；注入面与 109#11 同型。

## 待核

### 125#10 Anthropic 并行工具结果被拆成多条独立 user 消息——严格校验的代理/网关可能 400

- 位置：`anthropic.rs:43-50`（每个 tool 消息各生成一条 `{"role":"user","content":[tool_result]}`）。上游一次返回 N 个并行 tool_use（agent 允许 ≤8）时映射为 assistant(tool_use×N) → user(tool_result₁) → user(tool_result₂)…；连续 user 消息官方语义会合并为单轮，多数放行，但按「严格交替+tool_result 必须紧跟」校验的实现会 400。需真实端点/代理验证。

### 125#11 HTTP 客户端未禁系统代理——代理环境下 DNS 钉扎与公网校验被架空

- 位置：`openai.rs:284-308`（两个 builder 均无 `.no_proxy()`）；reqwest 默认读 HTTP(S)_PROXY/系统代理。对照 `web/mod.rs:156` 明确 `.no_proxy()`（项目已知该面）。用户环境配置代理时请求实发代理服务器，resolve_to_addrs 钉扎与 public_address 校验被绕（代理侧可连私网）；与 ProviderHub「远程档案要求 HTTPS 与公网地址」的承诺在代理场景不成立。是否有意支持企业代理待产品确认；至少安全承诺需限定语境。

### 125#12 stream_options include_usage 无条件下发——不支持该字段的严格实现可能 400

- 位置：`openai.rs:1188-1194`。对所有 OpenAI Chat 端点（含 llama.cpp/旧 vLLM/杂牌网关）强制携带；主流端点容忍。需对目标本地引擎实测；可按 preset 提供开关。

## 核实通过项（免后续复查）

- **凭据安全全链**：仅 SystemCredentials（principal 隔离命名+旧名迁移删除）；provider_profiles 无 api_key 列（PRAGMA 断言）；export_config 脱敏；事件/导出/CSV 零 token（多组测试断言）；入参 zeroize；keyring/事件写失败回滚。
- **SSRF/endpoint 链**（除 125#11 代理面）：public_address 全段拒绝（回环/私网/CGNAT/0.0.0.0/组播/文档段/NAT64 内嵌递归/3fff::/20）；endpoint 拒 userinfo/query/fragment/`..`；本地仅回环私网、远程必须 HTTPS 公网；每请求 DNS 钉扎+禁重定向；deep link https 补查（81#2）。
- **取消与超时三级**：路由 flag→parent_cancel 级联→直连 flag；SSE 泵 chunk 前后双查；流读 120s（73#11）；stop 15s；取消竞态前端静默（实测）。
- **不重复计费**：直连同 id 复用（request_count 不变测试）；路由同 id 幂等续读；partial 非空禁重试禁转移；completed_chat 供恢复判定。
- **熔断状态机**：closed→open 阈值+窗口；open→half_open CAS；half_open 悬挂探测超时逃逸（102#2）；半开取消回 open（99#7）；IMMEDIATE 读改写；无变化不发噪音（99#11/102#3）；计数收窄 routing（99#3）；Err 路径回写（34#13）；recover 收口。
- **failover 与用量**：`{request_id}-f{N}` 映射（≤60 保证后缀过校验）；ledger 经 upstream_request_id 归属（34#22）；预算 FK 主+GLOB 兜底只认 agent-{run}-*（34#16）；三协议 usage 逐字段合并（31#39/34#17）；预算检查先于 INSERT（31#4）；日预算索引（86#2）。
- **SSE 解析边界**：CRLF/LF/裸 CR；BOM；error:null 不误杀；[DONE] 与尾行（99#8）；零事件断流 endpoint_unreachable 可转移（55#5）；行 64KB/参数 256KB/文本 1MB；工具 index≤256 同 index 异 id fail-closed（55#1/55#8 三协议同口径）；游标切分消 O(N²)。
- **三协议映射**：OpenAI wire 手工 snake_case（15#5）；工具名消毒往返（10#8）；Anthropic system 抽取/tool_result；Gemini system_instruction/functionCall/OBJECT schema；Responses items 粒度 delta（102#5）；refusal（99#9）——protocol_tests 四协议全链断言。
- **契约**：20 命令/参数/字段/事件前缀/upstreamRequestId 切换逐一对上；熔断参数与 maxTokens 前后端范围一致。
- **恢复与 panic**：chats running→interrupted；route runs 终态保留已完成上游；delta 重建；panic catch_unwind 落终态（106#7）。
- **流式落库**：4KB/150ms 缓冲+前沿触发+流末 flush（55#10）；表达式索引支撑重建。
- **sync 脱敏导入**：reject_sensitive 递归；版本/上限/引用校验；快照-回滚-残留降级（94#2/103#1-3）；导入路径不触 keyring。
- **stop/删除防护**：当前供应商与路由引用禁删禁停用；删除连带清 keyring。
