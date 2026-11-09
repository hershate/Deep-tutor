use super::super::error::Result;
use super::{start, SyncJobView, SyncService};
use rusqlite::OptionalExtension;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread::JoinHandle;
#[cfg(not(test))]
use std::time::Duration;
#[cfg(not(test))]
use tauri::Manager;
use tauri::{AppHandle, Runtime};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

static SCHEDULER: Mutex<Option<JoinHandle<()>>> = Mutex::new(None);
static STOP: AtomicBool = AtomicBool::new(false);

pub fn run_due<R: Runtime>(
    app: &AppHandle<R>,
    service: &SyncService,
) -> Result<Option<SyncJobView>> {
    let Some(config) = super::config(service)? else {
        return Ok(None);
    };
    if !config.auto_sync {
        return Ok(None);
    }
    let store = service.store()?;
    // 81#6：锚点按 rowid 取最新一行——原先 MAX(finished_at) 是 RFC3339 字符串
    // 字典序比较，time crate 亚秒位数不定（0/3/6/9 位），混合精度下字典序
    // 与时间序不一致；且后续 WHERE finished_at = ?1 再按字符串匹配取
    // status 可能读到空/错行。rowid 单调递增即真实写入顺序
    let last_row = store
        .conn
        .query_row(
            "SELECT finished_at, status FROM sync_jobs WHERE kind = 'upload' ORDER BY id DESC LIMIT 1",
            [],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()?;
    let (anchor_text, last_status): (String, Option<String>) = match last_row {
        Some((finished_at, status)) => (finished_at, Some(status)),
        None => (config.updated_at.clone(), None),
    };
    let anchor = anchor_text;
    let anchor = OffsetDateTime::parse(&anchor, &Rfc3339).map_err(|_| {
        super::super::error::BackendError::new("invalid_sync_schedule", "同步调度时间无效")
    })?;
    // 47#5：失败也写 finished_at，原先失败后同样要等满 24h——断网当天的同步
    // 缺失且不自愈（一天最多尝试一次）；失败后 1 小时短退避重试，成功/取消
    // 仍按滚动 24h（决策文档的 24h 口径指成功后的同步间隔）
    let interval = if last_status.as_deref() == Some("failed") {
        1
    } else {
        24
    };
    if anchor + time::Duration::hours(interval) > OffsetDateTime::now_utc() {
        return Ok(None);
    }
    start(app, service, "upload".into(), None).map(Some)
}

#[cfg(not(test))]
pub fn start_scheduler<R: Runtime + 'static>(app: AppHandle<R>) {
    let mut scheduler = SCHEDULER.lock().unwrap_or_else(|error| error.into_inner());
    if scheduler.is_some() && !STOP.load(Ordering::Acquire) {
        return;
    }
    STOP.store(false, Ordering::Release);
    let handle = super::super::security::spawn(move || {
        for tick in 0.. {
            std::thread::sleep(Duration::from_secs(1));
            if STOP.load(Ordering::Acquire) {
                break;
            }
            if tick % 60 != 59 {
                continue;
            }
            let state = app.state::<super::super::BackendState>();
            if let Err(error) = run_due(&app, &SyncService::new(&state.learning)) {
                eprintln!("scheduled sync failed: {}", error.code);
            }
        }
    });
    match handle {
        Ok(handle) => *scheduler = Some(handle),
        Err(error) => eprintln!("scheduled sync thread failed to start: {error}"),
    }
}

#[cfg(test)]
pub fn start_scheduler<R: Runtime + 'static>(_app: AppHandle<R>) {}

pub fn stop_scheduler() {
    // E-1：置 STOP 与 take/join 必须全程持锁——否则「置位后、take 前」的
    // 窗口内 start_scheduler 会看到 STOP=true 便清零并覆盖句柄：stop 随后
    // join 到一个 STOP 已被清零、每秒循环永不退出的新线程，sync::stop
    // 永久挂死，被覆盖的旧线程同时泄漏。持锁期间 start 只能等待，join 的
    // 调度线程自身不获取 SCHEDULER 锁，无死锁。
    let mut scheduler = SCHEDULER.lock().unwrap_or_else(|error| error.into_inner());
    STOP.store(true, Ordering::Release);
    if let Some(handle) = scheduler.take() {
        let _ = handle.join();
    }
}
