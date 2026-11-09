import { invoke } from '@tauri-apps/api/core'
import { openUrl } from '@tauri-apps/plugin-opener'

// 37#4/37#8：官方 opener 插件通道。capabilities 仅保留 https 外链
// （opener:allow-open-url 的 scope 只放行 https://**），不授予任意
// 协议/scheme 打开能力。
/** 用系统默认浏览器打开 https 链接（预设文档等外部资料）。 */
export function openExternalUrl(url: string) {
  return openUrl(url)
}

// 72#2：资源管理器定位收回后端 reveal_path 命令——原先前端持有
// opener:allow-reveal-item-in-dir 无 scope 权限（可定位本机任意路径）；
// 后端仅放行数据根与本进程近期导出回执（TTL 24h）
/** 在系统文件管理器中定位并选中该文件/目录（导出回执、数据目录入口）。 */
export function revealInFileManager(path: string) {
  return invoke('reveal_path', { path })
}
