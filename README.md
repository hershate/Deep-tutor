# Deep-tutor

> AI 驱动的学习方案生成 Web 应用。多模态输入（文档 / 文本 / 网页 / 需求描述）→ 自动生成完备、详细、实用的个性化学习方案，并支持练习测验与进度跟踪。
>
> 本仓库当前处于 **Phase 1 MVP 实现阶段**。完整设计见 [`plan/`](./plan/)（11 份细化文档）。实施进度见 [`stage.md`](./stage.md)。

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 / SQLite / Alembic / pydantic v2 / anthropic SDK
- **前端**：React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + React Router + Zustand + react-markdown
- **部署**：本地直起脚本（`scripts/start.sh` / `start.ps1`，uvicorn 单进程托管前端 SPA + `/api`）

设计要点（借鉴 [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 三支柱）：缓存优先循环、工具调用修复、成本计量 + 预算守卫；两层执行模型（确定性编排器 + 内层工具调用循环）；Skill 作方法论指令源（skill-for-skills）。

## 目录结构

```
Deep-tutor/
├── plan/            # 实施计划文档（11 份）
├── stage.md         # 分阶段实施进度跟踪
├── API.md           # 插件 API 权威契约（前端 + 后端，双版本线）
├── backend/         # FastAPI 后端
│   ├── app/         # main / core / db / llm / agent / tools / skills / plugins / api
│   ├── alembic/     # 数据库迁移
│   └── requirements.txt
├── frontend/        # Vite + React 前端
├── plugins/         # 后端插件目录（自动发现装载；约定与示例见其 README）
├── skills/          # vendor 复用的 Skill（step 5 填充）
├── data/            # 运行时数据（gitignore，启动时自动创建）
├── scripts/         # 本地直起脚本（start.sh / start.ps1）
├── .env.example
└── README.md
```

## 快速开始（本地开发）

### 1. 配置环境变量（可选）

> **零配置开箱即用（iter 89）**：不配任何参数也能启动——后端以「开放本机模式」运行，
> 首次打开浏览器会进入**首次设置向导**（设置访问密码 / 配置 AI API Key，均可跳过，
> 之后可在「设置」页补配）。需要多用户、固定 JWT 密钥等高级配置时再建 `.env`：

```bash
cp .env.example .env   # 可选。所有字段均有默认值或可在界面内配置
```

> ⚠️ **开放模式安全提示**：零配置下免登录运行，且服务绑定 `0.0.0.0`——**同一局域网内的任何人都能访问本应用**（查看/修改你的数据）。请在首次设置向导或「设置」页设置访问密码；设密码后开放模式自动关闭。

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

## 部署（本地直起，本机/局域网）

单个 Python 进程托管前端 SPA + `/api`（默认 `:41173`），无需 Docker/nginx：

```bash
# Linux / macOS
bash scripts/start.sh

# Windows（PowerShell）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

脚本自动完成：建 `backend/.venv` → 装依赖 → 无 `.env` 时以零配置模式继续（不再退出）→ 无 `frontend/dist` 且有 npm 时自动构建前端 → 数据库迁移 → 启动并自动打开浏览器（`NO_BROWSER=1` 关闭）。再次运行幂等秒过。端口覆盖（默认 41173——8000 常落入 Windows Hyper-V/winNAT 排除端口段致 bind 失败）：`bash scripts/start.sh --port 9000`（PowerShell：`scripts\start.ps1 -Port 9000`；亦可用 `PORT` 环境变量，参数优先）。

依赖：Python 3.11+（必需）、Node.js 20+（仅首次构建前端需要）。启动后访问 `http://localhost:41173`（局域网 `http://<本机IP>:41173`）。**无 `.env` 首次进入会自动跳到首次设置向导**：设置访问密码与 AI API Key 均可在向导或之后的「设置」页完成，无需手改任何配置文件。

### 配置（.env，全部可选）

| 变量 | 说明 |
| --- | --- |
| `JWT_SECRET` | JWT 签名密钥。留空则自动生成随机密钥并持久化到 `data/.jwt_secret`（重启不失效；多实例部署需显式设置同一密钥）。生成：`python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | 多用户场景建议。管理员初始密码，启动时自动创建/收敛 `ADMIN_USERNAME`（默认 `admin`）账号；也可在首次设置向导/设置页直接设置（忘记密码时配此项重启可强制收敛重置） |
| `AUTH_TOKEN` | 简单 token 回退（与 JWT 双轨；API/浏览器扩展可用它认证） |
| `DATABASE_URL` | 可选。完整 SQLAlchemy URL（如切换 PostgreSQL）；留空则使用 `DB_PATH` 的 SQLite（默认 `data/deep-tutor.db`） |
| `ANTHROPIC_API_KEY` | 生成功能必需（也可在首次设置向导或 Web 设置页配置供应商档案，无需写入 .env） |
| `BING_API_KEY` | 资源搜索可选 |

> **安全提示（认证设计权衡）**：前端 JWT 明文存于 localStorage，且 `VITE_AUTH_TOKEN`（若设置）会打包进客户端 bundle——两者都假定本机/受信局域网部署。多用户或公网部署请勿设置 `VITE_AUTH_TOKEN`（走登录页 JWT），并在公网前加 HTTPS 反代。

启动时自动执行数据库迁移（`app.db.migrate`）：新库建表、旧库增量迁移；历史 `create_all` 时期的无版本库会按 schema 特征探测代际后接续迁移，无需手工处理。

首次使用：浏览器打开 → 无 `.env` 时进入首次设置向导（可跳过）→ 已配 `ADMIN_PASSWORD` 时跳转登录页，用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录 → 设置页可配置供应商档案与创建其他用户。

### 升级

```bash
git pull
bash scripts/start.sh   # 依赖/前端/迁移自动增量处理（Windows 用 start.ps1）
```

### 备份

数据全部在 `data/`（SQLite + 上传/导出文件），停机后整体打包即可：

```bash
tar czf dt-backup-$(date +%Y%m%d).tgz data/        # Linux/macOS
# Windows PowerShell: Compress-Archive data dt-backup-$(Get-Date -f yyyyMMdd).zip
```

### 可选项

- **OCR / 音视频转写**：默认未装依赖（R9 降级设计）。上传图片 OCR 需安装 `tesseract`（+ 中文语言包）与 `pip install pytesseract`，音视频转写需 `pip install faster-whisper`（装进 `backend/.venv`）；缺失时对应入口报 `dependency_missing`，其余功能不受影响。
- **HTTPS**：本机/局域网部署不需要；若暴露公网，请在前面加一层反代（Caddy / Nginx Proxy Manager 等）终止 TLS 并转发到 `:41173`。

## 插件开发

Deep-tutor 提供前后端两条插件 API 版本线（各自语义化版本、只增不改；**当前版本号以 [API.md](./API.md) 文首版本线表为准**，本文件不钉版本防过期）：

- **前端插件**（`window.DeepTutor`）：浏览器端 JS 插件——小组件插槽（首页仪表盘/侧栏底部/方案详情）、皮肤令牌、API 事件、REST 代理（`api.get` / `api.request`）、插件 URL 持久化加载。侧栏 🧩「插件」页可视化加载/移除（免控制台）；也可控制台试：`await DeepTutor.plugins.load('https://…/plugin.js')`。
- **后端插件**（`plugins/` 目录）：Python 插件自动发现装载——REST 路由命名空间 `/api/plugins/<id>/`（默认带宿主认证）、核心事件订阅（方案/材料/进度/测验/打卡/启动关闭）、Agent 工具注册。把 `.py` 文件放进 `plugins/` 重启即生效，`_` 前缀即停用；可启用示例见 [plugins/example_hello.py.disabled](./plugins/example_hello.py.disabled)，装载约定见 [plugins/README.md](./plugins/README.md)。

## 进度与计划

- 实施步骤与进度：[`stage.md`](./stage.md)
- 完整设计：[`plan/README.md`](./plan/README.md)
