# Product

<!-- impeccable:product-schema 1 -->

## Platform

Tauri 2 桌面应用（唯一用户界面，不再交付浏览器 SPA 或 Electron）。

## Stack

React + TypeScript + Vite + Tauri 2（Rust 壳），界面来源为用户提供的 `REF/frontend/app`。

## Users

以自学为主的学习者，需要围绕考试、技能或兴趣目标管理完整学习过程。

## Product Purpose

将目标、诊断、计划、资料、理解、练习、复习、项目和报告串成连续学习闭环。成功意味着学习者始终知道下一步做什么，并能看见掌握度证据。

## Positioning

不是课程列表或独立聊天框，而是带有上下文 AI 导师的个人学习操作系统。

## Operating Context

桌面多栏深度学习工作台，同时适配手机；支持本地演示数据、学习操作和设置持久化。

## Capabilities and Constraints

包含今日、AI 方案生成、学习计划、学习空间、知识地图、Agent 工作台、练习测评、间隔复习、自由笔记、KB 文档与 Wiki 引用问答、项目作品、总览／日历／按方案报告、学习社区、插件与模型供应商、备份、只读分享和访问安全设置。

方案生成覆盖四源输入、暂存解析、目标澄清、实时进度、断线续接、五维校验与局部重生成；导出覆盖 Markdown、系统打印 PDF、ICS、Anki 降级 TSV、HTML 幻灯片及插件格式。

独立模型接入中心提供 18 种官方／兼容／本地供应商预设、档案增删改、端点与模型请求检测、直连／本地路由／聚合三种模式、按学习任务分配模型、故障转移队列、Deep Link 与 JSON 脱敏导入导出、WebDAV／S3 同步流程。

API Key 仅用于内存态表单，保存时丢弃且不进入 localStorage、导出或同步内容。

当前为用户确认的本地可交互演示：AI 回复、SSE、供应商测试、安全策略和后台任务均为界面模拟，数据仅存于当前应用 WebView，不代表真实云端同步、模型服务或后端安全边界。

## Brand Commitments

产品名：知舟 Deep Tutor。中文界面。使用用户指定的 Impeccable 设计方法。

## Evidence on Hand

页面内学习记录与课程内容均为用于界面演示的合成数据，不构成真实用户成绩或学习效果声明。

## Product Principles

1. 下一步行动优先于信息堆积。
2. 掌握度必须有练习、复习或项目证据。
3. AI 优先提示与反馈，而非替代学习者完成。
4. 所有关键操作可见、可撤销或可恢复。
5. 完整功能不能牺牲阅读、响应式和无障碍体验。

## Accessibility & Inclusion

支持键盘操作、可见焦点、减弱动态效果、字号与高对比度设置，并保证移动端基本可用性。
