# 55 · Provider 流式解析对抗（第二十三轮）

> 审计方式：对 openai.rs（1399 行）/protocol.rs/anthropic.rs/gemini.rs/
> responses.rs 全文深读，对每个 SSE/JSON/错误边界构造具体畸形输入并逐行
> 推演代码路径。主审对 P2 与三条 P3 错误路径逐行亲验。已有测试覆盖的
> happy path/取消/中断/重试场景不重复（见文末）。

## P2

### 55#1 换行符洪泛 → O(N²) memmove CPU 放大——行数与总字节数无上限，取消是唯一出口

- 位置：`desktop/src-tauri/src/backend/providers/openai.rs:1109-1123`（stream_once）与 `protocol.rs:312-326`（stream_events，Anthropic/Gemini/Responses 共用）——两处相同（主审亲验）
- 证据：
  ```rust
  while let Some(end) = pending.iter().position(|byte| *b == b'\n') {
      …
      let line = pending.drain(..=end).collect::<Vec<_>>();   // 头部 drain → 尾部整体左移
  ```
- **传递链条（构造与推演）**：① 恶意/故障端点返回 200 + `text/event-stream`，body 为持续不断的 `"\n"`（或 `": x\n"`）。64KB chunk 全换行 → 约 6.5 万次 drain，每次把平均 32KB 尾部 memmove——约 2×10⁹ 字节拷贝/64KB 流量（**~32000 倍 CPU 放大**），且每行一个 Vec 分配。行数/累计字节数无上限（64KB 上限只约束单行与 leftover）；chunk 持续到达使 chunk_timeout 永不触发；文本 1MB 上限只计已 emit 的 content（空行不计）；取消检查夹在每行 memmove 之间，响应也迟钝。
- **影响范围**：攻击面为用户自配「兼容服务」端点（deep link 一键导入 baseUrl，mod.rs:692-744 降低门槛）；一次请求即可让线程池核满直至手动取消。内存有界、CPU 无界。修复：行循环改游标偏移（避免 drain），对 pending 设总字节/行数预算，无进展字节计入 chunk_timeout。
- **同根（P3 合并记录）**：tool_call 条目数（distinct index）无上限（openai.rs:953 `entry(index)`、anthropic.rs:207、responses.rs:166）——畸形流可用每条 ~80 字节的事件让 BTreeMap 无限增长直至 EOF 才在 finish() 报错，放大系数 ≈2×。

## P3

### 55#2 `"error": null` 误杀 OpenAI Chat 整条流——四协议口径不一致

- 位置：`openai.rs:907`（主审亲验）；对照 `gemini.rs:162`（已过滤 null）。
- **传递链条**：① 兼容网关在正常 chunk 里回显 `"error": null` 字段 → ② consume_line 解析 JSON 后 `body.get("error").is_some()` 对 `Some(Value::Null)` 判真 → ③ 整条流立即以 `provider_error`（不在重试白名单）终止 → ④ 已输出的内容只保留在 partial、请求必然失败——尽管 choices 内容完全正常。
- **影响范围**：使用会回显 error:null 的网关的用户——该供应商所有流式请求 100% 失败，错误消息（「模型服务在流式响应中返回了错误」）与真实原因（网关冗余字段）无关，排障极难。Gemini/Anthropic/Responses 三路径无此问题（口径不一致本身也是维护陷阱）。

### 55#3 非流式 HTTP 200 内嵌 error 对象——细节丢失或被静默忽略

- 位置：`openai.rs:449-454`（主审亲验）。
- **传递链条**：① 供应商以 200 返回 `{"error":{"message":"Insufficient credits"}}`（无 choices）→ ② `completion()` 直接取 choices → `ok_or_else` 报「模型响应缺少 choices」——真实原因（配额耗尽）丢弃；或 ① choices 与 error 并存 → ② 无 error 检查 → 按成功处理，error 被静默忽略。
- **影响范围**：供应商测试（test_provider realRequest）与非流式调用用户——测试结果「缺少 choices」无法定位到配额/权限类真实原因；并存分支的半失败响应被当全成功。修复：completion 补 error 提取（对齐流式路径与 Gemini 的做法）。

### 55#4 4xx/5xx 的响应体从不读取——供应商诊断信息与 Cloudflare HTML 全丢弃

- 位置：`openai.rs:310-314`（主审亲验）+ `:330-342`。
- **传递链条**：① 供应商/CDN 返回 400/403/429/5xx → ② `read_json` 见非 200 直接丢弃连接（不读 body）→ ③ `http_error(status)` 按状态码映射固定中文——`400 {"error":{"message":"Invalid model: foo"}}` 的具体 message 丢失；403 实为 Cloudflare WAF 拦截（HTML 页）却提示「请检查 API Key」→ ④ 用户被误导去改正确的 Key。
- **影响范围**：所有供应商故障排障场景（配错模型名、额度、地区限制、WAF）——错误提示只剩状态码猜谜。修复：非 200 时读 body 前 4KB，提取 error.message 截断展示。

### 55#5 流式 body 中途网络错误归为不可重试的 invalid_response——不重试也不故障转移

- 位置：`openai.rs:1137`、`protocol.rs:340`。
- **传递链条**：① 服务器发完响应头与若干 delta → ② 网络瞬时故障（RST/超时）→ ③ `Ok(Err(_))` 一律映射「流式响应读取失败」（invalid_response）→ ④ 该码不在重试白名单也不在 can_failover 清单 → 空 partial 的首连接毛刺直接失败、路由不切换上游——瞬时抖动被当永久错误。
- **影响范围**：网络不稳定的直连用户（路由用户失去 failover 保护）；已有 partial 不重试是正确的防重复计费设计，受害的只是「一个字都没收到就断」的场景。修复：空 partial 的 body 读取错误归入可重试类。

### 55#6 孤立 CR 行尾不识别（违反 SSE 规范）——整流失败且报错误导

- 位置：`openai.rs:1109`/`protocol.rs:312`（仅按 `\n` 切行）。
- **传递链条**：① 代理/手写服务器以 CR（\r）作行终止（SSE 规范允许）→ ② 解析器找不到 `\n`，整个 body 滞留 pending → ③ 总量 <64KB 时 EOF 分支把整个 body 当**一行**交给 consume_line → ④ JSON 解析失败 → 报「流式事件不是有效 JSON」——误导排查方向（其实是行尾协议问题）。
- **影响范围**：经 CR-only 代理接入的用户——该端点所有流式请求失败且错误文案答非所问；主流官方 API 均用 LF/CRLF，正常配置不受影响。

### 55#7 BOM 前缀使首个事件被静默丢弃

- 位置：`openai.rs:895`、`protocol.rs:286`。
- **传递链条**：① 某些网关在流首输出 UTF-8 BOM（EF BB BF）→ ② 首行变为 `\u{FEFF}data: {...}`，`strip_prefix("data:")` 失败 → ③ 静默 `Ok(false)` 跳过（无日志）→ ④ 首事件丢失：若为内容首字则输出缺头；若为 Anthropic message_start 则 usage 全丢（missing_usage 计数+1）。
- **影响范围**：经 BOM 网关接入的用户——偶发首 token 丢失或用量缺失，且完全无诊断线索。修复：行首剥 BOM 一次。

### 55#8 Anthropic/Responses 同 index 起始块静默覆盖——对比 OpenAI 路径有 id 冲突检查

- 位置：`anthropic.rs:207`、`responses.rs:166`。
- **传递链条**：① 畸形/故障服务器对同一 index 发两条 content_block_start/tool_use（id A 与 B）→ ② `output.tool_calls.insert(index, B)` 直接覆盖 A → ③ A 已 emit 的 partial 参数静默丢弃、无任何报错 → ④ 下游拿到截断的工具调用。对照 OpenAI 路径 :956 有「同索引不同 ID」fail-closed 检查。
- **影响范围**：仅畸形服务器触发（正常 Anthropic/Responses 不会重发 index）；后果为静默数据丢失而非崩溃。修复：对齐 OpenAI 的冲突检查。

### 55#9 Gemini 多 candidate 全量 emit——重复输出

- 位置：`gemini.rs:202`。
- **传递链条**：① 网关/配置使 candidates 数组含多个元素（candidateCount>1 或畸形回显）→ ② `for candidate in candidates` 全量处理（OpenAI 路径只取 choices[0]）→ ③ 每个候选的文本都 emit → ④ 输出内容成倍重复。
- **影响范围**：使用 candidateCount>1 或会回显多候选网关的 Gemini 用户——输出重复、用量按重复文本计量。正常单候选配置零影响。修复：只取 candidates[0]（或首个有内容的候选）。

### 55#10 每 delta 一条 DB 事件 + IPC 广播——上限 1MB 输出可放大至约百万行事件

- 位置：`mod.rs:1166-1174`（on_delta 内 Store::event INSERT + emit，解析层无合并）。
- **传递链条**：① 上游按每 delta 1 字节下发（合法 SSE，每事件 ~60 字节，绕过一切行/文本上限——文本上限只约束累计 emit）→ ② 每条 delta 落一行 events 表 + 一次 IPC 广播 → ③ 1MB 输出 = ~100 万次 INSERT + 100 万次前端事件 → ④ events 表 ~100MB 膨胀、UI 事件风暴、后续 events_since 分页与 maintenance 截断压力。
- **影响范围**：恶意/故障端点可主动构造（与 55#1 同攻击面）；正常端点 delta 粒度为词级（数千条）无感。总量受 1MB 文本上限约束（有界但巨大）。修复：解析层按字节/时间窗合并 delta 事件。

## 信息性

- **多行 data 不拼接**：链条：SSE 规范要求同事件多条 `data:` 行以 `\n` 拼接后分发 → 实现逐行独立解析 → JSON 跨行网关首行即报 invalid_response。影响：JSON 单行的主流网关零影响；与 Gemini alt=sse 网关兼容性取决于对方实现。
- **tool_call 严格点三处**：链条：缺 `function` 字段的 keep-alive 探针 → 整流硬错；缺 `index` 归 0 → 同 chunk 两个无 index 调用拼到一个参数里（finish 时报参数非 JSON）；负 index 归 0 → 与真实 0 冲突时 fail-closed 报不同 ID。影响：仅畸形流触发，方向均为安全侧（报错优于错数据）。
- **协议语义透传**：链条：① Anthropic stop_reason 原样透传不映射 OpenAI 语义（下游仅存储展示）→ ② Gemini inlineData 回图静默丢弃（候选只含图时 empty_completion）→ ③ 非末 chunk finishReason 后同 chunk 事件丢弃。影响：展示层差异（停止原因为英文枚举、模型回图丢失），无逻辑分支依赖这些值。
- **非流式超时分类略偏**：链条：body 中途超时 → read_to_end 失败 → invalid_response（而非 provider_timeout）。影响：不重试不放大，仅错误码语义略偏。
- **OpenRouter**：链条：① `: OPENROUTER PROCESSING` 注释行 → 以 `:` 开头被跳过（正确）；② `delta.reasoning` 字段 → 三字段循环覆盖（正确）；③ 流内错误 → 走 openai.rs:907 → message 细节丢失。影响：前两项无问题；第三项与 55#2/#4 同族合并处理。
- **「代码已修、夹具未锁」**：链条：25#17/31#36/31#39 等边界修复只以代码注释锚定 → 可见测试无对应回归用例（grep response_too_large 在测试零命中）→ 未来重构易回退且无测试报警。影响：纯回归保护缺口，建议各补一例。

## 正向结论（对抗性验证过、安全）

- **分块与编码**：事件任意字节分裂安全（UTF-8 连续字节 ≥0x80 不含 0x0A，行边界永不切断多字节字符）；非 UTF-8 → 类型化错误带 partials；CRLF/data 无空格/多空格/空行/`:` 注释行全部正确处理。
- **delta 三态**：content 缺失/null/空串三态区分正确；`choices:[]` 兼容尾部 usage-only chunk；usage or-merge 不会被空对象抹掉（31#39）；finish_reason 中途出现不丢后续文本。
- **tool_calls 重组**：index 乱序/稀疏（BTreeMap）、id 中途补写、同 index 异 id fail-closed、参数 UTF-8 片段拼接必然合法、拼接失败延迟到 finish() 报 invalid_tool_arguments 且 partial_tool_calls 阻断重试（不双倍计费）、单参数 256KB 上限。
- **协议族**：Anthropic 完整事件链（thinking/partial_json 256KB/usage 合并/流内 error 提取且 overloaded 可重试）；Gemini（error null 已过滤、blockReason/SAFETY→content_blocked、usage 含 thoughts 求和不回退、functionCall 缺 name/args 报错、id 合成）；Responses（added/delta/done 全链、max_output_tokens→length 不失败 10#4、三级 message 回退）。
- **超时与取消**：响应头超时/body 停顿（100ms 轮询+chunk_timeout）/取消逐行检查、取消保留 partial、已有 partial 绝不重试。
