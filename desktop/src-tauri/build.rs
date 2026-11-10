//! 100#2：遮蔽 mingw 工具链的默认 manifest——winlibs GCC 的 *endfile spec
//! 无条件链接 default-manifest.o（`%:if-exists`），与 tauri-build 嵌入的
//! manifest 在 .rsrc 段合并时冲突，ld 报「multiple non-default manifests」
//! 并丢弃 tauri 侧（最终生效的是仅含 longPathAware/asInvoker 的默认清单，
//! Common-Controls v6 与 DPI 声明丢失）。GCC 无关闭开关，-B 前缀目录优先
//! 于系统库目录被 startfile 搜索——放一个空的 default-manifest.o 到
//! OUT_DIR 并注入 -B，让 `%:if-exists` 命中空对象（无 .rsrc 段，不参与
//! 合并）。空对象为 288 字节最小 COFF（仅 .text/.data/.bss 节头，零内容），
//! 跨工具链小版本稳定；MSVC 目标不经 GCC spec，不受影响。

/// 最小空 COFF 对象（x86_64 pe-x86-64）：无任何段内容，链接为无操作
const EMPTY_DEFAULT_MANIFEST_OBJECT: &[u8] = &[
    100, 134, 3, 0, 0, 0, 0, 0, 140, 0, 0, 0, 8, 0, 0, 0, 0, 0, 5, 0, 46, 116, 101, 120, 116, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 32, 0,
    80, 96, 46, 100, 97, 116, 97, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 64, 0, 80, 192, 46, 98, 115, 115, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 128, 0, 80, 192, 46, 102, 105,
    108, 101, 0, 0, 0, 0, 0, 0, 0, 254, 255, 0, 0, 103, 1, 102, 97, 107, 101, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 46, 116, 101, 120, 116, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 3, 1, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 46, 100, 97, 116, 97, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0,
    0, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 46, 98, 115, 115, 0, 0, 0, 0, 0,
    0, 0, 0, 3, 0, 0, 0, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0, 0,
];

fn link_empty_default_manifest_shim() {
    if std::env::var("CARGO_CFG_WINDOWS").is_err() {
        return;
    }
    // msvc 工具链不经 GCC spec，无 default-manifest.o
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        return;
    }
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR");
    let shim = std::path::Path::new(&out_dir).join("default-manifest.o");
    if std::fs::write(&shim, EMPTY_DEFAULT_MANIFEST_OBJECT).is_ok() {
        println!("cargo:rustc-link-arg=-B{}", out_dir);
    }
}

fn main() {
    link_empty_default_manifest_shim();
    tauri_build::build()
}
