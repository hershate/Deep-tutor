import { openUrl, revealItemInDir } from '@tauri-apps/plugin-opener'

// 37#4/37#8：官方 opener 插件通道。capabilities 限定为 reveal-item-in-dir
// 与 https 外链（opener:allow-open-url 的 scope 仅放行 https://**），
// 不授予任意协议/scheme 打开能力。
/** 用系统默认浏览器打开 https 链接（预设文档等外部资料）。 */
export function openExternalUrl(url: string) {
  return openUrl(url)
}

/** 在系统文件管理器中定位并选中该文件/目录（导出回执、数据目录入口）。 */
export function revealInFileManager(path: string) {
  return revealItemInDir(path)
}
