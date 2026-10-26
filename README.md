# Deep-tutor

> **版本**：v0.1.0.0（Python 实现）· 改造形态 v2 进行中
>
> 本地优先的单机 AI 学习助手。输入学习目标与资料，产出结构化学习方案，并覆盖做题、间隔复习、自学讲义、笔记、知识库与 AI 对话的学习闭环。
>
> **改造公告（2026-10-15 起）**：项目正向 **Reasonix 内核（Go）架构改造**——单二进制多用户服务 + 四前端（Web / Deep-tutor Studio / CLI / TUI / ACP），接入 Reasonix 内核能力（工具审批、Rewind、子代理、MCP、插件包、技能、双模型）。
> 当前可用实现仍为 **Python v0.1.x**（`backend/` + `frontend/`，端口 41173），保留至改造阶段① M5 验收后停用，阶段④删除。
> 改造决策（61 项）见 [`note/12`](./note/12-Reasonix改造决策.md)，方案见 [`plan/reasonix/`](./plan/README.md)，进度见 [`stage.md`](./stage.md)，实施分支 `refactor/reasonix`。工程规约见 [`DEEPTUTOR.md`](./DEEPTUTOR.md)。上游来源与引用声明见 [`NOTICE`](./NOTICE)。

> 内核与桌面端吸收自 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)（studio 分支，2026-10 快照），遵循 MIT License。许可全文见 [`LICENSE-REASONIX`](./LICENSE-REASONIX)。
>
> 出处、路径映射、已移除资产与修改披露见根 [`NOTICE`](./NOTICE)。裁剪与修改清单随实施进度记入 [`stage.md`](./stage.md)，并入 [`DEEPTUTOR.md`](./DEEPTUTOR.md)。

---

## 1. 能力范围

### 1.1 资料与需求转学习方案

- **输入**：文件上传（PDF / DOCX / PPTX / TXT / MD / EPUB，可批量）、粘贴文字、网页链接、纯需求描述；图片走 OCR、音视频走转写（需额外依赖，见 §5.3）。
- **上传分三段**：暂存 → 确认 → 解析，材料是否入库、是否解析、解析质量高低由使用方逐项决定。
- **意图澄清**：信息不足时生成追问（当前水平、每日可用时长等），回答后继续。
- **生成过程实时可见**：SSE 推送工具级进度，断线可续接，任务可取消。
- **局部重生成**：阶段设计 / 资源 / 排程等区段可带反馈重跑，方案保留历史版本并支持结构化对比。
- **日程时间线**：按周分组逐日展示，自动高亮当日与下一项任务。

### 1.2 学习闭环

- **练习测验**：按知识点出题（选择 / 填空 / 简答 / 开放题），服务端判分，错题可重练。
- **进度跟踪**：逐知识点维护掌握度，到期复习提醒。
- **间隔复习**：到期知识点与错题合并为翻卡自评队列（忘了 / 模糊 / 记住了），按掌握度排定复习间隔。
- **自学讲义**：逐单元生成 Markdown 学习材料，阅读器支持字号调节、目录导航、内容检索、单篇下载。
- **打卡与统计**：打卡（含补卡）、连续天数、打卡热力图、学习时长分布、番茄钟专注计时。

### 1.3 笔记与知识库

- **知识库**：文档直接上传（不依赖学习方案），可搜索、按类型筛选，原件与解析文本对照查看；支持基于文档问答并给出引用出处，问答记录入库。
- **独立笔记**：标题 + Markdown 正文，编辑器带格式工具栏，支持 `.md` 导出与 ZIP 批量导入导出。
- **全局搜索**：顶栏输入即出建议，聚合方案 / 笔记 / 知识库 / 知识点四域。

### 1.4 AI 对话

- 会话历史持久化，AI 可显式存取记忆条目（记忆仅按需召回，不自动注入每轮上下文）。
- 对话内可执行联网搜索、网页抓取、工作区文件读写，并将草稿提升为正式方案或笔记（需确认）。

### 1.5 导出与分享

- 方案导出：Markdown / PDF / PPT 风格网页幻灯片 / 日历（.ics）/ Anki 卡片包（.apkg）。
- 只读分享链接（免登录访问），设置页统一管理与撤销。
- 全量数据备份导出 / 导入，导入前提供预检摘要。

---

## 2. 已知限制

### 2.1 安全

- **面向本机 / 受信任局域网设计**，未做公网加固。暴露到公网须前置 HTTPS 反向代理（Caddy / Nginx）并严格配置认证。
- **零配置首次启动进入开放模式**：未设密码时免登录运行并监听全部网卡，同一局域网内任意设备可访问数据。设置访问密码后开放模式自动关闭。
- **认证存在已知权衡**：浏览器登录凭证（JWT）以明文存于 localStorage；配置 `VITE_AUTH_TOKEN` 时该值被打包进前端产物。两者均以本机或受信任局域网为前提，不适用于公网多用户场景。

### 2.2 费用

- 生成功能调用付费 AI 接口，按人民币 ¥ 计价。内置会话级 / 每日预算守卫，作用于多数路径但不能杜绝异常支出，需关注用量页趋势。

### 2.3 功能完整度

- **高级输入需额外依赖**：图片 OCR（tesseract）、音视频转写（faster-whisper）、PDF 导出（weasyprint）默认未安装，缺失时对应入口返回 `dependency_missing`，其余功能不受影响。
- **PDF 深度解析栈默认不装**：marker 那套（约 2GB）在慢网络下会卡住启动，故默认只装服务栈，PDF 走 pypdf 快速档；要装见 §4.1 的 `INSTALL_OCR_STACK=1`。
- **记忆非自动召回**：AI 仅在需要时读取已记要点，属设计取舍。
- **幻灯片为 HTML 网页**，非 PowerPoint 原生文件。
- **存储为单机 SQLite 文件**，适合个人使用，未针对高并发多用户设计（PostgreSQL 迁移未实现）。

### 2.4 成熟度

- 个人性质项目，无服务等级保证。代码经 2026-07-18 全项目自审（87 项，修复记录见 [`docs/archive/err.md`](./docs/archive/err.md)），未发现的问题仍然可能存在，重要数据须自行备份。
- 界面信息密度较高，键盘快捷键（`?` 查看全表）可缓解初次上手成本。
- 文档以中文为主，无完整英文文档。
- 资源检索依赖 Bing 搜索、生成能力依赖第三方模型；第三方模型的可用性、速率限制与内容政策直接影响本工具。

### 2.5 已知小项

- 开发服务器依赖的 `esbuild` 存在一项安全告警，仅影响本地 `npm run dev`，不影响生产构建产物（记录见 [`docs/archive/err.md`](./docs/archive/err.md) E6）。
- 前端未引入 ESLint，CI 已强制 `tsc` 类型检查。

---

## 3. 适用场景

**适用**：自学方向明确但缺路线图；资料分散需要结构化学习路径；要求数据自持、不上传至不受信任的云服务；愿意自行配置 API Key 并承担相应费用。

**不适用**：要求零配置零成本的成品软件；公网陌生用户大规模服务；不接受任何命令行或配置操作。

---

## 4. 启动

### 4.1 一键脚本

前置：**Python 3.11+**（必需）、**Node.js 20+**（仅首次构建前端需要）、**[uv](https://docs.astral.sh/uv/)**（虚拟环境用它建；没有也能跑，脚本会回退到标准库 `venv` + pip）。

```bash
# Windows（PowerShell）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# Linux / macOS
bash scripts/start.sh
```

脚本依次完成：建虚拟环境 → 装依赖 → 构建前端 → 数据库迁移 → 启动服务 → 打开浏览器。重复执行跳过已完成步骤。

**虚拟环境用 uv 建（都在项目范围内）**：脚本先找 **本机 F 盘**那份 uv（`F:\uv\bin\uv.exe`），再退到 PATH 上的 uv——PATH 上可能是别的机器留下的旧版或占位入口，每个候选都要实跑一次 `--version` 才算数。两者都没有才回退到标准库 `venv` + pip（并打印提示）。

环境建在项目内的 `backend\.venv`，uv 的包缓存放在项目内的 `.uv-cache`（跟着仓库走，与 venv 同卷 → 硬链接可用，不占系统盘、不写用户目录）。`uv venv --seed` 会把 pip 一并装进环境——uv 不可用时 OCR 栈那段要靠 pip 兜底。

**想强制不用 uv**：设 `SKIP_UV=1`（既不找 uv，也不重建已有的 venv）。**想指定用哪份 uv**：设 `DEEPTUTOR_UV=<uv 可执行文件路径>`。**想指定包索引**：设 `UV_DEFAULT_INDEX`（见下）。

> **网络慢时**：官方 PyPI 从部分国内网络只有 ~65 KB/s，国内镜像能到 ~4 MB/s（差 60 倍，实测）。uv 与 pip 都认 `UV_DEFAULT_INDEX` / `PIP_INDEX_URL`，指到镜像即可。

**换机后不用手动清环境**：脚本判定「虚拟环境可用」的标准是**解释器真的能跑起来**，不是「文件在不在」。

venv 里的解释器指向的是**当初装它的那台机器**的基础解释器——换机 / 重装 Python 后那个路径可能已经没了，此时脚本会清掉这个空壳 venv 并重建（旧机的 venv 直接复制过来几乎必然踩这一条：文件在，pip 报 `No Python at ...`）。

同理，脚本会**逐个实跑** PATH 上的 `python` / `py`，取第一个能执行且版本 ≥ 3.11 的（Windows 的「应用执行别名」占位入口会被跳过）。

依赖分两栈装：`backend/requirements.txt` 是服务栈（**自身可解析**，默认装）；`backend/requirements-ocr.txt` 是 OCR/PDF 深度解析栈（marker，约 2GB，**默认不装**——要装得显式说，见下）。

服务栈用 uv 装（`uv pip install`，实测走镜像全程 10.4s：解析 2.3s + 准备 5.7s + 安装 0.9s）；uv 失败（网络、索引、解析器差异）会自动回退 pip 再试一次——服务栈装不上就永远起不来。OCR 栈若显式要求装，同样 uv 优先、pip 兜底。

两者必须分开——marker 钉 `Pillow<11`，服务进程须 `Pillow>=13.0`（直接解码用户上传图片；12.3.0 仍带 28 条 OSV 公告，13.0.0 是零公告版），写进同一个文件 pip 会判为无解（ResolutionImpossible），**装不上服务就起不来**。

OCR 栈**默认不装**：它是可选的重量级栈——`requirements-ocr.txt` 只有一行 `marker-pdf==1.10.2`，但它会拖进 torch / surya-ocr / transformers，首次约 2GB；慢网络下这一步足以把「启动」卡住几小时。默认启动**只装服务栈**：PDF 走 pypdf 快速档，full 档按 R9 报 `dependency_missing`。

**需要 PDF 深度解析时显式要求安装**：

```bash
# Windows（PowerShell）
$env:INSTALL_OCR_STACK='1'; powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# Linux / macOS
INSTALL_OCR_STACK=1 bash scripts/start.sh
```

`SKIP_OCR_STACK=1` 保留为兼容项（与默认同义；两个变量同时给时以 `SKIP` 为准）。**已经装好的 marker 不会被卸载**——默认路径只跳过安装动作，并提示「环境里已有 marker，未动它」。

服务地址 `http://localhost:41173`，局域网内其他设备使用 `http://<本机IP>:41173`。首次访问进入设置向导（访问密码与 AI API Key 均可跳过，后续补充）；未配置 API Key 时应用可正常浏览数据，生成类功能不可用。

**未设访问密码时服务以开放模式运行并监听全部网卡**（见 §2.1）：同一局域网内任意设备可免登录访问数据。设一个访问密码即可关闭开放模式（设置页或 `.env` 的 `ADMIN_PASSWORD`）；`AUTH_TOKEN` 必须是 `.env.example` 之外的真实值，模板占位串 `change-me-please` 不被接受为凭证。

**启动脚本会在这种配置下打印一条提醒**：`.env` 里 `ADMIN_PASSWORD` 与 `AUTH_TOKEN` **都没有**真实值才算「没配凭证」（模板占位串 `change-me-please` 不算；任意一项有效即视为已配置）。提醒只依据 `.env`——库中是否已有账号设过密码，脚本看不到，所以那句话说的是「若库里也没有账号设过密码」，不是断言。

端口改写：PowerShell 用 `-Port 9000`，或设置 `PORT` 环境变量。默认端口取 41173 而非 8000，因 8000 常落入 Windows 系统保留端口段导致绑定失败。

**端口被占用时会提前退出**：脚本在开头就查一遍端口（Windows 用 `Get-NetTCPConnection`，POSIX 用 connect 探针），已被占用就直接报「端口 41173 已被占用……占用者 pid：xxxx」并以非零码退出——不会走到装依赖、跑迁移之后才让 uvicorn 报 `address already in use`。换端口即可（`-Port` / `--port`）。

### 4.2 手动开发启动

后端：

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Linux: source .venv/bin/activate
pip install -r requirements.txt
# 需要 PDF 深度解析（marker）时另装（与上面的 Pillow 下限互斥，必须分开）：
# pip install -r requirements-ocr.txt
uvicorn app.main:app --reload   # dev 默认 :8000
```

健康检查：`curl -H "X-Auth-Token: <AUTH_TOKEN>" http://localhost:8000/api/health`

前端：

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173，/api 经 Vite 代理到 :8000
# 后端在其他端口时：VITE_API_PROXY_TARGET=http://localhost:41173 npm run dev
```

---

## 5. 配置

### 5.1 `.env`（全部可选）

```bash
cp .env.example .env
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 空 | 生成功能所需。也可在设置向导 / 设置页以供应商档案或 Anthropic 回退 Key 档案配置，档案优先于 `.env` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | 默认模型。当前推荐 `deepseek-v4-flash`（需在设置页配置指向 DeepSeek 兼容端点的供应商档案） |
| `AUTH_TOKEN` | 空 | 简单令牌回退，与 JWT 双轨；始终有效，用于个人访问令牌失效时防锁死 |
| `ADMIN_USERNAME` | `admin` | 管理员登录名，启动时自动创建 / 收敛 |
| `ADMIN_PASSWORD` | 空 | 管理员初始密码；配置后启动时设置或重置该账号密码 |
| `JWT_SECRET` | 空 | 留空则生成随机密钥并持久化到 `data/.jwt_secret`（重启不失效）；多实例部署须显式设为同一值 |
| `JWT_EXPIRE_DAYS` | `7` | JWT 有效期（天） |
| `DB_PATH` | `data/deep-tutor.db` | SQLite 路径 |
| `DATABASE_URL` | 空 | 完整 SQLAlchemy URL，配置后优先于 `DB_PATH` |
| `DATA_DIR` / `SKILLS_DIR` / `PLUGINS_DIR` / `FRONTEND_DIST` | `data` / `skills` / `plugins` / 空 | 存储、方法论、插件目录与前端产物目录 |
| `MAX_UPLOAD_MB` | `50` | 单文件上传上限 |
| `MAX_TOOL_CALLS` | `20` | 单会话工具调用轮次上限 |
| `MAX_REGENERATIONS` | `2` | 方案重生成上限 |
| `LLM_MAX_TOKENS` | `100000` | 单次 LLM 输出上限；可在设置页按用户调低 |
| `DB_BUSY_TIMEOUT_MS` / `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | `10000` / `20` / `80` / `30` | SQLite 写争用等待与连接池参数；并发 SSE 会占用连接，需按用户数上调 |
| `MAX_CONCURRENT_GENERATIONS` / `..._PER_USER` | `8` / `1` | 方案生成并发配额（超额 503） |
| `MAX_CONCURRENT_CHAT_RUNS` / `MAX_RUNNING_CHAT_PER_THREAD` | `8` / `1` | 对话 run 并发配额 |
| `MAX_CONCURRENT_LLM_CALLS` | `16` | 全局 LLM 调用背压信号量，0 表示不限 |
| `MAX_PLUGIN_EVENT_THREADS` | `32` | 插件事件后台线程上限 |
| `MAX_CONCURRENT_WEBHOOK_DELIVERIES` | `16` | 插件 Webhook 出站并发上限 |
| `MAX_CONCURRENT_SEARCH_FETCHES` | `2` | 搜索页面抓取并发上限 |
| `BUDGET_PER_SESSION_USD` / `DAILY_BUDGET_USD` | `1.0` / `10` | 会话与日预算；字段名为历史遗留，计费按设置页模型计费表以 ¥ 计价 |
| `API_USAGE_RETENTION_DAYS` | `180` | 用量台账保留天数，0 表示永久 |
| `MAINTENANCE_INTERVAL_SECONDS` / `ORPHAN_GRACE_SECONDS` | `86400` / `86400` | 定期运维节奏与孤儿目录冷却 |
| `SHARE_DEFAULT_EXPIRE_DAYS` | `90` | 分享链接默认有效期，0 表示永久 |
| `SEARCH_ENGINE` | `auto` | `auto`（有 Key 用付费 API，无 Key 用免 Key 网页版）/ `bing` / `bing_web` |
| `BING_API_KEY` | 空 | Bing 搜索 Key；亦可在设置页建档案切换 |
| `OCR_LANGUAGES` / `WHISPER_MODEL` / `MEDIA_MAX_MINUTES` | `eng+chi_sim` / `base` / `30` | OCR 语言包、转写模型与转写时长上限 |
| `AGENT_RUNS_ENABLED` | `false` | 方案生成旁挂 run 记录 |
| `CORS_ORIGINS` | `http://localhost:5173` | 允许的跨域来源 |
| `LOG_LEVEL` | `info` | 日志级别 |

模板占位值（`change-me-please`、`sk-ant-xxxx`）被识别为未配置。

启动时自动执行迁移：新库建表、旧库增量升级、历史 `create_all` 时期的无版本库按 schema 特征探测代际后接续迁移。

### 5.2 部署 / 升级 / 备份

本地部署为单进程托管前端 SPA 与 `/api`，无需 Docker 或反向代理：

```bash
bash scripts/start.sh          # Linux / macOS
# Windows: powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

升级：

```bash
git pull
bash scripts/start.sh   # 依赖 / 前端 / 迁移增量处理
```

数据全部位于 `data/`（SQLite + 上传与导出文件）：

```bash
tar czf dt-backup-$(date +%Y%m%d).tgz data/        # Linux/macOS
# Windows: Compress-Archive data dt-backup-$(Get-Date -f yyyyMMdd).zip
```

亦可在「设置 → 备份」导出 / 导入 JSON 备份（方案、进度、笔记、偏好等，导入前有预检摘要）。

### 5.3 可选依赖

| 能力 | 依赖 | 缺失时行为 |
|---|---|---|
| 图片 OCR | 系统安装 `tesseract` + 中文语言包；`pip install pytesseract Pillow` | 入口报 `dependency_missing` |
| 音视频转写 | `pip install faster-whisper`（首次使用自动下载模型） | 入口报 `dependency_missing` |
| PDF 深度解析（full 档） | `pip install -r backend/requirements-ocr.txt`（marker，约 2GB；默认不装，见 §4.1） | PDF 走 pypdf 快速档；full 档报 `dependency_missing` |
| PDF 导出 | `pip install weasyprint`（Windows 另需 GTK 运行时，Linux 需 pango） | 降级为 Markdown 并附提示 |
| HTTPS | 反向代理（Caddy / Nginx Proxy Manager 等） | 不适用本机 / 局域网部署 |

---

## 6. 浏览器扩展

[`extension/`](./extension/) 为 Manifest V3 扩展：网页选中文本或整页链接一键剪藏为会话资料（`POST /api/materials/clip`），供 Web 端直接生成方案。装载与配置见 [README](./extension/README.md)。

---

## 7. 插件开发

> **改造期状态（note/12 决策 #6/#25/#59）**：以下两条插件版本线属 **v1（Python 版）**。改造后前端插件宿主 `window.DeepTutor` 废弃（能力由 Reasonix 插件包与 Extension 结构化 UI 承担）；后端 Python 进程内插件体系终结（9 个内置插件实例的功能在新架构等价实现，映射表见 `plan/reasonix/05` 阶段③）；前端插件示例的番茄钟 / 浮层笔记 / 换肤 / 阅读器四项作为内置能力在新前端重建。v1 运行期间继续可用。

- **前端插件**（`window.DeepTutor`，当前 `1.19.0`，见 `frontend/src/lib/plugins.ts`）：小组件插槽、皮肤令牌、API 事件、REST 代理、热启停、插件 URL 持久化加载。侧栏「插件」页可视化加载 / 移除 / 启停。契约见 [`API.md`](./API.md)。
- **后端插件**（[`plugins/`](./plugins/) 目录，当前 `1.20.0`）：Python 插件自动发现装载，路由命名空间 `/api/plugins/<id>/`（默认带宿主认证），可注册工具、解析器、导出器、供应商协议、MCP 桥、事件订阅、定时任务与 Webhook。`.py` 文件放入 `plugins/` 重启生效，`_` 前缀停用。装载约定见 [plugins/README.md](./plugins/README.md)。

---

## 8. 工程结构

### 8.1 目录

```text
Deep-tutor/
├── ── 改造工程与文档（本仓自有）──
├── plan/            # 改造方案（plan/reasonix/ 六份）；Python 时代计划已归档 docs/archive/plan-v1/
├── note/            # 项目分析笔记（01–11）+ 改造决策档案（12）+ graft/ 上游嫁接一手档案
├── stage.md         # 实施进度跟踪（活文件，持续追加）
├── API.md           # v1 插件 API 契约（改造期 = shim 兼容层验收基准）
├── NOTICE           # 第三方作品引用与许可声明（Reasonix 吸收权威声明）
├── AGENTS.md        # 仓库工作指引（AI agent 与人类协作者）
├── docs/archive/    # Python 时代归档（plan-v1 12 份 / chat.md / err.md 审计清单）
├── ── Reasonix 内核（Go，吸收自上游，见 NOTICE）──
├── internal/        # 内核实现（base/contract/safety/state/model/platform/tools/ext/runtime/session/assembly + runtime/tutor=Deep-tutor 学习域）
├── cmd/             # 命令行入口（reasonix / reasonix-studio-host / reasonix-plugin-example / extension-protocol-gen / studio-delta）
├── tools/           # 工程门禁（repolint / covergate / pricecheck 等）
├── sdk/             # Go SDK
├── benchmark/       # 上游 Go 基准测试包
├── desktop/         # Electron 宿主 + frontend-next 统一前端（阶段④前端基座）
├── docs/kernel/     # 内核工程文档（上游原样吸收，溯源与已移除资产说明见 docs/kernel/README.md；upstream/ = 与本仓根冲突的上游原件存档）
├── scripts/reasonix/# 上游工具脚本
├── go.mod 等        # 根级构建基座（Makefile/golangci/goreleaser/.githooks 等）
├── ── Python v1（当前可用实现，41173 端口；阶段④退役）──
├── backend/         # FastAPI 后端（app / alembic / tests，1462 个 pytest 用例）
├── frontend/        # Vite + React 前端（19 页，阶段①②作 shim 回归工具）
├── extension/       # 浏览器扩展（MV3 网页剪藏，阶段④重构）
├── plugins/         # 后端插件目录（9 个实例：8 个 .py + 1 个 .disabled）
├── plugin-examples/ # 前端插件示例（四项功能将在新前端重建）
├── skills/          # LLM 方法论 Skill（deep-tutor-* 6 个 + vendor 复用 2 个）
├── scripts/         # 本地直起脚本 start.sh / start.ps1（另含 reasonix/ 上游脚本）
├── ── 运行时与参考 ──
├── data/            # 运行时数据（gitignore，启动时自动创建）
├── REF/             # 吸收用上游源快照（gitignore，仅供对照）
├── .env.example     # Python v1 环境变量样例（内核配置见 reasonix.example.toml）
└── README.md
```

上游无关资产树（`release-notes/` `benchmarks/` `examples/` `workers/` `npm/` `output/`）已于 2026-10-20 移除，范围与理由见 [`docs/kernel/README.md`](./docs/kernel/README.md) 与 note/12 修订记录。

### 8.2 技术栈

- **后端（v1）**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 / SQLite（WAL）/ Alembic / pydantic v2 / pydantic-settings / anthropic SDK / pyjwt；PDF 解析 marker（高质量）+ pypdf（快速档）；可选 tesseract OCR / faster-whisper 转写；Anki 导出 genanki。
- **前端（v1）**：React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + React Router + Zustand + react-markdown + react-window + KaTeX。
- **AI 架构**：缓存优先循环、工具调用修复（校验 + 重试 ≤3 → 降级默认）、成本计量与预算守卫；两层执行模型（确定性编排器 + 内层工具循环）；Skill 作方法论指令源（[`skills/`](./skills/)）。
- **内核（v2 目标形态）**：Go 单二进制（Reasonix 内核）+ 多用户 SQL 域 + `/api` 兼容层 + 四前端。

### 8.3 测试与门禁

```bash
cd backend
.venv/Scripts/python -m pytest        # 1462 个用例
.venv/Scripts/python -m ruff check .  # lint（E4/E7/E9/F，行宽 120）
cd ../frontend && npm run build       # tsc 类型检查 + vite 构建
```

Go 侧门禁命令在 `Makefile`：

```bash
make lint     # golangci-lint + go run ./tools/repolint
make check    # repolint -changed（仅报告工作树变更）
make test     # 根模块 + desktop + sdk/go 三个模块的 go test
```

**CI 现状**：`.github/workflows/ci.yml` 已接入 **Go 内核门禁与桌面端前端门禁**（M1.5b，commit `c60b800`）——保留 Python v1 四步门禁（ruff → pytest 覆盖率 ≥80% → OpenAPI 契约 → 前端 build）。

新增 `go-lint`（gofmt / go vet / golangci-lint v2.12.2 / repolint / desktop 子模块）、`go-test`（ubuntu + windows 矩阵，三个嵌套 module 各测各的）、`desktop-frontend`（pnpm build / test / **census** / census:verify + Electron `node --test`）。CI 只在 PR 与手动触发时跑。

`go test ./...` 在本机固定失败的用例（WSL 启动器缺失、CI 缺 Windows Go job、AV 占用临时 `.exe`、`REF/` 副本污染工作树扫描等）属环境或工作树依赖，非代码回归；`internal/assembly/boot` 单包本机耗时 974.9 秒，超过 Go 默认 600 秒超时上限，一次性执行必报 timeout panic，需 `-timeout 25m`。完整清单与归因见 [`AGENTS.md`](./AGENTS.md) 的「门禁现状」表。

---

## 9. 改造形态 v2 现状

| 任务 | 状态 |
|---|---|
| M1.1 内核吸收与裁剪 | ✅ 完成（2026-10-17，8289 文件入库） |
| M1.2 模块改名 `reasonix` → `deep-tutor` | ✅ 完成（2026-10-21，commit `fd05bf6`；根模块 `module deep-tutor`，`desktop` 子模块 `module deep-tutor/desktop`） |
| M1.3 多实例 spike | ✅ 完成（2026-10-22；矩阵/全局态/深水点回填 `plan/reasonix/04` §四） |
| M1.4 品牌化与 `DEEPTUTOR.md` 建档 | ✅ 完成（2026-10-22，commits `40e4209`~`4c22dbe`；M1.4f 环境变量改名、M1.4g 半改收口随后完成） |
| M1.5 门禁基线重建 + CI 接入 | ✅ 完成（2026-10-22，commits `3a9c267`/`c60b800`；`tools/repolint/baseline.json` 已按本仓树重建，CI 已含 Go 与桌面端前端门禁） |
| M1.6 `bin/deep-tutor.exe` 冒烟 | ✅ 完成（2026-10-22，commit `ddb4070`；入口已改名 `cmd/deep-tutor`） |
| M2 数据层与认证（SQL + sqlc + JWT/PAT） | ⬜ 未开始；**第 0 步**是 `plan/reasonix/04` §四 的 K 类内核改动（`Options.Home` 贯通 + `control.Controller` 加 Roots + `secrets` 包级开关 + `healthyConfig` 按 home 记账），M2.0b 测试隔离守卫已完成 |
| M3 生成流水线 + `/api` 兼容层 | ⬜ 未开始（`internal/frontend/serve` 仅挂 `/tutor/*`；`internal/web` 服务层尚不存在 → 无 `/api` shim，旧 SPA 暂不能对接 Go 服务） |
| M4 学习闭环全页 | ⬜ 未开始 |
| M5 管理页 + 事件流 + 旧 SPA 19 页回归 | ⬜ 未开始 |

Go 侧学习域实现位于 `internal/runtime/tutor/`（R-Tutor 移植，67 个 Go 文件），8 步流水线与 v1 同构；存储当前为 state root 下的 JSON 文件树，阶段① M2/M3 改为 SQL。

---

## 10. 相关文档

- 改造方案：[`plan/reasonix/`](./plan/README.md)（总方案 / 仓库与内核 / 数据层 / API 与 shim / 多用户集成 / 任务分解）
- 改造决策档案（61 项，改造宪法）：[`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md)
- 项目分析笔记（架构 / 数据 / 流水线 / Agent / LLM / API / 插件 / 基础设施 / 工具 / 前端 / 周边）：[`note/00-INDEX.md`](./note/00-INDEX.md)
- 实施进度：[`stage.md`](./stage.md)
- 插件契约（v1，shim 验收基准）：[`API.md`](./API.md)
- Python 时代设计归档：[`docs/archive/`](./docs/archive/README.md)

---

## 11. 开源许可

- 内核与桌面端吸收自 **[DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)**（`studio` 分支，快照 `R-Tutor-src-studio-20261017.zip`），遵循 **MIT License**（全文见 [`LICENSE-REASONIX`](./LICENSE-REASONIX)）。上游维护者 Huahui Yu 与全体贡献者。
- 引用与修改披露见 [`NOTICE`](./NOTICE)：吸收范围与路径映射、已移除的上游资产、随内核携带的第三方许可（Inter / Noto Sans SC / IBM Plex Mono 字体 OFL、cua-driver LICENSE）。
- 内核文档整体位于 [`docs/kernel/`](./docs/kernel/)，溯源与资产处置说明见 [README](./docs/kernel/README.md)。
- 本仓自有代码沿用根目录 [`LICENSE`](./LICENSE)。
