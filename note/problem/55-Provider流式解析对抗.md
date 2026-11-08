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
- **构造与推演**：恶意/故障端点返回 200 + `text/event-stream`，body 为持续不断的 `"\n"`（或 `": x\n"`）。64KB chunk 全换行 → 约 6.5 万次 drain，每次把平均 32KB 尾部 memmove——约 2×10⁹ 字节拷贝/64KB 流量（**~32000 倍 CPU 放大**），且每行一个 Vec 分配。行数/累计字节数无上限（64KB 上限只约束单行与 leftover）；chunk 持续到达使 chunk_timeout 永不触发；文本 1MB 上限只计已 emit 的 content（空行不计）；取消检查夹在每行 memmove 之间，响应也迟钝。
- **影响范围**：攻击面为用户自配「兼容服务」端点（deep link 一键导入 baseUrl，mod.rs:692-744 降低门槛）；一次请求即可让线程池核满直至手动取消。内存有界、CPU 无界。修复：行循环改游标偏移（避免 drain），对 pending 设总字节/行数预算，无进展字节计入 chunk_timeout。
- **同根（P3 合并记录）**：tool_call 条目数（distinct index）无上限（openai.rs:953 `entry(index)`、anthropic.rs:207、responses.rs:166）——畸形流可用每条 ~80 字节的事件让 BTreeMap 无限增长直至 EOF 才在 finish() 报错，放大系数 ≈2×。

## P3

### 55#2 `"error": null` 误杀 OpenAI Chat 整条流——四协议口径不一致

- 位置：`openai.rs:907`（主审亲验：`body.get("error").is_some()` 未过滤 null）；对照 `gemini.rs:162` 有 `.filter(|e| !e.is_null())`、anthropic 按 `type:"error"` 事件、responses 只认类型化事件。部分网关回显 `error:null` 时整流以 provider_error（不可重试码）终止，尽管 choices 内容正常。

### 55#3 非流式 HTTP 200 内嵌 error 对象——细节丢失或被静默忽略

- 位置：`openai.rs:449-454`（completion 不检查 error，主审亲验）。`200 {"error":{"message":"Insufficient credits"}}` → 报「模型响应缺少 choices」，真实原因丢失；choices 与 error 并存时按成功处理。供应商测试（test_provider realRequest）走此路径，用户无法得知配额耗尽。

### 55#4 4xx/5xx 的响应体从不读取——供应商诊断信息与 Cloudflare HTML 全丢弃

- 位置：`openai.rs:310-314`（非 200 直接 `http_error(status)`，主审亲验）+ `:330-342`（按状态码映射固定中文）。`400 {"error":{"message":"Invalid model: foo"}}` 的 message 丢弃；403 实为 Cloudflare WAF 拦截却提示「请检查 API Key」误导排查方向。Content-Type 从不校验。

### 55#5 流式 body 中途网络错误归为不可重试的 invalid_response——不重试也不故障转移

- 位置：`openai.rs:1137`、`protocol.rs:340`（`Ok(Err(_))` 一律「流式响应读取失败」）。服务器发完头部与一半 delta 后 RST——瞬时网络毛刺被当永久错误（该码不在重试白名单也不在 can_failover 清单）。已有 partial 不重试是正确防重复计费，但空 partial 的首连接毛刺也被拒绝。

### 55#6 孤立 CR 行尾不识别（违反 SSE 规范）——整流失败且报错误导

- 位置：`openai.rs:1109`/`protocol.rs:312` 仅按 `\n` 切行。`data: {...}\rdata: [DONE]\r`（CR-only，规范允许、代理可能发出）→ 全部滞留 pending；<64KB 时 EOF 把整个 body 当一行 → JSON 解析失败报「不是有效 JSON」。

### 55#7 BOM 前缀使首个事件被静默丢弃

- 位置：`openai.rs:895`、`protocol.rs:286`（strip_prefix("data:") 前无 BOM 剥离）。`\u{FEFF}data: {...}` 首行静默跳过——若首事件是 Anthropic message_start（usage 在其中）则用量全丢（missing_usage+1）且无任何诊断。

### 55#8 Anthropic/Responses 同 index 起始块静默覆盖——对比 OpenAI 路径有 id 冲突检查

- 位置：`anthropic.rs:207`、`responses.rs:166`（`insert(index, …)` 直接覆盖）。同 index 两条 content_block_start/tool_use：后者覆盖前者，已 emit 的 partial 参数静默丢弃无报错；OpenAI 路径 :956 有「同一索引不同 ID」fail-closed 检查。

### 55#9 Gemini 多 candidate 全量 emit——重复输出

- 位置：`gemini.rs:202`（`for candidate in candidates` 不取 [0]）。candidateCount>1 或网关畸形时文本重复 emit（OpenAI 路径只取 choices[0]）。

### 55#10 每 delta 一条 DB 事件 + IPC 广播——上限 1MB 输出可放大至约百万行事件

- 位置：`mod.rs:1166-1174`（on_delta 内 Store::event INSERT + emit，解析层无合并）。1MB 输出按每 delta 1 字节下发（每事件 ~60 字节 SSE，绕过一切行上限）→ ~100 万次 INSERT + IPC，events 表 ~100MB 膨胀与 UI 事件风暴（总量受文本 1MB 上限约束，有界但巨大）。

## 信息性

- 多行 `data:` 不按规范拼接（逐行独立解析，JSON 单行网关无实际影响）；`data: "[DONE]"` 带引号 → incomplete_stream（可接受严格度）。
- tool_call 缺 `function` 字段（keep-alive 探针）→ 整流硬错 invalid_tool_call；缺 `index` 归 0（同 chunk 两个无 index 调用拼接后报参数非 JSON）；负 index 归 0 与真实 0 冲突时 fail-closed。
- Anthropic stop_reason 原样透传不映射 OpenAI 语义（下游仅存储展示，无逻辑依赖）；Gemini inlineData（模型回图）静默丢弃、非末 chunk finishReason 后同 chunk 事件丢弃。
- 非流式 body 中途超时分类为 invalid_response 而非 provider_timeout（不重试不放大，仅分类略偏）。
- OpenRouter：`: OPENROUTER PROCESSING` 注释行与 `delta.reasoning` 三字段循环均正确；流内错误 message 丢失（=55#2 同族）。
- **「代码已修、夹具未锁」**：25#17（256KB 参数）、31#36（64KB 整行）、31#39（空 usage 不覆盖）等修复以注释锚定，但可见测试中无 response_too_large 等对应回归用例。

## 正向结论（对抗性验证过、安全）

- **分块与编码**：事件任意字节分裂安全（UTF-8 连续字节 ≥0x80 不含 0x0A，行边界永不切断多字节字符）；非 UTF-8 → 类型化错误带 partials；CRLF/data 无空格/多空格/空行/`:` 注释行全部正确处理。
- **delta 三态**：content 缺失/null/空串三态区分正确；`choices:[]` 兼容尾部 usage-only chunk；usage or-merge 不会被空对象抹掉（31#39）；finish_reason 中途出现不丢后续文本。
- **tool_calls 重组**：index 乱序/稀疏（BTreeMap）、id 中途补写、同 index 异 id fail-closed、参数 UTF-8 片段拼接必然合法、拼接失败延迟到 finish() 报 invalid_tool_arguments 且 partial_tool_calls 阻断重试（不双倍计费）、单参数 256KB 上限。
- **协议族**：Anthropic 完整事件链（thinking/partial_json 256KB/usage 合并/流内 error 提取且 overloaded 可重试）；Gemini（error null 已过滤、blockReason/SAFETY→content_blocked、usage 含 thoughts 求和不回退、functionCall 缺 name/args 报错、id 合成）；Responses（added/delta/done 全链、max_output_tokens→length 不失败 10#4、三级 message 回退）。
- **超时与取消**：响应头超时/body 停顿（100ms 轮询+chunk_timeout）/取消逐行检查、取消保留 partial、已有 partial 绝不重试。
