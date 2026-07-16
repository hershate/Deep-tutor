# 09 - 计划审计：风险与边界情况（迭代补充）

> 目标：反复检查全计划，补齐未考虑的风险/边界/不详细处，"不允许有未规定的细节或风险"。本文件按类别迭代补充。第 1 轮覆盖：存储策略、上下文溢出、安全、解析边界、Agent 守卫、数据/运维、前端边界。

## 一、存储策略（DB vs 文件系统）— 回应用户示例

### 问题
SQLite TEXT/BLOB 默认上限 1GB（`SQLITE_MAX_LENGTH=10^9`），但把长文本（解析全文、自学材料、大方案 JSON）塞进 DB 会：DB 文件膨胀、备份/查询变慢、单写者锁竞争（P4 多用户）、VACUUM 成本高。`materials.parsed_text`、`study_materials.content_md/content_html`、export/slides 均可能很大。

### 决策：大内容入文件系统，DB 存路径 + 元数据
| 内容 | 存储 | DB 字段 |
|---|---|---|
| 上传原件 | 文件 | `materials.source`(path/url) |
| 解析文本 | 文件 | `materials.parsed_text_path` + `parsed_chars` |
| 自学材料 content_md/content_html | 文件 | `study_materials.content_md_path`/`content_html_path` + `size` |
| 导出 md/pdf、幻灯片 html | 文件 | 按需记录 url |
| `plan.content`(JSON) | DB（<512KB）；超阈值溢出文件 | `plans.content` 或 `plans.content_path` |
| 元数据/进度/用量/审计 | DB | 各表 |

### 目录布局
```
data/
├── deep-tutor.db
└── sessions/<session_id>/
    ├── originals/<material_id>.<ext>   # 上传原件
    ├── parsed/<material_id>.txt        # 解析文本
    ├── plans/<plan_id>.json            # 大方案溢出
    ├── materials/<unit_id>.{md,html}   # 自学材料
    └── exports/<plan_id>.{md,pdf,html} # 导出/幻灯片
```

### SQLite 配置与运维
- 启用 WAL（`journal_mode=WAL`、`synchronous=NORMAL`），并发读写友好。
- 上传单文件默认上限 50MB（可配置）；解析文本无硬限但超长走分块（见二）。
- 备份：定期 `.backup` 或复制 `deep-tutor.db` + `data/` 目录。
- 清理：删 session 级联删 `sessions/<sid>/`；定期清孤儿文件；周期性 VACUUM。
- P4 多用户：迁 Postgres + 对象存储（S3/MinIO），路径字段抽象为 `storage_url`。

### 与工具信封的关系
08 的 `ToolOutput.data` 在流水线中**内存传递**实际文本；**持久化层**（本节）把大字段写文件、DB 存路径。两层分离，互不影响。

## 二、上下文窗口溢出（长资料 > Claude 上下文）

### 问题
大 PDF / 多资料合并后 `parsed_text` 可能超模型上下文窗口（如 200k tokens），全量送 LLM 会报错或截断丢失。

### 策略
- `parse_material` 输出 `chunks`（按 token 上限分块，如每块 8k tokens）。
- `understand_intent` / `build_knowledge_map` 等：总量超阈值时 -> map-reduce（分块抽取 -> 合并）/ 截断 + 标注"已截断前 N 字，完整见文件" / 先摘要再处理。
- 阈值可配（模型上下文 − 预留输出 − system）。
- 是否截断/分块写入 `tool_calls` 审计。
- 风险 R18。

## 三、安全

### SSRF（`/materials/url`）
- 用户可能提交内网 URL（`http://localhost`、`169.254.169.254`、私网 IP）。
- 防护：仅 http/https；解析主机 IP 拒绝私网/环回/链路本地/保留段；重定向上限 3 跳；禁元数据端点。

### 路径遍历（上传文件名）
- 用户文件名含 `../` 或绝对路径。
- 防护：用 `material_id` 命名，不直接用用户文件名；路径拼接限定在 `data/sessions/<sid>/` 内；写前校验 `resolve()` 仍在根目录内。

### XSS（渲染用户/网络内容）
- 方案/资源标题/材料文本来自用户与网络，渲染到前端。
- 防护：`react-markdown` + `rehype-sanitize`；HTML 输出（材料/幻灯片）用白名单标签/属性；CSP 头；幻灯片/材料 HTML 用 sandbox iframe。

### API Key
- 仅本地 `.env`，不入库、不入日志、不返回前端（设置页掩码）。

### 本地文件沙箱
- 后端仅可读写 `data/` 与 `skills/`；禁止任意路径访问。

## 四、解析边界（parse_material）
- 密码保护 PDF -> `password_required`，提示提供无密码版。
- 扫描件 PDF（无文本层）-> `scanned_pdf_suggest_ocr`，提示 P3 OCR；MVP 不强行 OCR。
- 编码检测（GBK/UTF-8/UTF-16）-> `chardet`/`charset-normalizer` 转 UTF-8；校验解码率。
- 损坏文件 -> `parse_failed` + 降级（能提取多少提多少，标 `degraded`）。
- PPTX/DOCX 内嵌媒体 -> 仅提取文本，标注"媒体已跳过"。
- zip bomb（PPTX/DOCX 是 zip）-> 解压大小/文件数上限，拒绝超限。
- 超大文件 -> 拒绝（超上限）或分块（见二）。
- 空文件 -> `empty_content`（R5）。

## 五、Agent 守卫
- 工具调用最大轮次：每会话生成上限 N（默认 20）-> 超限中断返回部分 + 告警（防死循环，R22）。
- 反馈循环最大重生成轮次：validate -> regenerate 上限 2 轮 -> 仍不过则交付当前 + 标注未过项（R7 补轮次上限）。
- 预算守卫（R6）、token 上限、工具调用修复重试上限 3（R2）。

## 六、数据与运维
- 中断恢复：生成中崩溃 -> 已产出 `tool_calls` + `partial_plan` 持久化；支持"从断点继续"或幂等重跑（R24）。
- 幂等：相同输入重跑可复现（可选缓存 LLM 响应）。
- 日志：记工具调用/用量/错误；不记 PII 与 API key；日志轮转。
- PII：材料文本会发往 Anthropic API（学习方案生成必需）；本地不外传第三方；隐私说明写入设置页。
- 数据保留：默认永久（本地）；"删除会话"级联清理文件。
- WebSearch 限流/key（`search_resources`）-> 退避 + 关键词降级（02 已有）。
- `verify_url` 的 robots.txt/ToS -> 遵守 robots，仅校验不存全文。

## 七、前端边界
- 长方案渲染 -> 大纲虚拟化（`react-window`）避免卡顿。
- SSE 断线 -> 重连后从 last `tool_calls` 续接（后端按会话状态）。
- 大文件上传 -> 进度条（MVP 整体上传 + 进度；超大可分片）。
- 幻灯片/材料 HTML 渲染 -> sandbox iframe 或 sanitize。

## 八、本轮新增风险（并入 [04](./04-mvp-risk.md) 风险表）
R18 上下文溢出、R19 SSRF、R20 路径遍历、R21 XSS、R22 工具调用死循环、R23 zip bomb/超大解压、R24 中断未恢复、R25 编码错检、R26 大文本塞 SQLite 致膨胀/锁。详见 04。

## 九、待后续轮次深化的方向
- 输入校验规则全集（字段级 min/max/regex/枚举）。
- 并发与事务（多资料并行解析的隔离、DB 事务边界）。
- 国际化（中英文输出语言匹配、时区/deadline）。
- 测试与 QA 策略（单元/集成/端到端/契约测试）。
- 可观测性（metrics、tracing、健康检查）。
- 失败注入与降级矩阵。
