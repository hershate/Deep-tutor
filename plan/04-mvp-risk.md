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

### 关键静默错误防护（参考 skill-chain-planner Step 5.5）
- API 返回 200 但 body 为错误对象 -> 校验响应 schema 而非仅 HTTP 状态。
- 转换后文件非空但内容为乱码/错误页 -> 校验输出包含预期标记（标题/结构关键字）。
- 数据被截断无通知 -> 校验输出行数/大小与输入对应。

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
