# PPT Slide Builder

## 资产状态

静态 HTML 幻灯片模板与编辑规则的历史资产。`SKILL.md` 由 `plugins/mod.rs` 通过 `include_str!` 嵌入，作为 `legacy-skill` 经 `get_plugin_overview` 和 `get_developer_asset` 展示。修改后必须重新编译 Rust。

当前应用不提供 `/ppt-slide-builder`、关键词触发或 Read/Write/Edit/Glob/Bash/WebSearch 执行链，不创建 `<project-name>/index.html`。

## 文件与分发

```text
ppt-slide-builder/
├── SKILL.md                       # 嵌入二进制的历史资产说明
├── README.md                      # 本文件，仅仓库存档
├── templates/base.html            # 两页模板，仅仓库存档
└── references/animation-reference.md # 动画参考，仅仓库存档
```

只有 `SKILL.md` 进入二进制。README、模板和 references 不嵌入、不打包，安装态不能依赖这些文件。

## 模板技术契约

- 纯静态 HTML，不使用前端框架、外部库或 CDN，不导出 `.pptx`。
- 主题由 `:root` 变量控制，覆盖颜色、字体、间距、内容宽度和过渡参数。
- 页面通过 `<!-- SLIDE: slide-N -->` 和 `<!-- /SLIDE -->` 定位；添加、修改和删除后保持编号、ARIA、页码与进度一致。
- 支持 12 种元素动画、10 档延迟和 7 种页面过渡，详见 `references/animation-reference.md`。
- 支持键盘、滚轮、按钮、页点、点击、触摸和 `#slide-N` 导航；内部滚动到达边界后才触发滚轮翻页。
- `prefers-reduced-motion` 将动画和过渡压到 0.01ms；响应式断点为 768px 和 480px。
- 导航背景使用 `color-mix()`，旧浏览器可替换为 `var(--bg-alt)`。

## 资源边界

无外部图片时 HTML 可独立离线打开。本地图片必须复制到项目 `images/` 并使用相对路径，此时目录必须整体分发。外部图片和 CDN 需要直接验证 HTTP 状态、内容类型和加载结果，不能用搜索摘要代替。

## 文档内容

`SKILL.md` 保留历史需求收集、12 维度规约、P0/P1/P2 分级、页面编辑、主题、动画、导航、图片和质量检查规则，并明确这些规则不属于当前应用执行能力。
