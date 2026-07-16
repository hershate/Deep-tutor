# Deep-tutor 实施方案

> 本目录为 Deep-tutor 项目的实施计划文档。计划已细化到数据模型字段、工具契约、API 端点、前端页面、MVP 任务清单与风险表。

## 文档索引

| 文件 | 内容 |
|---|---|
| [01-architecture.md](./01-architecture.md) | 系统架构、Agent 循环（缓存优先/工具调用修复/成本计量）、数据模型（表与字段） |
| [02-tools.md](./02-tools.md) | 自研工具契约（输入/输出/错误/LLM/幂等）与契约一致性 |
| [03-api-frontend.md](./03-api-frontend.md) | REST/SSE API 设计、前端页面与组件 |
| [04-mvp-risk.md](./04-mvp-risk.md) | MVP 任务分解（P0/P1）、路线图、风险登记表 |
| [05-skills.md](./05-skills.md) | Skill 层设计：skill-for-skills 构建的 AI 工作流 Skill、与 Python 运行时结合、映射与加载机制 |
| [06-frontend.md](./06-frontend.md) | 前端 UI 细化：设计系统、页面线框、组件规格、状态管理、SSE、用户流程、无障碍 |

---

## 一、项目定位

Deep-tutor 是一个 **AI 驱动的学习方案生成 Web 应用**。用户通过多模态输入（上传文档 / 粘贴文本 / 网页链接 / 图片 / 音视频 / 纯需求描述），系统自动生成 **完备、详细、实用** 的个性化学习方案，并支持学习过程中的练习测验与进度跟踪。

- **部署形态**：本地优先（单机可用），架构可扩展至多用户云端部署。
- **AI 后端**：以 Anthropic Claude 为主，设 Provider 抽象层便于后续兼容 DeepSeek 等。
- **工具体系**：自研 Agent 循环与工具集，借鉴 [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 的三个架构支柱（缓存优先循环、工具调用修复、成本控制）。
- **Skill 层**：方法论密集的 AI 工作流用 skill-for-skills 编写为标准 Claude Code Skill，作为 LLM 指令源；Python 运行时加载并驱动对应工具，Skill 也可在 Claude Code 独立复用（详见 [05-skills.md](./05-skills.md)）。

## 二、对 Reasonix 的借鉴与差异

借鉴理念（自研实现，非直接使用）：
- **缓存优先循环（Pillar 1）**：保持 prompt prefix 字节稳定，长会话低成本。
- **工具调用修复（Pillar 2）**：校验/修复模型产出的工具调用，提升鲁棒性。
- **成本控制（Pillar 3）**：token/费用计量与预算上限。
- **可扩展机制**：Skills / Memory / Hooks 预留。

差异：
| 维度 | Reasonix | Deep-tutor |
|---|---|---|
| 后端 LLM | DeepSeek-only | Anthropic Claude（Provider 抽象，可扩展） |
| 领域 | 编程 Agent | 学习方案生成 |
| 形态 | 终端 CLI | Web 应用 |
| 工具 | 文件/Shell/编辑 | 解析/规划/检索/出题/进度 |

## 三、交互形式全集（不拘泥于形式，尽可能多）

### 输入方式（★ 为 MVP 范围）
| # | 方式 | 说明 | 阶段 |
|---|---|---|---|
| 1 | ★ 文件上传 | PDF / DOCX / PPTX / TXT / MD / EPUB | P1 |
| 2 | ★ 文本粘贴 | 直接贴大段文本 | P1 |
| 3 | ★ 网页链接 | 抓取正文 | P1 |
| 4 | ★ 纯需求描述 | "我想学 X" | P1 |
| 5 | ★ 对话式澄清 | 追问基础/目标/时长 | P1 |
| 6 | 图片上传（OCR） | 扫描件/截图 | P3 |
| 7 | 音频转写 | faster-whisper | P3 |
| 8 | 视频转写 | 提取音轨转写 | P3 |
| 9 | 批量导入 | 多文件合并为一个资料集 | P2 |
| 10 | 浏览器插件/剪藏 | 选中文本发送 | P4 |
| 11 | 邮件转发 | 转发邮件正文为资料 | P4 |

### 输出形式
| # | 形式 | 阶段 |
|---|---|---|
| 1 | ★ Web 结构化方案视图（可折叠大纲 / 时间线 / 资源卡） | P1 |
| 2 | ★ Markdown 导出 | P1 |
| 3 | ★ PDF 导出 | P1 |
| 4 | 日历文件（.ics） | P5 |
| 5 | Anki 卡片包（基于知识点） | P5 |
| 6 | 进度看板（Kanban / 打卡日历） | P2 |
| 7 | PPT 风格网页演示（HTML 幻灯片，复用 ppt-slide-builder） | P1 |

## 四、完整学习方案数据结构（plan schema）

```jsonc
Plan {
  id, session_id, title, topic, created_at, updated_at,
  user_profile { current_level, goal, daily_minutes, deadline? },
  knowledge_map {
    nodes: [ KnowledgeNode { id, name, difficulty(1-5), must_learn, parent_id } ],
    edges: [ { from, to, relation } ]
  },
  stages: [ Stage {
    index, name, goal, difficulty(1-5), estimated_weeks,
    topics: [ { name, why, must_learn } ],
    resources: [ { title, url, type, verified: ✅|⚠️|❌, note } ],
    exercises: [ { desc, est_hours } ],
    milestone
  } ],
  schedule: [ { date, stage_index, tasks, est_minutes } ],
  quizzes: [ Quiz { node_id, type, question, options?, answer, explanation } ],
  validation_report {
    dependency_closure, difficulty_step, cognitive_load,
    milestone_feasibility, gap_analysis, passed, fixes[]
  },
  metadata { model, total_tokens, cost, duration_ms }
}
```

## 五、路线图

| 阶段 | 目标 |
|---|---|
| **P1 MVP** | 资料/需求 -> 完整学习方案主线（大纲+路径+时间+资源+校验） |
| P2 | 练习/测验生成 + 进度跟踪 + 掌握度评估 + 批量导入 |
| P3 | 图片 OCR + 音视频转写 |
| P4 | 多用户 + 云部署 + Skills/MCP 扩展 + 浏览器插件 |
| P5 | 缓存深度优化 + Anki/ics 导出 + 协作分享 |

## 六、关键假设（待用户确认）

1. LLM 以 **Anthropic Claude 为主**，Provider 抽象便于后续兼容 DeepSeek 等。
2. **本地单用户优先**，认证先做最简（简单 token），预留多用户（JWT）。
3. MVP 持久化用 **SQLite**（SQLAlchemy，可迁移 Postgres）。
4. API Key **本地配置**（`.env`），不入库。
5. MVP 解析范围：PDF / Word / Markdown / TXT / 网页（OCR 与音视频在 P3）。
6. 方法论密集的 AI 工作流以 skill-for-skills 生成的 Skill 承载（指令源），基础设施类工具保持纯 Python（详见 [05-skills.md](./05-skills.md)）。
