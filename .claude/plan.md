# P2 generate_study_materials（简化稳定 MVP，parallel slides 模式）

plan/07 §三 全量（每单元 LLM + WebSearch + DB 持久化 + index + StudyMaterialReader + 异步 SSE）过于庞大。本轮做**简化稳定 MVP**（同 slides：一次 LLM 调用 -> 规约 -> markdown -> 前端 react-markdown 渲染），核心价值：从 plan 生成结构化自学材料供阅读。**延后**：DB 持久化（StudyUnit/StudyMaterial 表已存在但本轮不用）、每单元独立文件、WebSearch 补充资料、index 路线图、异步/SSE、StudyMaterialReader 翻页、quiz 联动。

vendor self-study-guide（SKILL.md + README）作方法论参考；LLM 用**聚焦 inline_prompt**（SKILL.md 为交互式，不宜直接作 system prompt）。复用 run_tool 修复链（≤3->降级 default_materials_from_plan）。content_md 为 markdown，前端 react-markdown 默认不渲染原始 HTML（安全，无需 slides 那种净化）。

## 增量 1（iter 34）：vendor + 模型 + 默认 + 渲染 + 测试（无 LLM）
1. 拷 `~/.claude/skills/self-study-guide/{SKILL.md,README.md}` -> `skills/self-study-guide/`。
2. 新建 `backend/app/tools/generate_study_materials.py`：
   - `StudyUnitSpec`(title/objectives/prerequisites/estimated_time/content_md) + `StudyMaterialsSpec`(title/units) pydantic。
   - `default_materials_from_plan(plan)`：纯 Python 默认（每阶段一单元，brief 内容），降级用。
   - `render_materials_md(spec) -> str`：拼成单 markdown 文档（# 标题 + 每单元 ## 标题 + frontmatter 信息 + content_md）。
3. 测试 `test_generate_study_materials.py`：render mock spec；default 渲染；loader 加载 self-study-guide。

## 增量 2（iter 35）：generate_study_materials 工具（LLM 规约 + 降级）+ 注册 + 测试
1. `generate_study_materials_tool = Tool(llm=True, output_model=StudyMaterialsSpec, default_data=_DEFAULT, inline_prompt=PROMPT)`（聚焦 prompt：plan stages -> units，每单元 content_md 按 self-study-guide 模板 概述/核心概念/实践练习≥2/检查理解/总结/扩展阅读）。注册 `default_registry`（不在 PIPELINE）。
2. 测试：mock provider 合法 spec->ok；非法->降级 repaired+默认。

## 增量 3（iter 36）：端点 + 前端
1. 新建 `backend/app/api/materials_gen.py`（或并入 exports）：`POST /plans/{plan_id}/materials` -> 读 plan content -> ctx 注 llm_provider（无 key->降级）-> `run_tool("generate_study_materials")`->spec（ok 用 LLM，否则 default_materials_from_plan）-> `render_materials_md` -> 返 `{materials_md, unit_count, warning?}`。注册路由。
2. 前端 [PlanDetail.tsx](frontend/src/pages/PlanDetail.tsx)：「自学材料」按钮 -> POST -> `{materials_md}` -> 弹窗（react-markdown 渲染，可滚动）。[api.ts](frontend/src/lib/api.ts) `generateStudyMaterials`。
3. 测试：POST materials（mock provider）-> 200 + materials_md + unit_count；无 provider->降级仍 200。
4. 验证：后端全量 + 前端 build。

## 稳定性/扩展性
- 复用 run_tool 修复链；降级 default_materials_from_plan（无 key/LLM 失败仍出材料）。
- markdown 经 react-markdown 渲染（默认不渲染原始 HTML，无 XSS）。
- 一次 LLM 调用（非每单元），简单稳定；max_tokens 调大（如 8192）容纳多单元。
- 扩展性：后续可切 per-unit LLM + DB 持久化（StudyUnit/StudyMaterial 表已就绪）+ WebSearch + StudyMaterialReader。
