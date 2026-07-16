# 04 - MVP 任务分解与风险登记

## 一、MVP 任务分解（Phase 1）

### P0：核心链路（必须完成）

**骨架与基础设施**
- [ ] backend 骨架：FastAPI 入口、路由注册、CORS、异常处理
- [ ] frontend 骨架：Vite + React + Tailwind + 路由 + TanStack Query
- [ ] docker-compose（frontend nginx + backend uvicorn + 卷）
- [ ] README（启动说明）+ .env.example
- [ ] core：settings（pydantic-settings）、logging

**数据层**
- [ ] SQLAlchemy 模型（全部表）+ SQLite 初始化
- [ ] Alembic 迁移脚手架

**LLM 与 Agent 核心**
- [ ] LLM Provider 抽象（`LLMProvider` 接口：chat、usage 解析）
- [ ] Anthropic 实现（Messages API + prompt caching + streaming）
- [ ] Agent 循环：工具注册表、pydantic schema 校验
- [ ] 工具调用修复（schema 不符 -> tool_result 反馈 -> 重试 3 次 -> 降级告警）
- [ ] 成本计量（解析 usage -> 写 api_usage -> 会话聚合）
- [ ] 预算守卫（累计 cost 超阈值中断）

**工具链（P0 范围）**
- [ ] parse_material（PDF/DOCX/PPTX/TXT/MD + text + url）
- [ ] understand_intent
- [ ] build_knowledge_map
- [ ] design_stages
- [ ] search_resources + verify_url（含 ✅⚠️❌ 标记）
- [ ] schedule_plan
- [ ] validate_plan（5 项检查 + 修复建议 + 反馈循环）
- [ ] export_plan（md）

**Skill 层（skill-for-skills，详见 05-skills.md）**
- [ ] 用 skill-chain-planner 规划主链路（产出接口契约与创建顺序）
- [ ] skill-for-skills 生成 deep-tutor-intent
- [ ] deep-tutor-knowledge-map
- [ ] deep-tutor-stages
- [ ] deep-tutor-resources（含 verify 方法论 + ✅⚠️❌ 规则）
- [ ] deep-tutor-validate（5 项检查）
- [ ] Python Skill 加载器（扫描 skills/ -> 注册表 -> 工具调用注入 system prompt + pydantic 校验）

**API**
- [ ] /materials/upload | /url | /text
- [ ] /sessions
- [ ] /plans/generate（SSE）
- [ ] /plans（list） | /plans/{id}
- [ ] /export/{plan_id}

**前端**
- [ ] Layout + 路由
- [ ] 首页/新建（MaterialUploader：上传+链接+粘贴+描述）
- [ ] 澄清对话页（ChatClarify）
- [ ] 生成中页（GenerateProgress：SSE 进度 + 实时用量）
- [ ] 方案详情页（PlanOutline + StageCard + ResourceCard + Timeline + ValidationReport）
- [ ] 设置页（API Key/模型/预算）

**端到端**
- [ ] 联调：描述"我想学 X" + 上传 PDF -> 生成完整方案
- [ ] 联调：纯需求描述（无资料）-> 生成方案
- [ ] 基础冒烟测试

### P1：增强（MVP 后紧跟）
- [ ] PDF 导出（export_plan 扩展 pdf）
- [ ] build_plan_slides（复用 ppt-slide-builder，输出 HTML 幻灯片）
- [ ] 方案版本化（plans.version + 列表区分）
- [ ] 局部重生成（/plans/{id}/regenerate）
- [ ] 成本看板页（/usage 可视化）

### P2：自学材料 + 测验 + 进度
- [ ] generate_study_materials（复用 self-study-guide，逐单元自学材料）
- [ ] study_units / study_materials 表 + API（/plans/{id}/materials）
- [ ] 前端自学材料阅读页 + StudyMaterialReader（见 06）
- [ ] generate_quiz + record_progress + 学习中心 + 进度看板

---

## 二、路线图（总览）

| 阶段 | 目标 | 关键交付 |
|---|---|---|
| **P1 MVP** | 资料/需求 -> 完整学习方案 | 大纲+路径+时间+资源(带验证)+校验 |
| P2 | 测验 + 进度 | generate_quiz、record_progress、学习中心、进度看板 |
| P3 | 多模态 | 图片 OCR、音频/视频转写 |
| P4 | 多用户 + 扩展 | JWT、云部署、Skills/MCP、浏览器插件 |
| P5 | 优化 + 导出 | 缓存深度优化、Anki/ics 导出、协作分享 |

---

## 三、风险登记表

| # | 风险 | 类别 | 概率 | 影响 | 应对策略 |
|---|---|---|---|---|---|
| R1 | Anthropic API 超时/限流 | external | 中 | 高 | 重试 3 次 + 指数退避；超预算中断返回部分结果 + 告警 |
| R2 | 工具调用 schema 不符 | silent | 中 | 中 | 工具调用修复机制（反馈重试 3 次）-> 降级默认参数 + 告警 |
| R3 | 资源链接失效 | data | 高 | 中 | verify_url 标记 ✅⚠️❌；失效换源；不编造 URL |
| R4 | 大文件解析超时/OOM | resource | 低 | 中 | 限制单文件大小 + 分块解析 + 超时阈值 |
| R5 | 静默错误（解析出空文本） | silent | 中 | 高 | parse_material 输出校验：空文本 -> 明确 `empty_content` 错误标记 |
| R6 | 成本失控 | cascade | 中 | 高 | 预算守卫 + 实时 usage 展示 + 会话级上限 |
| R7 | 方案校验失败但未修复 | logic | 中 | 中 | validate_plan 严重项触发上游重生成；残留风险记录并提示用户 |
| R8 | 缓存命中率低致成本高 | resource | 低 | 中 | system/tools 固定前缀 + cache_control；监控 cache hit |
| R9 | 多模态解析依赖缺失（P3） | dependency | 中 | 中 | 启动时检测 tesseract/whisper，缺失则禁用对应入口 + 提示安装 |
| R10 | 单文件并发写入冲突（多资料） | resource | 低 | 中 | 每会话独立子目录 + 唯一文件名 |
| R11 | Skill 输出 schema 与 Python 期望漂移 | silent | 中 | 高 | Skill Constraints 固化 schema + Python pydantic 校验 + 工具调用修复兜底 |
| R12 | Skill 升级引入回归 | logic | 中 | 中 | skill-for-skills UPGRADE 回归审查 + 版本号 + schema 校验 + 冒烟 |
| R13 | Skill 加载失败/缺失 | dependency | 低 | 高 | 启动检测必需 Skill，缺失报错提示；降级内置默认 prompt |
| R14 | ppt-slide-builder 仅出 HTML 非 .pptx | 约束 | 中 | 低 | 文档明确"幻灯片=HTML"；后续如需 .pptx 再接 python-pptx，另起工具 |
| R15 | 幻灯片内容过多/超模板容量 | resource | 中 | 中 | 按阶段分多个 deck 或精简要点；单 deck 页数上限 |
| R16 | 自学材料生成成本高（多单元多 LLM 调用） | cascade | 中 | 高 | 单元数上限/分批生成；复用 plan 上下文缓存；预算守卫 |
| R17 | 单元内容过长或质量不均 | silent | 中 | 中 | 单文件>500 行拆分；每单元≥2 练习校验；抽样审查 |
| R18 | 上下文窗口溢出（长资料） | resource | 中 | 高 | 分块 chunks + map-reduce/截断标注；阈值可配；审计记录 |
| R19 | SSRF（/materials/url 内网 URL） | security | 中 | 高 | URL 校验禁私网/环回/元数据端点；重定向上限 3 跳 |
| R20 | 路径遍历（上传文件名） | security | 中 | 高 | 用 material_id 命名；resolve 校验在 data/ 内 |
| R21 | XSS（渲染用户/网络内容） | security | 中 | 高 | rehype-sanitize + HTML 白名单 + CSP + sandbox iframe |
| R22 | 工具调用死循环 | logic | 低 | 中 | 每会话工具调用上限 20；超限中断返回部分 |
| R23 | zip bomb/超大解压（PPTX/DOCX） | resource | 低 | 高 | 解压大小+文件数上限；拒绝超限 |
| R24 | 中断未恢复（崩溃丢进度） | cascade | 中 | 中 | 持久化 partial_plan+tool_calls；断点续接/幂等重跑 |
| R25 | 编码错检（GBK 等） | silent | 中 | 中 | chardet/charset-normalizer 转 UTF-8；校验解码率 |
| R26 | 大文本塞 SQLite 致膨胀/锁 | resource | 中 | 中 | 大内容入文件系统，DB 存路径；WAL；定期 VACUUM（见 09） |
| R27 | 配置缺失/非法（启动） | dependency | 中 | 高 | 启动校验必填 env，缺失报错退出；.env.example 模板 |
| R28 | 部署卷丢失数据 | resource | 低 | 高 | 卷持久化；定期备份；删容器不删卷 |

### 关键静默错误防护（参考 skill-chain-planner Step 5.5）
- API 返回 200 但 body 为错误对象 -> 校验响应 schema 而非仅 HTTP 状态。
- 转换后文件非空但内容为乱码/错误页 -> 校验输出包含预期标记（标题/结构关键字）。
- 数据被截断无通知 -> 校验输出行数/大小与输入对应；截断/分块写入 tool_calls 审计。
- 密码/扫描 PDF 误判为空 -> 区分 empty_content / password_required / scanned_pdf_suggest_ocr，不混为一种。

---

## 四、第一步动作（批准后）

1. 建骨架（backend/frontend/docker-compose/README/.env.example）-> `chore: scaffold project skeleton`
2. LLM Provider 抽象 + Anthropic 实现 -> `feat(llm): provider abstraction + anthropic`
3. Agent 循环骨架（工具注册 + 调用修复 + 成本计量）-> `feat(agent): core loop with tool-call repair and cost tracking`
4. P0 工具链（每工具独立 commit）-> `feat(tools): parse_material` 等
5. Skill 层：skill-chain-planner 规划 + skill-for-skills 生成各 Skill + Python 加载器 -> `feat(skills): deep-tutor-* skills and loader`
6. API 层 -> `feat(api): materials/sessions/plans endpoints`
7. 前端 P0 页面 -> `feat(frontend): generate flow and plan detail`
8. 端到端联调 -> `test: e2e smoke for plan generation`

**全程细粒度 git commit，便于版本管理与回滚**（遵循全局规范：commit 信息不含 co-author）。
