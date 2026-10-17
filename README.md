# Deep-tutor

> **版本：v0.1.0.0**
>
> 一个还在不断打磨中的**个人 / 学习性质项目**。它想解决的问题是：当你想学一个新东西，手里有一堆资料、或者只有一个模糊的念头时，能不能让 AI 帮你理出一条清楚的学习路线，并且在学的过程中陪着你练习、复习、记笔记。
>
> 它**做不到完美**，很多地方还比较粗糙，作者希望它对你有用，也欢迎你指出它的不足。

> 🔨 **改造公告（2026-10-15）**：本项目正在向 **Reasonix 内核（Go）架构完全改造**——单二进制多用户服务 + 四前端（Web / Deep-tutor Studio / CLI / TUI / ACP），完整使用 Reasonix 的内核能力（工具审批、Rewind、子代理、MCP、插件包、技能、双模型等）。
> 当前仓库状态：**Python v0.1.x 仍为可用实现**；改造决策（61 项）见 [`note/12`](./note/12-Reasonix改造决策.md)，完整方案见 [`plan/reasonix/`](./plan/README.md)，进度见 [`stage.md`](./stage.md)，实施分支 `refactor/reasonix`。
> 内核来源与致谢见文末「[开源许可与致谢](#开源许可与致谢)」。

---

## 这是什么（写给第一次来的朋友）

简单说，Deep-tutor 是一个**帮你把"想学的东西"变成"能照着做的学习计划"**的小工具。

你可以：

- 上传一份 PDF / Word / PPT，或者粘贴一段文字、贴一个网页链接，甚至只写一句"我想学 React"；
- 它会先弄明白你想学什么、现在什么水平、每天能花多少时间；
- 然后像一位耐心的老师那样，帮你把一个大目标**拆成几个阶段**、每个阶段**该学哪些知识点**、**去哪里找靠谱的资料**、**怎么安排时间**；
- 最后它还会**自己检查一遍**这份计划合不合理（知识点有没有断层、难度是不是跳跃太大、时间够不够），不合理就试着改一改。

计划生成之后，它还能陪你走完学习过程：**做题、记录进度、间隔复习、生成自学讲义、记笔记、建个人知识库、和 AI 对话**。

> 📌 一句话定位：**本地优先、单机开箱即用**的 AI 学习助手。装在自己电脑上用最舒服，也可以在信任的家庭/小团队局域网内共享。

---

## 它能帮你做什么

### 1. 把资料 / 需求变成学习方案

- **多种输入方式**：上传文件（PDF / DOCX / PPTX / TXT / MD / EPUB，可批量）、粘贴文字、网页链接、纯需求描述；图片可 OCR、音视频可转写（需额外装依赖，见下文"不足"）。
- **上传后先暂存、由你确认**：是否入库、是否解析、解析质量高低，都由你逐项决定，不会偷偷处理。
- **看不懂你的需求时会追问**：比如会问你"现在是什么基础？""每天能学多久？"，回答完再生成。
- **实时进度**：生成过程像看直播一样实时显示在做什么，断了能接着来，随时能取消。
- **可以局部重做**：觉得某一环节（阶段设计 / 资源 / 排程）不满意？带反馈重跑那一节就行，历史版本都能切换和对比。
- **日程时间线**：学习日程按周分组、逐日展示，自动高亮"今天"和下一个要做的事。

### 2. 边学边练

- **练习测验**：按知识点出题（选择 / 填空 / 简答 / 开放题），服务器判分，错题可以重练。
- **进度跟踪**：每个知识点的掌握程度一目了然，有"待复习"提醒。
- **间隔复习**：到期的知识点 + 错题统一在一个页面翻卡自评（忘了 / 模糊 / 记住了），像 Anki 那样安排复习节奏。
- **自学讲义**：逐单元生成自学材料，带阅读器（字号调节、目录导航、内容检索、单篇下载）。
- **打卡与统计**：学习打卡（支持补卡）、连续天数、打卡热力图、学习时长分布、番茄钟专注计时器。

### 3. 记笔记、建知识库

- **知识库**：文档直接上传（不依赖学习方案），可搜索、按类型筛选，原件与解析文本对照查看；还能**就着文档向 AI 提问**（带引用出处），问答记录会保存。
- **独立笔记**：自由创建（标题 + Markdown 正文，编辑器带格式工具栏），支持导出 `.md`、打包 ZIP，也能从 ZIP 批量导入（方便和 Obsidian / Notion 互导）。
- **全局搜索**：顶栏搜索框输入即出建议，一键搜方案 / 笔记 / 知识库 / 知识点。

### 4. 和 AI 聊聊

- 一个类似 DeepSeek 的对话页面，**会话历史不丢失**，AI 还能主动记住你交代过的要点（可随时查看和管理）。
- AI 在对话里可以**真正动手**：联网搜索、抓取网页、读写它自己的工作区文件、把草稿提升为正式方案/笔记（需你确认）。

### 5. 导出和分享

- 方案可以导出为 **Markdown / PDF / PPT 风格网页幻灯片 / 日历(.ics) / Anki 卡片包**。
- 可以生成**只读分享链接**（免登录查看），在设置页能统一管理、随时撤销。
- 全量数据支持**备份导出 / 导入**（导入前有预检摘要）。

---

## ⚠️ 它还不够好的地方（请你先了解）

诚实地说，这个项目有不少局限。**请在使用前花一分钟读完这一节**，尤其是和安全、费用相关的几条。

### 关于安全（最重要）

- **它是为"本地 / 信任的局域网"设计的，不是一个上过锁的云端产品。** 如果你要把它暴露到公共互联网，请务必在前面加一层 HTTPS 反向代理（如 Caddy / Nginx），并认真配置认证。**不建议直接公网裸跑。**
- **零配置首次启动是"开放模式"**：为了让你开箱即用，没设密码时它会免登录运行，并且监听所有网卡——**同一局域网内的任何人都能访问你的数据**。请务必在首次设置向导或"设置"页里设一个访问密码（设了之后开放模式会自动关闭）。
- **认证设计存在已知权衡**：浏览器登录凭证（JWT）以明文存在浏览器的 localStorage 里；如果配置了 `VITE_AUTH_TOKEN`，它会被打包进前端代码。这些设计都假定你是在**本机或受信任的局域网**里使用，不适合公网多用户场景。

### 关于费用

- **生成功能依赖付费的 AI 接口**，会产生真实费用（以人民币 ¥ 计价）。你需要在设置页配置自己的 API Key。虽然内置了**会话级 / 每日预算守卫**来防止费用失控，但它只是"尽量兜底"，**不能 100% 杜绝异常花费**，请你留意用量页的费用趋势。

### 关于功能完整度

- **部分高级输入需要额外装依赖**：图片 OCR（需 tesseract）、音视频转写（需 faster-whisper）、PDF 导出（需 weasyprint）默认都没装；没装时对应入口会明确提示"依赖缺失"，其余功能不受影响。
- **记忆功能不是全自动的**：AI 只会在它觉得需要时去翻自己记下的要点，而不是像某些产品那样自动召回全部历史。这是有意为之的设计，但如果你期待"全自动记忆"，可能会觉得不够。
- **幻灯片是网页（HTML），不是真正的 .pptx 文件**：导出的"PPT 风格幻灯片"是可以在浏览器里演示的 HTML，不是 PowerPoint 原生文件。
- **数据库是单机文件（SQLite）**：个人使用完全够，但它不是为"很多人同时高强度使用"设计的（未来有迁移到 PostgreSQL 的打算，目前未实现）。

### 关于成熟度

- **这是一个个人 / 学习性质项目，没有服务等级保证（SLA）。** 代码经历过一次较彻底的自审（2026-07-18，发现并修复了 87 项问题，详见 [`docs/archive/err.md`](./docs/archive/err.md)），但**几乎可以肯定仍存在未被发现的问题**。请把它当作"还不错但需自行承担风险"的工具，重要数据请记得备份。
- **功能很多，初次上手可能有点懵**：界面信息密度较高，新用户可能需要一点时间摸索。我们尽量做了键盘快捷键（`?` 查看全部）和引导，但还远谈不上"傻瓜式"。
- **文档以中文为主**：对中文用户友好，但暂无完整的英文文档。
- **依赖外部服务**：资源检索依赖 Bing 搜索、生成能力依赖第三方 AI 模型，它们的服务可用性、速率限制、内容政策都会影响到本工具。

### 已知、暂未处理的小项

- 开发服务器依赖 `esbuild` 存在一个已知安全告警（仅影响本地 `npm run dev` 开发，**不影响生产构建产物**；计划在 Vite 大版本升级时一并处理，详见 [`docs/archive/err.md`](./docs/archive/err.md) E6）。
- 前端暂未引入 ESLint 代码检查（CI 中已强制 `tsc` 类型检查）。

> 尽管有上面这些不足，作者仍然尽力让它**稳定、可回滚、可审计**：每次 AI 调用都有记录、生成过程可中断可恢复、数据库可备份可迁移。

---

## 适合谁、不适合谁

✅ **可能适合你**，如果你：

- 想自学某个领域，但不知道该按什么顺序学；
- 手里有一堆资料，想要一份结构化的学习路线；
- 喜欢本地部署、自己掌控数据、不想把内容传到不信任的云服务；
- 愿意自己配一下 AI API Key，并承担相应费用。

❌ **可能不适合你**，如果你：

- 想要一个开箱即用、零成本、零配置的"成品"商业软件；
- 要在公网上面向很多陌生用户提供服务（它没做这层加固）；
- 完全不想接触任何命令行或配置项。

---

## 三分钟试用（最简方式）

> 需要：**Python 3.11+**（必需）、**Node.js 20+**（仅首次构建前端需要）。

```bash
# Windows（PowerShell）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1

# Linux / macOS
bash scripts/start.sh
```

这个脚本会自动完成：建虚拟环境 → 装依赖 → 构建前端 → 数据库迁移 → 启动服务 → 打开浏览器。之后再运行会跳过已完成的步骤，秒级启动。

启动后访问 **http://localhost:41173** （局域网内其他机器用 `http://<你的IP>:41173`）。

- **首次进入**会看到一个设置向导：可以设置访问密码、配置 AI API Key（都能跳过，之后再补）。
- 没配 API Key 时，应用照常打开、数据照常能看，只是"生成"类功能暂时不能用。

> 想换端口？加 `--port 9000`（PowerShell 用 `-Port 9000`，或设 `PORT` 环境变量）。
> 默认端口是 41173 而不是常见的 8000，是因为 8000 在 Windows 上经常落在系统保留端口段里导致启动失败。

---

## 常见问题（FAQ）

**Q：不写任何配置真的能启动吗？**
能。不配 `.env`、不设密码也能跑（以"开放本机模式"运行）。但请务必在向导或设置页设个密码，尤其是局域网共享时。

**Q：必须用 Anthropic 的 Claude 吗？**
不是。当前推荐用性价比更高的 `deepseek-v4-flash`（在设置页配一个指向 DeepSeek 兼容端点的"供应商档案"即可）。未配置任何供应商档案时，会回退到默认的 `claude-sonnet-5`。接入任何第三方模型时，请遵守对应平台的服务条款与使用规范，自行配置合法凭证并承担费用与合规责任。

**Q：数据存在哪里？安全吗？**
全部存在本地的 `data/` 目录（一个 SQLite 文件 + 上传/导出的文件）。停机后把整个 `data/` 打包即可备份；也可以在"设置 → 备份"里导出 JSON。**不会自动上传到任何地方**（除了你主动调用 AI 接口时，内容会发给所配置的模型供应商）。

**Q：生成的学习方案靠谱吗？**
它由 AI 生成，并经过了五维自动校验（依赖是否完整、难度是否递进、认知负荷、里程碑可行性、缺口分析）。但 AI 会犯错，**资源链接可能失效、内容可能有偏差**——请把它当作"一个挺能干的初稿"，关键之处仍需你自己判断。

**Q：忘记密码怎么办？**
在 `.env` 里设置 `ADMIN_PASSWORD` 后重启，会强制重置管理员密码；或者一直可以用 `AUTH_TOKEN` 作为回退登录。

---
---

> 以下内容面向**开发者和进阶用户**。普通使用者可以不看。

## 技术栈

> **改造目标形态（v2，进行中）**：Go 单二进制（Reasonix 内核）多用户服务 + SQL（SQLite 预留 PG）+ 四前端全套 + /api 重设计（旧 SPA 经 shim 兼容层回归）。详见 [`plan/reasonix/`](./plan/README.md)。以下为当前可用的 Python 实现。

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 / SQLite（WAL）/ Alembic / pydantic v2 / pydantic-settings / anthropic SDK / pyjwt；PDF 解析 marker（高质量）+ pypdf（快速）；可选 tesseract OCR / faster-whisper 转写；Anki 导出 genanki。
- **前端**：React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + React Router + Zustand + react-markdown + react-window + KaTeX（数学公式）。
- **AI 架构**（借鉴 [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) 三支柱）：缓存优先循环、工具调用修复（校验 + 重试≤3 → 降级默认）、成本计量 + 预算守卫；两层执行模型（确定性编排器 + 内层工具循环）；Skill 作方法论指令源（[`skills/`](./skills/)）。
- **部署**：本地直起脚本（[`scripts/start.sh`](./scripts/start.sh) / [`start.ps1`](./scripts/start.ps1)，uvicorn 单进程托管前端 SPA + `/api`，默认端口 `41173`）。

## 目录结构

> 2026-10-17 起，仓库为**双栈并存**：Reasonix 内核（Go，改造目标形态，已全量吸收自上游 studio 快照）+ Python v1（当前可用实现，保留至阶段① M5 验收）。内核的引用声明与路径映射见 [`NOTICE`](./NOTICE)。

```text
Deep-tutor/
├── ── 改造工程与文档（本仓自有）──
├── plan/            # 改造方案（plan/reasonix/ 六份）；Python 时代计划已归档 docs/archive/plan-v1/
├── note/            # 项目分析笔记（01–11）+ Reasonix 改造决策档案（12，改造宪法）+ graft/ 上游嫁接一手档案
├── stage.md         # 分阶段实施进度跟踪（活文件，持续追加）
├── API.md           # v1 插件 API 契约（改造期 = shim 兼容层验收基准）
├── NOTICE           # 第三方作品引用与许可声明（Reasonix 吸收权威声明）
├── AGENTS.md        # 仓库工作指引（AI agent 与人类协作者）
├── docs/archive/    # Python 时代归档（plan-v1 12 份 / chat.md / err.md 审计清单）
├── ── Reasonix 内核（Go，吸收自上游，见 NOTICE；完整清单 docs/kernel/README.md）──
├── internal/        # 内核实现（base/contract/safety/state/model/platform/tools/ext/runtime/session/assembly + runtime/tutor=Deep-tutor 学习域）
├── cmd/             # 命令行入口（tui/serve/acp/studio 等）
├── tools/  sdk/     # 门禁（repolint 等）/ Go SDK
├── desktop/         # Electron 宿主 + frontend-next 统一前端（阶段④前端基座）
├── docs/kernel/     # 上游工程文档（原样冻结，溯源说明见其 README；upstream/=与本仓根冲突的上游原件存档）
├── scripts/reasonix/# 上游工具脚本
├── workers/ npm/ examples/ benchmark*/ output/ release-notes/   # 内核周边资产（workers 等禁用项代码保留、不接线）
├── go.mod 等        # 根级构建基座（Makefile/golangci/goreleaser/.githooks 等）
├── ── Python v1（当前可用实现，41173 端口；阶段④退役）──
├── backend/         # FastAPI 后端（app / alembic / tests 1100+ 项）
├── frontend/        # Vite + React 前端（阶段①②作 shim 回归工具）
├── extension/       # 浏览器扩展（MV3 网页剪藏，阶段④重构）
├── plugins/         # 后端插件目录（功能将由 Reasonix 扩展体系等价承接）
├── plugin-examples/ # 前端插件示例（四功能将在新前端重建）
├── skills/          # LLM 方法论 Skill（deep-tutor-* 与 vendor 复用）
├── scripts/         # 本地直起脚本 start.sh / start.ps1（另含 reasonix/ 上游脚本）
├── ── 运行时与参考 ──
├── data/            # 运行时数据（gitignore，启动时自动创建；v2 目录总图见 plan/reasonix/02）
├── REF/             # 吸收用上游源快照（gitignore，20261017 zip 为本地备份，仅供对照）
├── .env.example     # Python v1 环境变量样例（内核配置见 reasonix.example.toml）
└── README.md
```

## 手动开发启动

### 1. 后端

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows: source .venv/bin/activate (Linux)
pip install -r requirements.txt
uvicorn app.main:app --reload   # dev 后端默认 :8000
```

健康检查：`curl -H "X-Auth-Token: <你的 AUTH_TOKEN>" http://localhost:8000/api/health`

### 2. 前端

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173 （/api 经 Vite 代理到 :8000；后端若起在 41173 等其他端口，用 VITE_API_PROXY_TARGET=http://localhost:41173 npm run dev 覆盖）
```

## 配置项（.env，全部可选）

> **零配置开箱即用**：不配任何参数也能启动。下方字段用于多用户、固定密钥等高级场景。完整说明见 [`docs/archive/plan-v1/09-audit.md`](./docs/archive/plan-v1/09-audit.md) §十七。

```bash
cp .env.example .env   # 可选。所有字段均有默认值或可在界面内配置
```

| 变量 | 说明 |
| --- | --- |
| `JWT_SECRET` | JWT 签名密钥。留空则自动生成随机密钥并持久化到 `data/.jwt_secret`（重启不失效；多实例部署需显式设置同一密钥）。生成：`python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | 多用户场景建议。管理员初始密码，启动时自动创建/收敛 `ADMIN_USERNAME`（默认 `admin`）账号；也可在首次设置向导/设置页直接设置（忘记密码时配此项重启可强制重置） |
| `AUTH_TOKEN` | 简单 token 回退（与 JWT 双轨；API/浏览器扩展可用它认证）。始终有效——即使设置页建了个人访问令牌（PAT），也可用它回退登录防锁死 |
| `DATABASE_URL` | 可选。完整 SQLAlchemy URL（如切换 PostgreSQL）；留空则使用 `DB_PATH` 的 SQLite（默认 `data/deep-tutor.db`） |
| `ANTHROPIC_API_KEY` | 生成功能必需（也可在首次设置向导或 Web 设置页配置供应商档案 / Anthropic 回退 Key 档案，无需写入 .env；档案优先于 .env） |
| `ANTHROPIC_MODEL` | 模型名，代码默认值 `claude-sonnet-5`；**当前推荐 `deepseek-v4-flash`**（见下方说明，可在设置页 / 供应商档案覆盖） |
| `SEARCH_ENGINE` | 资源搜索引擎：`auto`（默认，有 key 用付费 API、无 key 用免 Key 网页版）/ `bing`（仅付费 API）/ `bing_web`（仅免 Key 网页版） |
| `BING_API_KEY` | 可选。不配置时自动使用免 Key 的 Bing 网页版；也可在设置页建 Bing Key 档案卡片切换 |
| `BUDGET_PER_SESSION_USD` / `DAILY_BUDGET_USD` | 会话 / 日预算（字段名沿用 USD，实际计费按设置页「模型计费」表以人民币 ¥ 计；可在设置页按用户覆盖） |

> **推荐模型**：当前推荐使用 `deepseek-v4-flash`（性价比高，「模型计费」表已预填其参考价）。需在「设置」页配置一个指向 DeepSeek 兼容端点（`base_url`）的供应商档案（含该模型对应的 `api_key`）方可生效；未配置任何供应商档案时回退到 `ANTHROPIC_MODEL` 默认值 `claude-sonnet-5`。
>
> **模型使用合规**：接入任何第三方模型（DeepSeek / Anthropic / OpenAI 等）时，请遵守对应平台的服务条款与使用规范（API 用量与速率限制、内容政策、数据合规、计费规则等），自行配置合法有效的凭证并承担相应费用与合规责任。

> **安全提示（认证设计权衡）**：前端 JWT 明文存于 localStorage，且 `VITE_AUTH_TOKEN`（若设置）会打包进客户端 bundle——两者都假定本机/受信局域网部署。多用户或公网部署请勿设置 `VITE_AUTH_TOKEN`（走登录页 JWT），并在公网前加 HTTPS 反代。

启动时自动执行数据库迁移（`app.db.migrate`）：新库建表、旧库增量迁移；历史 `create_all` 时期的无版本库会按 schema 特征探测代际后接续迁移，无需手工处理。

## 部署 / 升级 / 备份

**部署（本地直起，本机/局域网）**：单个 Python 进程托管前端 SPA + `/api`（默认 `:41173`），无需 Docker/nginx：

```bash
bash scripts/start.sh          # Linux / macOS
# 或：powershell -ExecutionPolicy Bypass -File scripts\start.ps1   # Windows
```

脚本自动完成：建 `backend/.venv` → 装依赖 → 无 `.env` 时以零配置模式继续 → 无 `frontend/dist` 且有 npm 时自动构建前端 → 数据库迁移 → 启动并自动打开浏览器（`NO_BROWSER=1` 关闭）。再次运行幂等秒过。依赖：Python 3.11+（必需）、Node.js 20+（仅首次构建前端需要）。

**升级**：

```bash
git pull
bash scripts/start.sh   # 依赖/前端/迁移自动增量处理（Windows 用 start.ps1）
```

**备份**：数据全部在 `data/`（SQLite + 上传/导出文件），停机后整体打包即可：

```bash
tar czf dt-backup-$(date +%Y%m%d).tgz data/        # Linux/macOS
# Windows PowerShell: Compress-Archive data dt-backup-$(Get-Date -f yyyyMMdd).zip
```

也可在应用内使用「设置 → 备份」导出 / 导入 JSON 备份（方案、进度、笔记、偏好等，导入前有预检摘要）。

### 可选依赖

- **OCR / 音视频转写**：默认未装（降级设计，其余功能不受影响）。上传图片 OCR 需安装 `tesseract`（+ 中文语言包）与 `pip install pytesseract Pillow`，音视频转写需 `pip install faster-whisper`（装进 `backend/.venv`）；缺失时对应入口报 `dependency_missing`。
- **PDF 导出**：需 `pip install weasyprint`（Windows 另需 GTK 运行时，Linux 需 pango）；缺失时 PDF 导出自动降级为 Markdown + 提示。
- **HTTPS**：本机/局域网部署不需要；若暴露公网，请在前面加一层反代（Caddy / Nginx Proxy Manager 等）终止 TLS 并转发到 `:41173`。

## 浏览器扩展（网页剪藏）

[`extension/`](./extension/) 为 MV3 浏览器扩展：在任意网页选中文本，一键发送到 Deep-tutor 成为会话资料。装载方式与配置见其 [README](./extension/README.md)。

## 插件开发

> ⚠️ **改造期状态（note/12 决策 #6/#25/#59）**：以下两条插件版本线属 **v1（Python 版）**。改造后：前端插件宿主 `window.DeepTutor` 废弃（能力改由 Reasonix 插件包 / Extension 结构化 UI 承担）；后端 Python 插件体系终结（8 个内置插件 + 1 个默认禁用的示例插件，功能将在新架构全部等价实现，见 `plan/reasonix/05` 阶段③等价表）；前端插件示例的四项功能（番茄钟/浮层笔记/换肤/阅读器）将作为内置能力在新前端重建。v1 插件在 Python 版运行期间继续可用。

Deep-tutor 提供前后端两条插件 API 版本线（各自语义化版本、只增不改；**当前版本号以 [API.md](./API.md) 文首版本线表为准**，本文件不钉版本防过期）：

- **前端插件**（`window.DeepTutor`，当前 `1.17.0`）：浏览器端 JS 插件——小组件插槽（首页仪表盘 / 侧栏底部 / 方案详情 / 方案列表 / 复习 / 学习中心 / 笔记 / 用量 / 知识库）、皮肤令牌、API 事件、REST 代理（`api.get` / `api.request`）、热启停（disable/enable/unload/reload）、插件 URL 持久化加载。侧栏 🧩「插件」页可视化加载/移除/启停（免控制台）；也可控制台试：`await DeepTutor.plugins.load('https://…/plugin.js')`。
- **后端插件**（[`plugins/`](./plugins/) 目录，当前 `1.20.0`）：Python 插件自动发现装载——REST 路由命名空间 `/api/plugins/<id>/`（默认带宿主认证）、核心事件订阅（方案/材料/进度/测验/打卡/记忆/启动关闭）、Agent 工具注册、记忆系统 CRUD 门面、自定义 LLM 协议（`provider_bridge`）、MCP 桥（HTTP + stdio）。把 `.py` 文件放进 `plugins/` 重启即生效，`_` 前缀即停用；内置示例见 [plugins/](./plugins/)（study_export_pack / study_stats / epub_reader / ai_tutor / security_guard / webhook_notify / provider_bridge / mcp_bridge），装载约定见 [plugins/README.md](./plugins/README.md)。

## 测试与 CI

```bash
cd backend
.venv/Scripts/python -m pytest        # 超过 1100 项测试（含契约/漂移守卫）
.venv/Scripts/python -m ruff check .  # lint（E4/E7/E9/F，行宽 120）
cd ../frontend && npm run build       # tsc 类型检查 + vite 构建
```

GitHub Actions CI（[.github/workflows/ci.yml](./.github/workflows/ci.yml)）四步门禁：ruff → pytest（覆盖率 ≥80%）→ OpenAPI 契约检查 → 前端 build。

## 了解更多

- **Reasonix 改造方案**：[`plan/reasonix/`](./plan/README.md)（六份：总方案/仓库与内核/数据层/API 与 shim/多用户集成/任务分解）
- **改造决策档案（61 项，改造宪法）**：[`note/12-Reasonix改造决策.md`](./note/12-Reasonix改造决策.md)
- 项目分析笔记（架构/数据/流水线/Agent/LLM/API/插件/前端）：[`note/00-INDEX.md`](./note/00-INDEX.md)
- 实施进度：[`stage.md`](./stage.md)
- 插件契约（v1，shim 验收基准）：[`API.md`](./API.md)
- Python 时代设计归档：[`docs/archive/`](./docs/archive/README.md)

## 开源许可与致谢

- 本项目内核与桌面端吸收自 **[DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)**（`studio` 分支，快照 `R-Tutor-src-studio-20261017.zip`），遵循其 **MIT License**（全文见 [`LICENSE-Reasonix`](./LICENSE-REASONIX)）。感谢 Reasonix 的维护者 **Huahui Yu** 与全体贡献者——缓存优先的 Agent 循环、工具调用修复、成本计量三支柱最早启发了本项目的 Python 版实现，如今更进一步直接以其内核为基座。
- **引用与修改的权威声明见 [`NOTICE`](./NOTICE)**：吸收范围与路径映射、对上游代码的全部修改披露（目前仅 productdocs 两行 import 适配）、随内核携带的第三方许可（Inter / Noto Sans SC / IBM Plex Mono 字体 OFL、cua-driver LICENSE）。
- 内核文档（上游 `docs/`）整体落在 [`docs/kernel/`](./docs/kernel/)，其溯源说明见 [`docs/kernel/README.md`](./docs/kernel/README.md)；随内核携带的工程规约将在改造 M1 里程碑本地化为 `DEEPTUTOR.md`。
- 其余本项目自有代码沿用仓库根 [`LICENSE`](./LICENSE)。
