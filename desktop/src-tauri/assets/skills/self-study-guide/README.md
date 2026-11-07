# Self-Study Guide

## 简介

将大纲、知识点列表或课程提纲转化为系统化自学材料的方法论指令源：拆分学习单元、搜索补充资料、设计练习与复习环节。源自 Claude Code 用户态技能；`SKILL.md` 现作为 legacy-skill 静态资产编译期嵌入二进制、仅经插件面板展示，应用内不可触发执行。

## 目录结构

```
self-study-guide/
├── SKILL.md                # 技能主文件（legacy-skill 静态展示内容）
└── README.md               # 本文件
```

> 历史版本声明的 `references/` 与 `study-materials/` 目录从未随本仓库资产
> 分发；本技能不生成任何输出目录。

## 当前资产状态

- **嵌入方式**：`SKILL.md` 由 `desktop/src-tauri/src/backend/plugins/mod.rs` 的
  `assets()` 以 `include_str!` **编译期嵌入**二进制，作为「学习方法论（历史
  Skill 资产）」静态条目经 `get_plugin_overview`（清单）与 `get_developer_asset`
  （正文）命令展示（`asset` 为 Rust 内部函数，非 IPC 命令）。
- **生效方式**：修改 `SKILL.md` 后须**重新编译 Rust**（重启应用不生效）。
- **执行边界**：应用不提供斜杠命令或关键词激活（`/self-study-guide` 及触发词
  属历史 Claude Code 机制），不执行 SKILL.md 声明的 Read/Write/WebSearch 工具。

## SKILL.md 历史行为（Claude Code 用户态技能时期，当前应用不执行）

1. **解析输入** — 读取大纲或知识点列表，识别结构与依赖关系
2. **拆分单元** — 按知识依赖和体量拆分（每单元 30-60 分钟）
3. **搜索资料** — 对每个核心概念搜索补充教学资料
4. **生成材料** — 每单元一个文件（概念 + 示例 + 练习 + 检查）
5. **生成索引** — 学习路线图与复习计划

历史输出约定（从未随本仓库分发）：`self-study-guide/study-materials/` 下
`index.md`（路线图/导航/复习计划）与 `01-单元名称.md`（单元材料），单元多时
按模块分组（`01-模块名/`）。历史工具声明：Read/Write/WebSearch。

## 适用场景（SKILL.md 方法论覆盖面）

课程学习（按课程大纲生成配套材料）、技能自学（知识点列表转学习路径）、
考前复习（考试大纲转复习材料与练习题）、知识整理（零散知识点结构化）。
适用于任何学科；材料质量取决于输入大纲详细程度。
