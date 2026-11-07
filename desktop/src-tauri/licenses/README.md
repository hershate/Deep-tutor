# 第三方许可汇总

本目录由 `scripts/collect-licenses.mjs` 在构建安装包前填充（`make build` /
CI），随安装包分发以履行 MIT 等许可的分发义务。此 README 是占位文件：
保证 `tauri.conf.json` 中 `bundle.resources` 的 `licenses/**` 通配在尚未
生成许可文件时（如仅运行 `cargo test`）也能匹配到内容，构建不会失败。

完整依赖版本清单见仓库内的 `Cargo.lock` 与 `package-lock.json`。
