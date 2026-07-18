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

## 部署（docker-compose，本机/局域网）

架构：仅 `frontend`（nginx，`:8080`）对外暴露端口，`backend`（uvicorn）只在内部网络；SQLite 与上传/导出文件持久化在命名卷 `deep-tutor-data`；`skills/` 只读挂载。

### 1. 配置

```bash
cp .env.example .env
```

`.env` 必填/建议项：

| 变量 | 说明 |
| --- | --- |
| `JWT_SECRET` | **必填**（compose 硬校验）。JWT 签名密钥，留空则重启后全员登录态失效。生成：`python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | 多用户必填。管理员初始密码，启动时自动创建/收敛 `ADMIN_USERNAME`（默认 `admin`）账号 |
| `AUTH_TOKEN` | 简单 token 回退（与 JWT 双轨；API/浏览器扩展可用它认证） |
| `ANTHROPIC_API_KEY` | 生成功能必需（也可在 Web 设置页配置供应商档案） |
| `BING_API_KEY` | 资源搜索可选 |

### 2. 启动

```bash
docker compose up -d --build
# 前端：http://localhost:8080 （局域网访问 http://<主机IP>:8080）
```

容器启动时自动执行数据库迁移（`app.db.migrate`）：新库建表、旧库增量迁移；历史 `create_all` 时期的无版本库会按 schema 特征探测代际后接续迁移，无需手工处理。

首次使用：浏览器打开 → 跳转登录页 → 用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录 → 设置页配置供应商档案与创建其他用户。

### 3. 升级

```bash
git pull
docker compose up -d --build   # 启动迁移自动应用新增 schema 变更
```

### 4. 备份

数据全部在命名卷内（SQLite + 文件），停机备份：

```bash
docker compose down
docker run --rm -v deep-tutor_deep-tutor-data:/data -v "%cd%":/backup alpine \
  sh -c "cd /data && tar czf /backup/dt-backup-$(date +%Y%m%d).tgz ."
docker compose up -d
```

（Linux/Mac 将 `%cd%` 换为 `$(pwd)`、`$(date ...)` 用法相同。）

### 5. 可选项

- **OCR / 音视频转写**：镜像默认不含（保持精简）。上传图片 OCR 需 `tesseract-ocr`（+ 中文包），音视频转写需 `pip install faster-whisper`，按需加到 `backend/Dockerfile`；缺失时对应入口报 `dependency_missing`，其余功能不受影响。
- **HTTPS**：本机/局域网部署不需要；若暴露公网，请在前面加一层反代（Caddy / Nginx Proxy Manager 等）终止 TLS 并转发到 `:8080`。

## 进度与计划

- 实施步骤与进度：[`stage.md`](./stage.md)
- 完整设计：[`plan/README.md`](./plan/README.md)
