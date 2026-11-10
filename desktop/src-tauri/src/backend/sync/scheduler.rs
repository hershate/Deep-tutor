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
    let (anchor_text, last_status) = match upload_anchor(&store.conn)? {
        Some(row) => row,
        None => (config.updated_at.clone(), None),
    };
    let anchor = OffsetDateTime::parse(&anchor_text, &Rfc3339).map_err(|_| {
        super::super::error::BackendError::new("invalid_sync_schedule", "同步调度时间无效")
    })?;
    if !due(
        anchor,
        last_status.as_deref() == Some("failed"),
        OffsetDateTime::now_utc(),
    ) {
        return Ok(None);
    }
    start(app, service, "upload".into(), None).map(Some)
}

/// 81#6/94#1：锚点按 rowid 取最新一行。sync_jobs.id 是 TEXT 主键
/// （`sync-{pid}-{计数}`），字典序与时间序无关——计数跨过 10
/// （`"…-9" > "…-12"`）或重启后 pid 变小都会乱序；此前 `ORDER BY id DESC`
/// 选中的是「字典序最大」而非「最近」的行，auto_sync 下每分钟重复上传，
/// retention 窗口被风暴版本挤满后真实历史不可逆销毁。rowid 单调递增
/// 即真实写入顺序（sync_jobs 无删除路径，rowid 不会复用）
pub(crate) fn upload_anchor(
    conn: &rusqlite::Connection,
) -> Result<Option<(String, Option<String>)>> {
    Ok(conn
        .query_row(
            "SELECT finished_at, status FROM sync_jobs WHERE kind = 'upload' ORDER BY rowid DESC LIMIT 1",
            [],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                ))
            },
        )
        .optional()?)
}

/// 到期判定。
/// 47#5：失败也写 finished_at，原先失败后同样要等满 24h——断网当天的同步
/// 缺失且不自愈（一天最多尝试一次）；失败后 1 小时短退避重试，成功/取消
/// 仍按滚动 24h（决策文档的 24h 口径指成功后的同步间隔）。
/// 94#4：finished_at 落在未来说明系统时钟被回拨——若照常比较则
/// 「anchor+24h > now」在真实时间追上前的数月内恒成立，自动同步停摆；
/// 此时按到期处理立即同步一次，新锚点以当前时钟重写，调度自愈
pub(crate) fn due(anchor: OffsetDateTime, last_failed: bool, now: OffsetDateTime) -> bool {
    if anchor > now {
        return true;
    }
    // 47#5：失败后 1 小时短退避重试，成功/取消按滚动 24h
    let interval = if last_failed { 1 } else { 24 };
    anchor + time::Duration::hours(interval) <= now
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
