# docs/kernel/ — 上游内核资料

本目录源自 [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix)
的 `studio` 分支（`R-Tutor-src-studio-20261017.zip`），按 MIT License 保留。
引用和修改披露见 [`NOTICE`](../../NOTICE)，许可全文见
[`LICENSE-REASONIX`](../../LICENSE-REASONIX)。

## 使用边界

- 这些文件用于上游溯源，以及仍保留的 Go 内核的规格、工具契约和测试。
- **它们不是当前产品架构、界面或安装说明。** 当前唯一交付物是
  `desktop/` 中的 Tauri 2 安装包，产品文档以根目录 [`README.md`](../../README.md)
  和 [`desktop/README.md`](../../desktop/README.md) 为准。
- 上游文档中出现的 Electron、`frontend-next`、Web 服务、旧插件示例及发布流程
  均为历史语境；即使相对链接仍存在，也不代表当前仓库交付该路径。
- `internal/tools/productdocs` 嵌入了这里的部分文档，不能将其误当作无用文件删除。
- `upstream/` 保存与仓库根文件冲突的上游原件；`upstream/github/` 中的工作流
  和模板不在本仓库生效。上游作品及协议标识保持原名。

## 本地修改

上游 `docs/` 移至本目录，相关 embed 和测试路径同步调整；用户可见产品名改为
Deep-tutor。已移除的上游资产及后续实质修改均记录在 git 提交历史和
[`NOTICE`](../../NOTICE)，不在此重复维护历史档案。

主要契约资料：

- [`SPEC.md`](./SPEC.md)、[`TOOL_CONTRACT.md`](./TOOL_CONTRACT.md)、
  [`TASK_CONTRACT.md`](./TASK_CONTRACT.md)：保留 Go 内核的规格与契约。
- [`EXTENSION_PROTOCOL.generated.md`](./EXTENSION_PROTOCOL.generated.md)：
  由协议生成器产出，不手工改写。
- [`REASONING_PROVIDERS.md`](./REASONING_PROVIDERS.md)、[`BILLING.md`](./BILLING.md)、
  [`RECOVERY.md`](./RECOVERY.md)：上游内核专题资料。
