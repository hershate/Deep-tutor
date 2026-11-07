# PPT Slide Builder

## 简介

PPT 风格全屏网页演示文稿生成器的方法论指令源：统一样式管理、多页面增删改、元素入场动画与页面过渡动画，生成纯静态自包含 HTML（五种导航：键盘方向键、鼠标滚轮、点击空白、屏幕按钮、触摸滑动；URL hash 定位 `index.html#slide-N`）。源自 Claude Code 用户态技能；`SKILL.md` 现作为 legacy-skill 静态资产编译期嵌入二进制、仅经插件面板展示，应用内不可触发执行；templates/ 与 references/ 仅随仓库分发，不嵌入二进制、不进安装包。

## 目录结构

```
ppt-slide-builder/
├── SKILL.md                       # 技能主文件（legacy-skill 静态展示内容）
├── README.md                      # 本文件
├── templates/
│   └── base.html                  # 演示文稿基础模板（默认 2 页示例内容）
└── references/
    └── animation-reference.md     # 动画与过渡效果完整参考
```

## 当前资产状态

- **嵌入方式**：`SKILL.md` 由 `desktop/src-tauri/src/backend/plugins/mod.rs` 的
  `assets()` 以 `include_str!` **编译期嵌入**二进制，作为「幻灯片模板（历史
  Skill 资产）」静态条目经 `get_plugin_overview`（清单）与 `get_developer_asset`
  （正文）命令展示（`asset` 为 Rust 内部函数，非 IPC 命令）。
- **生效方式**：修改 `SKILL.md` 后须**重新编译 Rust**（重启应用不生效）。
- **执行边界**：应用不提供斜杠命令或关键词激活（`/ppt-slide-builder` 及触发词
  属历史 Claude Code 机制），不执行 SKILL.md 声明的 Read/Write/Edit/Bash/WebSearch
  等工具，不生成 `<project-name>/index.html` 输出目录。

## SKILL.md 历史行为（Claude Code 用户态技能时期，当前应用不执行）

- **初始化项目**：逐轮提问收集需求（标题/风格/页数/内容）→ 12 维度分析优化规约
  → 从模板生成 HTML → 质量检查 → 输出结构化生成报告（设计摘要 + 修改指引）。
- **页面操作**：添加（末尾追加自动编号）/修改（经 `<!-- SLIDE: slide-N -->`
  注释精确定位，不用行号）/删除（后续 slide 重新编号保连续）；主题经 `:root`
  CSS 变量统一修改，全局生效。
- **图片**：本地图片复制到项目 `images/` 目录用相对路径引用；网络图床链接先经
  WebSearch 验证再引用；占位图优先用 CSS 纯色块，不依赖外部服务。
- **输出**：当前工作目录 `<project-name>/index.html`，自包含可离线；导出
  PowerPoint 需复制到 Google Slides/Keynote 重新编排。
- **历史工具声明**：Read/Write/Edit/Glob/Bash/WebSearch。
- **行为约定**：用户输入模糊时逐轮追问（一次 1-2 问）不自行假设；外部 CDN/占位
  图 URL 先验证；动画遵循 `prefers-reduced-motion`。

## 技术细节

### 动画系统

`templates/base.html` 内置 12 种元素动画和 7 种页面过渡效果，无需编写 CSS：

| 动画类型 | 类名 | 效果 |
|---------|------|------|
| 淡入 | `anim-fade-in` | 透明度从 0 到 1 |
| 上滑 | `anim-slide-up` | 从下方 30px 滑入 |
| 下滑 | `anim-slide-down` | 从上方 30px 滑入 |
| 左滑 | `anim-slide-left` | 从右侧 30px 滑入 |
| 右滑 | `anim-slide-right` | 从左侧 30px 滑入 |
| 放大 | `anim-scale-in` | 从 0.7 倍放大 |
| 模糊 | `anim-blur-in` | 从模糊到清晰 |
| 翻转 | `anim-flip-in` | 3D 翻转入场 |
| 旋转 | `anim-rotate-in` | 旋转入场 |
| 弹跳 | `anim-bounce-in` | 弹性缩放入场 |
| 高亮 | `anim-highlight` | 背景高亮闪烁 |
| 打字机 | `anim-typewriter` | 逐字显示（短文本） |

支持 `anim-delay-1` 到 `anim-delay-10` 控制错落节奏。

| 过渡类型 | 类名 | 效果 |
|---------|------|------|
| 淡入淡出 | `trans-fade` | 交叉淡入淡出 |
| 左滑 | `trans-slide-left` | 页面向左推入 |
| 右滑 | `trans-slide-right` | 页面向右推入 |
| 上滑 | `trans-slide-up` | 页面向上推入 |
| 缩放 | `trans-zoom` | 缩放过渡 |
| 3D 翻转 | `trans-flip` | 带透视的翻页 |
| 无过渡 | `trans-none` | 直接切换 |

完整参考见 `references/animation-reference.md`。

### 主题系统

通过 CSS 自定义属性在 `:root` 中统一控制：

```css
:root {
  --bg: #ffffff;              /* 背景色 */
  --text: #1a1a1a;            /* 主文字色 */
  --accent: #3b82f6;          /* 强调色 */
  --font-display: 'Georgia', serif;  /* 展示字体 */
  --font-body: system-ui, sans-serif; /* 正文字体 */
  --slide-padding: 4rem;      /* 幻灯片内边距 */
  --transition-duration: 0.6s; /* 过渡时长 */
}
```

修改主题后所有幻灯片自动生效。

### 模板约束

- 仅生成纯静态 HTML，不支持 React/Vue 等框架。
- `color-mix()` 用于导航栏背景，需 Chrome 111+ / Firefox 113+ / Safari 16.2+。
