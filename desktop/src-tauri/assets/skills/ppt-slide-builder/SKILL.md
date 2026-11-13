---
name: ppt-slide-builder
description: >-
  静态 HTML 幻灯片模板与编辑规则的历史资产。当前仅作静态展示，说明主题、页面结构、
  动画、导航、图片和离线资源约束。
version: 1.3.0
---

# PPT Slide Builder

> 资产状态：历史 Claude Code 技能。当前应用不注册 `/ppt-slide-builder` 或关键词触发，不执行 Read/Write/Edit/Glob/Bash/WebSearch，也不创建演示项目。
> `SKILL.md` 经 `include_str!` 嵌入二进制；`templates/base.html` 和 `references/animation-reference.md` 只在源码仓库存档，不进入安装包。

## 1. 当前资产与历史输出

- 历史输出目录为 `<project-name>/`，入口为 `index.html`，目录名使用 kebab-case。
- 模板生成纯静态 HTML，不使用 React、Vue、外部库或 CDN，不导出 `.pptx`。
- 无外部图片时，单个 HTML 可离线打开；使用本地图片时必须同时携带 `images/` 目录，不能称为单文件自包含。
- 当前应用没有生成、预览或修改演示项目的执行链。以下内容是模板技术契约，不是应用内操作指南。

## 2. 需求收集与规约

历史流程先确定操作是创建还是编辑。创建项目必须收集标题、视觉风格、页数和逐页内容；配色、字体和动画为可选项。缺失信息每轮询问 1–2 项，用户允许自行决定或停止补充后才使用默认值。

需求规约检查 12 个维度：风格、内容结构、使用场景与受众、布局、约束、参考、叙述顺序、语言、数据来源、受众先验知识、安全边界和后续编辑。每项判断必须引用用户输入，不得添加无关页面或虚构事实数据。

优先级定义：

- P0：缺少后无法生成有效 HTML 的必填信息。
- P1：影响内容或可用性的细化信息。
- P2：不影响基本生成的可选信息。

生成前规约至少确定项目名称、标题、页数、逐页内容、`--bg`、`--text`、`--accent`、`--font-display` 和 `--font-body`。示例数据必须明确标注。

## 3. 模板占位符与页面结构

`templates/base.html` 提供两页示例，使用以下占位符：

- `{{PRESENTATION_TITLE}}`：HTML title。
- `{{TITLE}}`、`{{SUBTITLE}}`：第一页标题与副标题。
- `{{SLIDE2_TITLE}}`、`{{SLIDE2_CONTENT}}`：第二页标题与内容。
- `{{SLIDE_COUNT}}`：初始总页数。
- `{{PROGRESS_PERCENT}}`：初始进度百分比。

每页使用注释标记和固定属性：

```html
<!-- SLIDE: slide-N -->
<section id="slide-N" class="slide" data-slide="N"
         role="region" aria-label="Slide N" aria-hidden="true">
  ...
</section>
<!-- /SLIDE -->
```

第一页使用 `class="slide active"` 和 `aria-hidden="false"`，其他页面初始为隐藏状态。编辑时通过注释标记定位，不依赖行号。

## 4. 页面编辑规则

### 添加

- 追加到 `.presentation` 内最后一个 `<!-- /SLIDE -->` 之后，编号为当前最大值加 1。
- 更新 `#slideTotal` 和初始进度条；JavaScript 的 `slides.length` 自动读取新数量。
- 新页不得重复编号，内容不得使用未标注的占位文本。

### 修改

- 只替换目标注释区间内的内容，不修改标记、其他页面或无关注释。
- 仅修改内容时保留页面 ID、data-slide、role 和既有动画类。

### 删除

- 删除目标注释区间后，连续调整后续页面的注释、id、data-slide 和 aria-label。
- 更新 `#slideTotal` 和进度条，不允许编号空洞或重复。
- 删除前确认目标页编号和内容。

## 5. 主题系统

`:root` 统一保存以下 CSS 变量：

```text
--bg --bg-alt --text --text-secondary --accent --accent-hover --border
--font-display --font-body --font-mono
--slide-padding --content-max-width
--transition-duration --transition-easing
```

主题编辑修改变量值，不重命名变量，不在单页硬编码颜色或字体。默认内容最大宽度为 960px，页面内边距为 4rem，过渡时长为 0.6s，缓动为 `cubic-bezier(0.16, 1, 0.3, 1)`。

模板在 768px 和 480px 断点调整内边距、标题和导航。导航背景使用 `color-mix()`；旧浏览器兼容处理可将其替换为 `var(--bg-alt)`。

## 6. 动画与过渡

每个元素最多使用一个 `anim-*` 和一个 `anim-delay-*` 类。页面具有 `.active` 时触发动画，延迟类为 0.1–1.0 秒。编辑约定每页动画元素不超过 5 个，该数量不是模板强制校验。

过渡类只设置在 `.presentation` 上，每次一种：`trans-fade`、`trans-slide-left`、`trans-slide-right`、`trans-slide-up`、`trans-zoom`、`trans-flip`、`trans-none`。

完整类名和时长见 `references/animation-reference.md`。`prefers-reduced-motion: reduce` 将动画和过渡时长压到 0.01ms，并移除页面 transform。

## 7. 导航契约

模板 JavaScript 实现以下行为：

- 右/下方向键和 Space：下一页；左/上方向键：上一页；Home/End：首尾页。
- 上一页、下一页按钮和动态生成的页点：直接导航。
- 滚轮：内部内容可滚动时先滚动，到达上下边界后翻页；无内部滚动时直接翻页。
- 点击非交互内容或页面空白：下一页；链接、按钮、输入控件和导航区域不触发该路径。
- 水平触摸位移超过 50px 且大于垂直位移时翻页。
- URL hash 使用 `#slide-N`，加载时定位有效页面，切换时更新 hash。
- 页码和进度条按 `(current + 1) / slides.length` 更新；500ms 动画锁期间忽略新的导航请求。

## 8. 图片与外部资源

- 每个 `<img>` 必须有描述性 `alt`，并使用 `max-width:100%;height:auto` 防止溢出。
- 历史本地图片流程先验证文件，再复制到 `<project-name>/images/`，HTML 只使用相对路径，不使用绝对路径或 `file://`。
- 支持的图片扩展名为 png、jpg、jpeg、gif、webp、svg。
- 外部图片和 CDN 必须直接验证 HTTP 状态、内容类型和实际加载结果；搜索摘要不能代替资源请求。无法验证时使用 CSS 占位块并明确报告。
- 居中、标题下方、四角、底部和右侧等位置通过容器或定位样式实现；目标容器不存在时追加到页面末尾。

## 9. 质量检查

生成或编辑后检查：

- 页数、编号、注释、id、data-slide、aria-label 和 `aria-hidden` 一致。
- 页面有实际内容，标签闭合，主题变量和过渡类唯一且有效。
- 键盘、滚轮、按钮、页点、点击、触摸和 hash 导航符合模板契约。
- 内部滚动不被滚轮翻页阻断，窄窗口导航不溢出。
- 图片 alt、相对路径和尺寸约束完整，外部资源已经直接验证。
- 减弱动态效果生效，控制台无错误。

历史生成报告包含项目路径、标题、页数、过渡、主题、逐页结构和修改指引。当前应用不生成该报告或文件。
