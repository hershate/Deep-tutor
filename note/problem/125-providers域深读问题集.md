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

- 位置：`desktop/src-tauri/src/backend/providers/routing.rs:1015-1029`（attempt=1 行存在且 task/provider/model 一致 → `return latest_chat`）；`:1007-1014`（existing 查询不筛 `upstream_request_id IS NOT NULL`——circuit_open 跳过行也触发幂等返回）。
- 传递链条：①某请求路由全部候选熔断 → attempts 表留下 attempt=1 行（upstream 为 NULL 的 circuit_open 行）、终态 all_providers_unavailable；②60s 熔断冷却过后用户/上层用**同一 request_id** 再调 stream_chat；③幂等检查命中 attempt=1 行且三元组一致 → 直接返回缓存的失败结果，不重新路由；④「熔断冷却后重试即可恢复」的用户预期永不成立——直到换新 id（前端失败后换 UUID，agent/learning 仅对 done 复用 id，故现网几乎不触发）。
- 影响范围：失败后以同 id 重试的调用方（实际调用面现网趋零——各调用方失败即换新 id）；换 id 重试与直连路径（mod.rs:1084-1099 同语义但有 done 前置）不受影响；无数据损坏，受损的是重试语义的可预期性与注释真实性。
- 修复方向：existing 查询筛掉 circuit_open 跳过行（或注释/熔断文案明示「需新请求 id 重试」）。

### 125#4 本地无 Key 时仍发送空值鉴权头——个别网关对空头返回 401

- 位置：`desktop/src-tauri/src/backend/providers/anthropic.rs:132-138`（`x-api-key: `）、`gemini.rs:147-153`（`x-goog-api-key: `）、`protocol.rs:555-575`（list_models 同型）。
- 传递链条：①用户配置本地模型（llama.cpp 等）且不填 API Key——本地档位允许无凭据；②`key.unwrap_or("")` 后无条件 append header → 请求携带空字符串值的 `x-api-key:`/`x-goog-api-key:`；③个别网关按「提供了无效 key」处理返回 401，而非按「无头」放行；④用户看到的错误指向鉴权失败，与「本地无 Key 可用」的产品语义相悖。
- 影响范围：无 Key 本地档位 + 严格网关组合的连接诊断（401 误导）；主流本地引擎（忽略空头）与有 Key 档位不受影响；纯兼容性，无安全面。
- 修复方向：Key 为空时不 append 对应 header（三处同改）。

### 125#5 finish_reason 词汇归一不完整——「展示层三协议一致」（99#10）未完全达成

- 位置：`desktop/src-tauri/src/backend/providers/anthropic.rs:283-288`（只归一 max_tokens→length、tool_use→tool_calls，`end_turn`/`stop_sequence` 原样）、`gemini.rs:256-263`（只归一 MAX_TOKENS→length，`STOP` 原样）；protocol_tests.rs:393 断言 `finish_reason == "STOP"` 即实证。
- 传递链条：①Anthropic/Gemini 流正常结束；②finish_reason 以各自原词汇（end_turn/STOP）落 provider_requests；③前端/日志展示该字段时三种拼法（stop/end_turn/STOP）并存；④按 finish_reason 分支消费的展示逻辑（如「正常结束」判定）需枚举三套词汇——99#10 注释宣称的归一口径未兑现。
- 影响范围：任何按 finish_reason 展示或分支的消费者（当前 UI 仅透传展示，无功能分支）；length/tool_calls 两个已归一项与 OpenAI 路径不受影响；纯语义一致性，无行为错误。
- 修复方向：match 补 end_turn/stop_sequence/STOP → stop 归一；测试同步改断言。

### 125#6 docs §5「每个 delta 双写」描述过时——55#10 已改为 4KB/150ms 缓冲合并

- 位置：`docs/architecture/02-operation-principles.md` §5 第 6 条（「每个 delta 双写：Store::event 落库 + emit」）vs `providers/mod.rs:1253-1313`（flush 闭包 4096 字节与 150ms 双阈值合并 + 前沿触发 + 流末 force flush）。
- 传递链条：①读者按文档推演 events 表行数 = delta 数；②实际 55#10 后按缓冲窗口合并——行数 ≈ max(输出量/4KB, 时长/150ms)；③恢复重建按事件拼接输出语义不变（缓冲不丢内容）；④文档与实现的行数量级差一个数量级——容量估算与按行数排障的读者被误导。
- 影响范围：按文档估算 events 增长量/排查流式问题的维护者；运行行为与恢复语义不受影响；纯文档滞后。
- 修复方向：§5 该条改为缓冲合并描述（阈值+前沿触发+流末 flush 三个要素）。

### 125#7 每次请求新建 reqwest Client + list() 逐档案读 keyring——无跨请求连接复用

- 位置：`desktop/src-tauri/src/backend/providers/openai.rs:284-308`（两个 builder 每次 `Client::builder()...build()`，连接池/TLS 会话随对象丢弃）；`mod.rs:385`（profile() 对每行做 `state.credentials.get(&id)` keyring 读取）+ `:415-426`（list() 逐行经 profile()）。
- 传递链条：①同一供应商高频调用（生成链/Agent 流式）；②每次请求完整 TCP+TLS 握手（DNS 钉扎要求每请求解析在安全上正确，但 Client 对象本可按 (host,钉扎地址,kind) 缓存复用连接池）；③ProviderHub 每条 provider.* 事件触发 refresh→list→N 档案 = N 次系统密钥库调用；④同供应商请求延迟常数项抬高、密钥库读放大——慢网/慢盘环境可感。
- 影响范围：高频流式请求与多档案供应商页的延迟常数（无功能错误）；首启低频调用与单档案用户不受影响；性能观察项。
- 修复方向：Client 按 (host, 钉扎地址, kind) 缓存；list() 的 has_key 批量化或缓存。

### 125#8 运行中请求每次轮询全量重放 delta 事件重建输出

- 位置：`desktop/src-tauri/src/backend/providers/mod.rs:1017-1038`（status != done 且 output 为空时从 events 表按序拼接全部 delta——每次 O(已输出长度)）；前端 150ms 节流拉 getProviderChat 各触发一次。
- 传递链条：①流式进行中 provider_requests.output 恒为空（终态才 UPDATE，:1348-1351）；②前端每次轮询 getProviderChat；③后端每次从 events 重放拼接**全部**历史 delta——长输出下每 150ms 一次 O(N) 重复 CPU/IO（有表达式索引查询本身不慢）；④55#10 缓冲已把事件数压到 ~7/s，实际负担有限；终态后 output 非空即短路。
- 影响范围：长输出流式期间的轮询开销（重复重放，无正确性问题）；短输出与终态读取不受影响；性能观察项。
- 修复方向：重放结果缓存（或进行中定期物化部分输出），低优先。

### 125#9 models 列表项不做 display_safe 校验——109#11 注入面未对齐

- 位置：`desktop/src-tauri/src/backend/providers/mod.rs:480-489`（models 仅查非空与 ≤200 字符）vs `:443-454`（name/default_model/note 均过 display_safe，109#11）。
- 传递链条：①用户经「从 /models 拉取」回填或 JSON 导入写入含 U+202E/bidi 伪装或控制字符的模型名到 models[]；②validate 只查长度不查 display_safe；③该模型名在 UI 下拉（ProviderHub 模型选择）与后续请求体的 model 字段中原样出现；④视觉伪装（字符顺序颠倒的假模型名）与 109#11 已在 name/default_model/note 修掉的注入面同型——同函数内防护不一致。
- 影响范围：models 列表含恶意/异常字符的档案（显示层伪装面）；name/default_model/note（已防护）与正常模型名不受影响；无执行面（纯展示与请求体字符串），损害是显示完整性与防护口径一致性。
- 修复方向：models 逐项过 display_safe（与 109#11 同款断言）。

## 待核

### 125#10 Anthropic 并行工具结果被拆成多条独立 user 消息——严格校验的代理/网关可能 400

- 位置：`desktop/src-tauri/src/backend/providers/anthropic.rs:43-50`（每个 tool 消息各生成一条 `{"role":"user","content":[tool_result]}`，无相邻合并逻辑）。
- 传递链条：①上游一次返回 N 个并行 tool_use（agent 域允许单步 ≤8）；②历史映射为 assistant(tool_use×N) → user(tool_result₁) → user(tool_result₂)…；③Anthropic 官方语义把连续 user 消息合并为单轮——多数情况可行；④按「严格角色交替 + tool_result 必须紧跟 tool_use」校验的代理/网关返回 400，run 失败且历史重放每次携带同样序列——是否触发取决于上游实现（官方 API 大概率放行）。
- 影响范围：使用严格校验代理/网关 + 并行工具调用的组合（需真实端点验证）；官方 API 与单工具调用不受影响；待核项，潜在请求失败面，无数据损坏。
- 修复方向：相邻 tool 消息合并为单条 user 的 content 数组（官方推荐形态）；先构造复现再定。

### 125#11 HTTP 客户端未禁系统代理——代理环境下 DNS 钉扎与公网校验被架空

- 位置：`desktop/src-tauri/src/backend/providers/openai.rs:284-308`（两个 builder 均无 `.no_proxy()`）；reqwest 默认读 HTTP(S)_PROXY/系统代理；对照 `web/mod.rs:156` 明确 `.no_proxy()`（项目已知该面）。
- 传递链条：①用户环境配置系统/环境代理；②providers 的模型请求实发代理服务器——resolve_to_addrs 的 DNS 钉扎只约束「直连时的目标地址」，经代理后由代理解析与连接；③public_address 的私网拒绝同样被架空（代理侧可连私网地址再回传）；④ProviderHub「远程档案要求 HTTPS 与公网地址」的安全承诺在代理场景不成立——SSRF 防护面从「端点校验」退化为「代理可信」。
- 影响范围：配置了代理且依赖 SSRF 防护承诺的安装（防护语义失效）；无代理环境（钉扎全效）与 web 抓取域（已 no_proxy）不受影响；安全语义弱化，无直接可利用路径（需用户自己配置恶意代理，威胁模型边缘）。
- 修复方向：是否支持企业代理先产品确认；若支持则在文档/承诺中限定语境，若不支持则补 `.no_proxy()` 对齐 web 域。

### 125#12 stream_options include_usage 无条件下发——不支持该字段的严格实现可能 400

- 位置：`desktop/src-tauri/src/backend/providers/openai.rs:1188-1194`（body 构造无条件写入 `"stream_options": {"include_usage": true}`，无 preset 分支）。
- 传递链条：①用户配置 llama.cpp/旧版 vLLM/杂牌兼容网关为 OpenAI Chat 端点；②每次流式请求 body 携带 stream_options；③不支持该字段的严格实现按 unknown field 拒绝 → 400 → 请求失败（failover 逐候选同 payload 全灭）；④主流端点（OpenAI/新 vLLM 等）容忍——面收敛在兼容层长尾。
- 影响范围：严格校验 body 的兼容网关用户（全部流式请求失败）；主流端点不受影响；待实测确认长尾面，无数据损坏。
- 修复方向：按 preset/档案开关（默认开、可关）；或失败时提示该字段可能原因。

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
