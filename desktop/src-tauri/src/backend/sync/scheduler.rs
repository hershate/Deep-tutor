use super::super::error::Result;
use super::{start, SyncJobView, SyncService};
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
    let last_attempt = store.conn.query_row(
        "SELECT COALESCE(MAX(finished_at), '') FROM sync_jobs WHERE kind = 'upload'",
        [],
        |row| row.get::<_, String>(0),
    )?;
    let anchor = if last_attempt.is_empty() {
        &config.updated_at
    } else {
        &last_attempt
    };
    let anchor = OffsetDateTime::parse(anchor, &Rfc3339).map_err(|_| {
        super::super::error::BackendError::new("invalid_sync_schedule", "同步调度时间无效")
    })?;
    if anchor + time::Duration::hours(24) > OffsetDateTime::now_utc() {
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
