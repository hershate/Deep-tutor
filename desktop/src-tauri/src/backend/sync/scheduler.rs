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
    let (anchor_text, last_status) = resolve_anchor(&store.conn, &config.updated_at)?;
    let Some(anchor_text) = anchor_text else {
        // 124#4：最新 upload 行 finished_at 为 NULL（任务进行中）——原先
        // 按非空列解析抛 InvalidColumnType，长上传期间每分钟报错一次；
        // 进行中按「本轮跳过」处理（上传本身在跑，下一轮任务收尾自愈）
        return Ok(None);
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
) -> Result<Option<(Option<String>, Option<String>)>> {
    Ok(conn
        .query_row(
            "SELECT finished_at, status FROM sync_jobs WHERE kind = 'upload' ORDER BY rowid DESC LIMIT 1",
            [],
            |row| {
                Ok((
                    row.get::<_, Option<String>>(0)?,
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
    anchor + time::Duration::hours(interval_hours(last_failed)) <= now
}

/// 47#5：失败后 1 小时短退避重试，成功/取消按滚动 24h。
/// due() 与 next_due_at() 共用该口径——面板显示的到期时刻与调度器判定
/// 不得各自维护一份间隔常数
pub(crate) fn interval_hours(last_failed: bool) -> i64 {
    if last_failed {
        1
    } else {
        24
    }
}

/// P-150：调度锚点的单一解析口径——全表最新 upload 行的 (finished_at,
/// status)；表内从未有过 upload 行时退回配置的 updated_at（与 run_due 同源，
/// 面板显示的到期时刻不得另立一份锚点口径）
pub(crate) fn resolve_anchor(
    conn: &rusqlite::Connection,
    config_updated_at: &str,
) -> Result<(Option<String>, Option<String>)> {
    Ok(match upload_anchor(conn)? {
        Some(row) => row,
        None => (Some(config_updated_at.to_string()), None),
    })
}

/// P-150：调度器下一次到期的时刻（RFC3339）。
/// 面板的「下次自动同步」必须与调度器同源：调度器锚点取全表最新 upload 行
/// （rowid 倒序，见 upload_anchor），而 overview 只返回最近 50 条 job，
/// 前端在窗口内找不到 upload 行时无从推导真实锚点（此前退回 config.updated_at
/// 会与调度器口径分叉）。口径与 due() 一致：时钟回拨（锚点落在未来）按立即
/// 到期处理；最新 upload 仍在进行中（finished_at 为 NULL）时无到期时刻。
pub(crate) fn next_due_at(
    conn: &rusqlite::Connection,
    config_updated_at: &str,
) -> Result<Option<String>> {
    let (anchor_text, last_status) = resolve_anchor(conn, config_updated_at)?;
    let Some(anchor_text) = anchor_text else {
        return Ok(None);
    };
    let anchor = OffsetDateTime::parse(&anchor_text, &Rfc3339).map_err(|_| {
        super::super::error::BackendError::new("invalid_sync_schedule", "同步调度时间无效")
    })?;
    let now = OffsetDateTime::now_utc();
    let due_at = if anchor > now {
        now
    } else {
        anchor + time::Duration::hours(interval_hours(last_status.as_deref() == Some("failed")))
    };
    Ok(Some(due_at.format(&Rfc3339)?))
}

#[cfg(not(test))]
pub fn start_scheduler<R: Runtime + 'static>(app: AppHandle<R>) {
    let mut scheduler = SCHEDULER.lock().unwrap_or_else(|error| error.into_inner());
    // 107#4：旧句柄已死（栈溢出/OOM 等极端情形线程死亡但 STOP 未置位）
    // 时原先恒 early-return，auto_sync 静默停摆到进程重启——探活后替换重启
    let alive = scheduler
        .as_ref()
        .map(|handle| !handle.is_finished())
        .unwrap_or(false);
    if alive && !STOP.load(Ordering::Acquire) {
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
