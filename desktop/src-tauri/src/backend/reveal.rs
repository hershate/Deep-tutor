// 72#2：导出回执注册表——资源管理器定位（reveal_path 命令）只允许两类
// 目标：当前工作区数据根（设置页入口）与本进程近期真实写出过的导出文件。
// 原先前端持有 opener:allow-reveal-item-in-dir 无 scope 权限，被注入脚本
// 可定位本机任意路径；收回后端后 capabilities 不再授予该权限。
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

static RECEIPTS: Mutex<Vec<(PathBuf, Instant)>> = Mutex::new(Vec::new());
const TTL: Duration = Duration::from_secs(24 * 60 * 60);
const CAP: usize = 64;

fn lock() -> std::sync::MutexGuard<'static, Vec<(PathBuf, Instant)>> {
    // 73#16 同口径：锁中毒不放大为全局拒绝
    RECEIPTS.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// 注册一次成功导出的目的地（用户经原生保存对话框选定、由后端真实写出）
pub(crate) fn register(path: &Path) {
    let mut entries = lock();
    let now = Instant::now();
    entries.retain(|(_, at)| now.duration_since(*at) < TTL);
    entries.push((super::lexical_absolute(path), now));
    let excess = entries.len().saturating_sub(CAP);
    if excess > 0 {
        entries.drain(..excess);
    }
}

/// 目标是否为本进程近期的导出回执（词形归一后精确比较）
pub(crate) fn is_known(path: &Path) -> bool {
    let target = super::lexical_absolute(path);
    let mut entries = lock();
    let now = Instant::now();
    entries.retain(|(_, at)| now.duration_since(*at) < TTL);
    entries.iter().any(|(known, _)| known == &target)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn receipts_register_lookup_and_expire_prune() {
        register(Path::new("F:/tmp/deeptutor-export.txt"));
        assert!(is_known(Path::new("F:/tmp/deeptutor-export.txt")));
        assert!(is_known(Path::new("F:\\tmp\\deeptutor-export.txt")));
        assert!(!is_known(Path::new("F:/tmp/other.txt")));
        // 相对段归一后同一路径仍命中
        register(Path::new("F:/tmp/sub/../note.md"));
        assert!(is_known(Path::new("F:/tmp/note.md")));
        // 过期回执在下次访问时被清出
        {
            let mut entries = lock();
            let stale = Instant::now() - TTL - Duration::from_secs(1);
            for entry in entries.iter_mut() {
                entry.1 = stale;
            }
        }
        assert!(!is_known(Path::new("F:/tmp/deeptutor-export.txt")));
    }
}
