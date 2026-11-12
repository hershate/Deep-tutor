import { invoke } from '@tauri-apps/api/core'
import { openUrl } from '@tauri-apps/plugin-opener'

// 37#4/37#8：官方 opener 插件通道。capabilities 仅保留 https 外链
// （opener:allow-open-url 的 scope 只放行 https://**），不授予任意
// 协议/scheme 打开能力。
// 91#1：openUrl 的拒绝原先直接透传插件原始英文串（无自救指引），
// 统一映射为中文类型化文案。134#3：按失败来源分流——scope 拒绝（URL
// 非 https）保持 91#1 文案；OS 打开失败（系统无默认浏览器/组策略禁用）
// 原先也报「仅支持 https 地址」，用户手里正是合法 https 被引向错误的自查
// 方向，改报真实原因（插件原始错误已进 console）
/** 用系统默认浏览器打开 https 链接（预设文档等外部资料）。 */
export function openExternalUrl(url: string) {
  return openUrl(url).catch(error => {
    console.error(error)
    const raw = typeof error === 'string' ? error : error instanceof Error ? error.message : String(error)
    if (/not allowed|not configured/i.test(raw)) {
      throw { code: 'open_url_failed', message: '无法打开链接：仅支持安全的 https 地址，由系统浏览器处理。' }
    }
    throw { code: 'open_url_failed', message: '无法打开链接：系统未设置默认浏览器，或打开操作被系统拒绝。' }
  })
}

// 72#2：资源管理器定位收回后端 reveal_path 命令——原先前端持有
// opener:allow-reveal-item-in-dir 无 scope 权限（可定位本机任意路径）；
// 后端仅放行数据根与本进程近期导出回执（TTL 24h）
/** 在系统文件管理器中定位并选中该文件/目录（导出回执、数据目录入口）。 */
export function revealInFileManager(path: string) {
  return invoke('reveal_path', { path })
}
