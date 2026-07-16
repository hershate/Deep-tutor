# Deep-tutor

> AI 驱动的学习方案生成 Web 应用。多模态输入（文档 / 文本 / 网页 / 需求描述）→ 自动生成完备、详细、实用的个性化学习方案，并支持练习测验与进度跟踪。
>
> 本仓库当前处于 **Phase 1 MVP 实现阶段**。完整设计见 [`plan/`](./plan/)（11 份细化文档）。实施进度见 [`stage.md`](./stage.md)。

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 / SQLite / Alembic / pydantic v2 / anthropic SDK
- **前端**：React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + React Router + Zustand + react-markdown
- **部署**：docker-compose（nginx 托管前端 + uvicorn 后端 + 卷）

设计要点（借鉴 [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 三支柱）：缓存优先循环、工具调用修复、成本计量 + 预算守卫；两层执行模型（确定性编排器 + 内层工具调用循环）；Skill 作方法论指令源（skill-for-skills）。

## 目录结构

```
Deep-tutor/
├── plan/            # 实施计划文档（11 份）
├── stage.md         # 分阶段实施进度跟踪
├── backend/         # FastAPI 后端
│   ├── app/         # main / core / db / llm / agent / tools / skills / api
│   ├── alembic/     # 数据库迁移
│   └── requirements.txt
├── frontend/        # Vite + React 前端
├── skills/          # vendor 复用的 Skill（step 5 填充）
├── data/            # 运行时数据（gitignore，启动时自动创建）
├── docker-compose.yml
├── .env.example
└── README.md
```

## 快速开始（本地开发）

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env：至少填写 AUTH_TOKEN；生成功能需 ANTHROPIC_API_KEY；搜索需 BING_API_KEY
```

### 2. 后端

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows: source .venv/bin/activate (Linux)
pip install -r requirements.txt
cd .. && uvicorn backend.app.main:app --reload --app-dir backend
# 或：cd backend && uvicorn app.main:app --reload
```

健康检查：`curl -H "X-Auth-Token: <你的 AUTH_TOKEN>" http://localhost:8000/api/health`

### 3. 前端

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173 （/api 经 Vite 代理到 :8000）
```

## 一键部署（docker-compose）

```bash
cp .env.example .env  # 填写
docker compose up --build
# 前端：http://localhost:8080 （nginx 反代 /api -> backend:8000）
```

## 进度与计划

- 实施步骤与进度：[`stage.md`](./stage.md)
- 完整设计：[`plan/README.md`](./plan/README.md)
