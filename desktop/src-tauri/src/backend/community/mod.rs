pub mod commands;

use super::commands::emit;
use super::error::{BackendError, Result};
use super::store::Store;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

pub struct CommunityState {
    database_path: PathBuf,
    #[cfg(test)]
    _guard: Option<tempfile::TempDir>,
}

impl CommunityState {
    pub fn new(path: &Path) -> Self {
        Self {
            database_path: path.to_path_buf(),
            #[cfg(test)]
            _guard: None,
        }
    }

    #[cfg(test)]
    pub fn for_tests() -> Result<Self> {
        let guard = tempfile::tempdir()?;
        Ok(Self {
            database_path: guard.path().join("app.db"),
            _guard: Some(guard),
        })
    }

    pub fn store(&self) -> Result<Store> {
        Store::new(self.database_path.clone())
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PostInput {
    pub title: String,
    pub body: String,
    pub kind: String,
    pub project_id: Option<i64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GroupInput {
    pub name: String,
    pub topic: String,
    pub schedule: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomInput {
    pub group_id: Option<i64>,
    pub title: String,
    pub topic: String,
    pub start_at: String,
    pub duration_minutes: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplyView {
    pub id: i64,
    pub post_id: i64,
    pub body: String,
    pub author: String,
    pub accepted: bool,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PostView {
    pub id: i64,
    pub title: String,
    pub body: String,
    pub kind: String,
    pub project_id: Option<i64>,
    pub project_title: Option<String>,
    pub author: String,
    pub likes: u32,
    pub liked: bool,
    pub bookmarked: bool,
    pub replies: Vec<ReplyView>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GroupView {
    pub id: i64,
    pub name: String,
    pub topic: String,
    pub schedule: String,
    pub joined: bool,
    pub member_count: u32,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomView {
    pub id: i64,
    pub group_id: Option<i64>,
    pub title: String,
    pub topic: String,
    pub start_at: String,
    pub duration_minutes: u32,
    pub status: String,
    pub reserved: bool,
    pub reservation_count: u32,
    pub reminder_at: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CommunityStats {
    pub posts: u32,
    pub replies: u32,
    pub reviews: u32,
    pub accepted_replies: u32,
    pub likes: u32,
    pub bookmarks: u32,
    pub joined_groups: u32,
    pub active_reservations: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CommunityOverview {
    pub posts: Vec<PostView>,
    pub groups: Vec<GroupView>,
    pub rooms: Vec<RoomView>,
    pub stats: CommunityStats,
    pub now: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReminderView {
    pub id: i64,
    pub room_id: i64,
    pub title: String,
    pub start_at: String,
    pub remind_at: String,
    pub delivered_at: Option<String>,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

fn parse_time(value: &str) -> Result<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339).map_err(|_| {
        BackendError::invalid("invalid_room_time", "预约时间必须是有效的 RFC 3339 时间")
    })
}

/// 25#15：入库前把任意偏移的 RFC 3339 时间统一为 UTC Z 形态——后续到期/重叠
/// 判定用字符串字典序比较（与 now() 的 Z 串），保留 "+08:00" 偏移会错序数小时
fn normalize_time(value: &str) -> Result<String> {
    Ok(parse_time(value)?
        .to_offset(time::UtcOffset::UTC)
        .format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS community_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            kind TEXT NOT NULL,
            project_id INTEGER,
            project_title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS community_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS community_reactions (
            post_id INTEGER NOT NULL REFERENCES community_posts(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (post_id, kind)
        );
        -- 53#8：帖子列表逐帖点查回复计数、删除走 FK CASCADE——无 post_id
        -- 前缀索引时社区页打开随帖量二次方变慢
        CREATE INDEX IF NOT EXISTS community_replies_post ON community_replies(post_id, id);
        CREATE TABLE IF NOT EXISTS community_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            topic TEXT NOT NULL,
            schedule TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS community_group_members (
            group_id INTEGER NOT NULL REFERENCES community_groups(id) ON DELETE CASCADE,
            joined_at TEXT NOT NULL,
            left_at TEXT,
            PRIMARY KEY (group_id)
        );
        CREATE TABLE IF NOT EXISTS community_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER REFERENCES community_groups(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            topic TEXT NOT NULL,
            start_at TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS community_reservations (
            room_id INTEGER NOT NULL REFERENCES community_rooms(id) ON DELETE CASCADE,
            reserved_at TEXT NOT NULL,
            cancelled_at TEXT,
            PRIMARY KEY (room_id)
        );
        CREATE TABLE IF NOT EXISTS community_reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL UNIQUE REFERENCES community_rooms(id) ON DELETE CASCADE,
            remind_at TEXT NOT NULL,
            delivered_at TEXT,
            created_at TEXT NOT NULL
        );",
    )?;
    // 31#33：25#15 只改写入路径，存量 '+08:00' 行与 Z 串做字典序比较会错序
    // 8 小时（claim 提前/推迟触发、rooms ORDER BY start_at 混排）——回填
    // Z 形态；无法解析的行保持原样（到期判定走 parse_time 不受影响）
    {
        let rooms: Vec<(i64, String)> = {
            let mut statement = conn
                .prepare("SELECT id, start_at FROM community_rooms WHERE start_at NOT LIKE '%Z'")?;
            let rows = statement
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            drop(statement);
            rows
        };
        for (id, start_at) in rooms {
            if let Ok(normalized) = normalize_time(&start_at) {
                conn.execute(
                    "UPDATE community_rooms SET start_at = ?1 WHERE id = ?2",
                    params![normalized, id],
                )?;
            }
        }
        let reminders: Vec<(i64, String)> = {
            let mut statement = conn.prepare(
                "SELECT id, remind_at FROM community_reminders WHERE remind_at NOT LIKE '%Z'",
            )?;
            let rows = statement
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            drop(statement);
            rows
        };
        for (id, remind_at) in reminders {
            if let Ok(normalized) = normalize_time(&remind_at) {
                conn.execute(
                    "UPDATE community_reminders SET remind_at = ?1 WHERE id = ?2",
                    params![normalized, id],
                )?;
            }
        }
    }
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    for table in [
        "community_reminders",
        "community_reservations",
        "community_rooms",
        "community_group_members",
        "community_groups",
        "community_reactions",
        "community_replies",
        "community_posts",
    ] {
        conn.execute(&format!("DELETE FROM {table}"), [])?;
    }
    Ok(())
}

fn reply_row(conn: &Connection, id: i64) -> Result<ReplyView> {
    Ok(conn.query_row(
        "SELECT id, post_id, body, accepted, created_at FROM community_replies WHERE id = ?1",
        params![id],
        |row| {
            Ok(ReplyView {
                id: row.get(0)?,
                post_id: row.get(1)?,
                body: row.get(2)?,
                author: "本机用户".into(),
                accepted: row.get(3)?,
                created_at: row.get(4)?,
            })
        },
    )?)
}

fn post_row(conn: &Connection, id: i64) -> Result<PostView> {
    let (title, body, kind, project_id, project_title, created_at, updated_at): (String, String, String, Option<i64>, Option<String>, String, String) = conn.query_row(
        "SELECT title, body, kind, project_id, project_title, created_at, updated_at FROM community_posts WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?)),
    )?;
    let likes = conn.query_row(
        "SELECT COUNT(*) FROM community_reactions WHERE post_id = ?1 AND kind = 'like'",
        params![id],
        |row| row.get(0),
    )?;
    let liked = conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM community_reactions WHERE post_id = ?1 AND kind = 'like')",
        params![id],
        |row| row.get(0),
    )?;
    let bookmarked = conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM community_reactions WHERE post_id = ?1 AND kind = 'bookmark')",
        params![id],
        |row| row.get(0),
    )?;
    let mut statement =
        conn.prepare("SELECT id FROM community_replies WHERE post_id = ?1 ORDER BY id")?;
    let ids = statement
        .query_map(params![id], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let replies = ids
        .into_iter()
        .map(|id| reply_row(conn, id))
        .collect::<Result<Vec<_>>>()?;
    Ok(PostView {
        id,
        title,
        body,
        kind,
        project_id,
        project_title,
        author: "本机用户".into(),
        likes,
        liked,
        bookmarked,
        replies,
        created_at,
        updated_at,
    })
}

fn posts(conn: &Connection, kind: Option<&str>, query: Option<&str>) -> Result<Vec<PostView>> {
    if let Some(kind) = kind {
        if !["discussion", "peer_review", "resource"].contains(&kind) {
            return Err(BackendError::invalid("invalid_post_kind", "讨论类型无效"));
        }
    }
    let query = query.unwrap_or("").trim().to_lowercase();
    if query.len() > 200 {
        return Err(BackendError::invalid(
            "invalid_search_query",
            "搜索词不能超过 200 字节",
        ));
    }
    // 13#13：关键词下推 SQL LIKE（配额量级 500），不再“先 LIMIT 200 再内存
    // 过滤”——否则第 201 条以后的旧帖即使完全匹配也永远搜不到
    // B-10：转义须先处理 ~ 自身（ESAPE 字符），否则含 ~ 的查询把 ~x 当
    // 字面 x 永远匹配不到原文；大小写比较用注册的 uni_lower（SQL 内建
    // lower 仅覆盖 ASCII，非 ASCII 字母变体检索由不敏感变回敏感）
    let pattern = if query.is_empty() {
        String::new()
    } else {
        format!(
            "%{}%",
            query
                .replace('~', "~~")
                .replace('%', "~%")
                .replace('_', "~_")
        )
    };
    let mut statement = conn.prepare(
        "SELECT id FROM community_posts
         WHERE (?1 IS NULL OR kind = ?1)
           AND (?2 = '' OR uni_lower(title || ' ' || body) LIKE uni_lower(?2) ESCAPE '~')
         ORDER BY id DESC LIMIT 500",
    )?;
    let ids = statement
        .query_map(params![kind, pattern], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let mut result = Vec::new();
    for id in ids {
        result.push(post_row(conn, id)?);
    }
    Ok(result)
}

pub fn create_post<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    input: PostInput,
) -> Result<PostView> {
    let title = input.title.trim();
    let body = input.body.trim();
    if title.is_empty() || title.len() > 200 || body.is_empty() || body.len() > 20_000 {
        return Err(BackendError::invalid(
            "invalid_post_content",
            "标题须为 1 到 200 字节，正文须为 1 到 20000 字节",
        ));
    }
    if !["discussion", "peer_review", "resource"].contains(&input.kind.as_str()) {
        return Err(BackendError::invalid("invalid_post_kind", "讨论类型无效"));
    }
    let store = state.store()?;
    let project_title = if input.kind == "peer_review" {
        let project_id = input
            .project_id
            .ok_or_else(|| BackendError::invalid("project_required", "项目互评必须关联真实项目"))?;
        store
            .collection("projects")?
            .value
            .as_array()
            .into_iter()
            .flatten()
            .find(|project| project.get("id").and_then(Value::as_i64) == Some(project_id))
            .and_then(|project| project.get("title").and_then(Value::as_str))
            .map(str::to_string)
            .ok_or_else(|| BackendError::new("project_not_found", "关联项目不存在"))
            .map(Some)?
    } else {
        if input.project_id.is_some() {
            return Err(BackendError::invalid(
                "invalid_project_link",
                "只有项目互评可以关联项目",
            ));
        }
        None
    };
    // 34#11：IMMEDIATE——与 25#18 注释宣称一致（DEFERRED 读快照计数仍可超配额）
    let tx = store.write_transaction()?;
    // 25#18：配额计数与插入同事务，关闭 check-then-insert 并发窗口
    store.ensure_quota_in_tx(&tx, "threads", 1)?;
    let timestamp = now()?;
    tx.execute(
        "INSERT INTO community_posts (title, body, kind, project_id, project_title, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)",
        params![title, body, input.kind, input.project_id, project_title, timestamp],
    )?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "community.post_created",
        json!({"postId": id, "kind": input.kind}),
    )?;
    tx.commit()?;
    emit(app, &event);
    post_row(&store.conn, id)
}

pub fn reply<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    post_id: i64,
    body: String,
) -> Result<ReplyView> {
    let body = body.trim();
    if body.is_empty() || body.len() > 10_000 {
        return Err(BackendError::invalid(
            "invalid_reply",
            "回复须为 1 到 10000 字节",
        ));
    }
    let store = state.store()?;
    let exists: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM community_posts WHERE id = ?1)",
        params![post_id],
        |row| row.get(0),
    )?;
    if !exists {
        return Err(BackendError::new("not_found", "讨论不存在"));
    }
    // 03#10：回复同样计入 threads 配额，与发帖共用同一上限；
    // 25#18：计数与插入同事务，关闭 check-then-insert 并发窗口。
    // 34#11：IMMEDIATE——同发帖
    let tx = store.write_transaction()?;
    store.ensure_quota_in_tx(&tx, "threads", 1)?;
    tx.execute(
        "INSERT INTO community_replies (post_id, body, created_at) VALUES (?1, ?2, ?3)",
        params![post_id, body, now()?],
    )?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "community.reply_created",
        json!({"postId": post_id, "replyId": id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    reply_row(&store.conn, id)
}

pub fn toggle_reaction<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    post_id: i64,
    kind: String,
) -> Result<PostView> {
    if !["like", "bookmark"].contains(&kind.as_str()) {
        return Err(BackendError::invalid("invalid_reaction", "反应类型无效"));
    }
    let store = state.store()?;
    let exists: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM community_posts WHERE id = ?1)",
        params![post_id],
        |row| row.get(0),
    )?;
    if !exists {
        return Err(BackendError::new("not_found", "讨论不存在"));
    }
    let tx = store.conn.unchecked_transaction()?;
    let active: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM community_reactions WHERE post_id = ?1 AND kind = ?2)",
        params![post_id, kind],
        |row| row.get(0),
    )?;
    if active {
        tx.execute(
            "DELETE FROM community_reactions WHERE post_id = ?1 AND kind = ?2",
            params![post_id, kind],
        )?;
    } else {
        tx.execute(
            "INSERT INTO community_reactions (post_id, kind, created_at) VALUES (?1, ?2, ?3)",
            params![post_id, kind, now()?],
        )?;
    }
    let event = Store::event(
        &tx,
        "community.reaction_changed",
        json!({"postId": post_id, "kind": kind, "active": !active}),
    )?;
    tx.commit()?;
    emit(app, &event);
    post_row(&store.conn, post_id)
}

pub fn accept_reply<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    reply_id: i64,
    accepted: bool,
) -> Result<ReplyView> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let post_id: i64 = tx
        .query_row(
            "SELECT post_id FROM community_replies WHERE id = ?1",
            params![reply_id],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "回复不存在"))?;
    if accepted {
        tx.execute(
            "UPDATE community_replies SET accepted = 0 WHERE post_id = ?1",
            params![post_id],
        )?;
    }
    tx.execute(
        "UPDATE community_replies SET accepted = ?1 WHERE id = ?2",
        params![accepted, reply_id],
    )?;
    let event = Store::event(
        &tx,
        "community.reply_acceptance_changed",
        json!({"postId": post_id, "replyId": reply_id, "accepted": accepted}),
    )?;
    tx.commit()?;
    emit(app, &event);
    reply_row(&store.conn, reply_id)
}

fn group_row(conn: &Connection, id: i64) -> Result<GroupView> {
    let (name, topic, schedule, created_at): (String, String, String, String) = conn.query_row(
        "SELECT name, topic, schedule, created_at FROM community_groups WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
    )?;
    let joined = conn.query_row("SELECT EXISTS(SELECT 1 FROM community_group_members WHERE group_id = ?1 AND left_at IS NULL)", params![id], |row| row.get(0))?;
    let member_count = conn.query_row(
        "SELECT COUNT(*) FROM community_group_members WHERE group_id = ?1 AND left_at IS NULL",
        params![id],
        |row| row.get(0),
    )?;
    Ok(GroupView {
        id,
        name,
        topic,
        schedule,
        joined,
        member_count,
        created_at,
    })
}

fn groups(conn: &Connection) -> Result<Vec<GroupView>> {
    let mut statement =
        conn.prepare("SELECT id FROM community_groups ORDER BY id DESC LIMIT 100")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    ids.into_iter().map(|id| group_row(conn, id)).collect()
}

pub fn create_group<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    input: GroupInput,
) -> Result<GroupView> {
    if input.name.trim().is_empty()
        || input.name.len() > 100
        || input.topic.trim().is_empty()
        || input.topic.len() > 1000
        || input.schedule.trim().is_empty()
        || input.schedule.len() > 100
    {
        return Err(BackendError::invalid(
            "invalid_group",
            "小组名称、主题与常规时间安排不能为空，且不能超过长度限制",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO community_groups (name, topic, schedule, created_at) VALUES (?1, ?2, ?3, ?4)",
        params![
            input.name.trim(),
            input.topic.trim(),
            input.schedule.trim(),
            now()?
        ],
    )?;
    let id = tx.last_insert_rowid();
    let event = Store::event(&tx, "community.group_created", json!({"groupId": id}))?;
    tx.commit()?;
    emit(app, &event);
    group_row(&store.conn, id)
}

pub fn toggle_group<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    id: i64,
    joined: bool,
) -> Result<GroupView> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let exists: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM community_groups WHERE id = ?1)",
        params![id],
        |row| row.get(0),
    )?;
    if !exists {
        return Err(BackendError::new("not_found", "小组不存在"));
    }
    if joined {
        tx.execute("INSERT INTO community_group_members (group_id, joined_at, left_at) VALUES (?1, ?2, NULL) ON CONFLICT(group_id) DO UPDATE SET left_at = NULL", params![id, now()?])?;
    } else {
        tx.execute("UPDATE community_group_members SET left_at = ?1 WHERE group_id = ?2 AND left_at IS NULL", params![now()?, id])?;
    }
    let event = Store::event(
        &tx,
        "community.group_membership_changed",
        json!({"groupId": id, "joined": joined}),
    )?;
    tx.commit()?;
    emit(app, &event);
    group_row(&store.conn, id)
}

fn room_row(conn: &Connection, id: i64) -> Result<RoomView> {
    let (group_id, title, topic, start_at, duration_minutes, status, created_at): (Option<i64>, String, String, String, u32, String, String) = conn.query_row(
        "SELECT group_id, title, topic, start_at, duration_minutes, status, created_at FROM community_rooms WHERE id = ?1",
        params![id],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?)),
    )?;
    let reserved = conn.query_row("SELECT EXISTS(SELECT 1 FROM community_reservations WHERE room_id = ?1 AND cancelled_at IS NULL)", params![id], |row| row.get(0))?;
    let reservation_count = conn.query_row(
        "SELECT COUNT(*) FROM community_reservations WHERE room_id = ?1 AND cancelled_at IS NULL",
        params![id],
        |row| row.get(0),
    )?;
    let reminder_at = conn
        .query_row(
            "SELECT remind_at FROM community_reminders WHERE room_id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?;
    Ok(RoomView {
        id,
        group_id,
        title,
        topic,
        start_at,
        duration_minutes,
        status,
        reserved,
        reservation_count,
        reminder_at,
        created_at,
    })
}

fn rooms(conn: &Connection) -> Result<Vec<RoomView>> {
    let mut statement =
        conn.prepare("SELECT id FROM community_rooms ORDER BY start_at DESC LIMIT 100")?;
    let ids = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    ids.into_iter().map(|id| room_row(conn, id)).collect()
}

/// 03#13-12：[start, end) 与现有排期重叠时拒绝——同一时段只保留一个学习预约
fn ensure_no_overlap(
    conn: &Connection,
    exclude: Option<i64>,
    start: OffsetDateTime,
    end: OffsetDateTime,
) -> Result<()> {
    let mut statement = conn.prepare(
        "SELECT id, start_at, duration_minutes FROM community_rooms WHERE status = 'scheduled'",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, i64>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, i64>(2)?,
        ))
    })?;
    for row in rows {
        let (room_id, other_start, duration) = row?;
        if Some(room_id) == exclude {
            continue;
        }
        // 34#19：损坏的 start_at 行跳过而非上抛——migrate 31#33 对不可解析行
        // 保持原样，一条毒行不该阻断全部房间创建与预约（到期判定独立不受影响）
        let Ok(other_start) = parse_time(&other_start) else {
            continue;
        };
        let other_end = other_start + Duration::minutes(duration.max(0));
        if start < other_end && other_start < end {
            return Err(BackendError::new(
                "room_time_conflict",
                "该时段已有学习预约，请选择其他时间",
            ));
        }
    }
    Ok(())
}

pub fn create_room<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    input: RoomInput,
) -> Result<RoomView> {
    if input.title.trim().is_empty()
        || input.title.len() > 200
        || input.topic.trim().is_empty()
        || input.topic.len() > 2000
        || !(10..=240).contains(&input.duration_minutes)
    {
        return Err(BackendError::invalid(
            "invalid_room",
            "房间标题、主题与时长无效；时长须为 10 到 240 分钟",
        ));
    }
    let start = parse_time(&input.start_at)?;
    if start <= OffsetDateTime::now_utc() {
        return Err(BackendError::invalid(
            "invalid_room_time",
            "预约开始时间必须晚于当前时间",
        ));
    }
    let store = state.store()?;
    if let Some(group_id) = input.group_id {
        let exists: bool = store.conn.query_row(
            "SELECT EXISTS(SELECT 1 FROM community_groups WHERE id = ?1)",
            params![group_id],
            |row| row.get(0),
        )?;
        if !exists {
            return Err(BackendError::new("not_found", "关联小组不存在"));
        }
    }
    // 25#15：入库统一 UTC Z 形态，不保留调用方输入的时区偏移
    let start_at = normalize_time(&input.start_at)?;
    // 31#35：重叠检查移入写事务——原先在 tx 外做 TOCTOU 检查，并发创建可
    // 各自通过后双双提交；对齐 reserve 的 IMMEDIATE 事务内检查
    let tx = store.write_transaction()?;
    // 03#13-12：新房间先与现有排期比对，重叠即拒绝
    ensure_no_overlap(
        &tx,
        None,
        start,
        start + Duration::minutes(input.duration_minutes as i64),
    )?;
    tx.execute("INSERT INTO community_rooms (group_id, title, topic, start_at, duration_minutes, status, created_at) VALUES (?1, ?2, ?3, ?4, ?5, 'scheduled', ?6)", params![input.group_id, input.title.trim(), input.topic.trim(), start_at, input.duration_minutes, now()?])?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "community.room_created",
        json!({"roomId": id, "startAt": start_at}),
    )?;
    tx.commit()?;
    emit(app, &event);
    room_row(&store.conn, id)
}

pub fn reserve<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    id: i64,
) -> Result<RoomView> {
    let store = state.store()?;
    let (group_id, start_at, duration, status): (Option<i64>, String, i64, String) = store
        .conn
        .query_row(
            "SELECT group_id, start_at, duration_minutes, status FROM community_rooms WHERE id = ?1",
            params![id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("not_found", "学习预约不存在"))?;
    if status != "scheduled" || parse_time(&start_at)? <= OffsetDateTime::now_utc() {
        return Err(BackendError::new(
            "room_not_available",
            "已取消或过期的时间不能预约",
        ));
    }
    // 34#11：IMMEDIATE——31#35 只把 create_room 挪进写事务，预约的重叠检查
    // 仍在 DEFERRED 读快照上做（注释与实现不符）
    let tx = store.write_transaction()?;
    let existing: Option<Option<String>> = tx
        .query_row(
            "SELECT cancelled_at FROM community_reservations WHERE room_id = ?1",
            params![id],
            |row| row.get(0),
        )
        .optional()?;
    if existing == Some(None) {
        return room_row(&tx, id);
    }
    // 03#13-12：预约时段不得与现有排期重叠（跳过本房间自身）
    let window_start = parse_time(&start_at)?;
    ensure_no_overlap(
        &tx,
        Some(id),
        window_start,
        window_start + Duration::minutes(duration.max(0)),
    )?;
    if let Some(group_id) = group_id {
        let joined: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM community_group_members WHERE group_id = ?1 AND left_at IS NULL)", params![group_id], |row| row.get(0))?;
        if !joined {
            return Err(BackendError::new(
                "group_not_joined",
                "请先加入关联小组，再预约",
            ));
        }
    }
    let timestamp = now()?;
    // 25#15：提醒时间统一 UTC Z 形态——claim 用字符串 <= 与 now() 的 Z 串比较
    let remind_at = std::cmp::max(
        parse_time(&start_at)? - Duration::minutes(15),
        OffsetDateTime::now_utc(),
    )
    .to_offset(time::UtcOffset::UTC)
    .format(&Rfc3339)?;
    tx.execute("INSERT INTO community_reservations (room_id, reserved_at, cancelled_at) VALUES (?1, ?2, NULL) ON CONFLICT(room_id) DO UPDATE SET reserved_at = excluded.reserved_at, cancelled_at = NULL", params![id, timestamp])?;
    tx.execute("INSERT INTO community_reminders (room_id, remind_at, created_at) VALUES (?1, ?2, ?3) ON CONFLICT(room_id) DO UPDATE SET remind_at = excluded.remind_at, delivered_at = NULL", params![id, remind_at, timestamp])?;
    let event = Store::event(
        &tx,
        "community.room_reserved",
        json!({"roomId": id, "remindAt": remind_at}),
    )?;
    tx.commit()?;
    emit(app, &event);
    room_row(&store.conn, id)
}

pub fn cancel_reservation<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    id: i64,
) -> Result<RoomView> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute("UPDATE community_reservations SET cancelled_at = ?1 WHERE room_id = ?2 AND cancelled_at IS NULL", params![now()?, id])?;
    if changed == 0 {
        return Err(BackendError::new("not_reserved", "该学习预约当前未预约"));
    }
    tx.execute(
        "DELETE FROM community_reminders WHERE room_id = ?1",
        params![id],
    )?;
    // 13#14：级联取消“即将开始”通知，否则通知中心永久残留已取消安排
    tx.execute(
        "DELETE FROM notifications WHERE id = ?1",
        params![format!("room-{id}")],
    )?;
    let event = Store::event(
        &tx,
        "community.reservation_cancelled",
        json!({"roomId": id}),
    )?;
    tx.commit()?;
    emit(app, &event);
    room_row(&store.conn, id)
}

pub fn cancel_room<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
    id: i64,
) -> Result<RoomView> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    let timestamp = now()?;
    let changed = tx.execute(
        "UPDATE community_rooms SET status = 'cancelled' WHERE id = ?1 AND status = 'scheduled'",
        params![id],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "room_not_available",
            "房间不存在或已取消",
        ));
    }
    tx.execute("UPDATE community_reservations SET cancelled_at = ?1 WHERE room_id = ?2 AND cancelled_at IS NULL", params![timestamp, id])?;
    tx.execute(
        "DELETE FROM community_reminders WHERE room_id = ?1",
        params![id],
    )?;
    // 13#14：同步取消“即将开始”通知
    tx.execute(
        "DELETE FROM notifications WHERE id = ?1",
        params![format!("room-{id}")],
    )?;
    let event = Store::event(&tx, "community.room_cancelled", json!({"roomId": id}))?;
    tx.commit()?;
    emit(app, &event);
    room_row(&store.conn, id)
}

pub fn claim_due_reminders<R: Runtime>(
    app: &AppHandle<R>,
    state: &CommunityState,
) -> Result<Vec<ReminderView>> {
    let store = state.store()?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    let mut statement = tx.prepare("SELECT r.id, r.room_id, rooms.title, rooms.start_at, r.remind_at FROM community_reminders r JOIN community_rooms rooms ON rooms.id = r.room_id JOIN community_reservations reservations ON reservations.room_id = r.room_id AND reservations.cancelled_at IS NULL WHERE r.remind_at <= ?1 AND r.delivered_at IS NULL AND rooms.status = 'scheduled' ORDER BY r.id")?;
    let rows = statement
        .query_map(params![timestamp], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    let mut events = Vec::new();
    let mut reminders = Vec::new();
    for (id, room_id, title, start_at, remind_at) in rows {
        tx.execute(
            "UPDATE community_reminders SET delivered_at = ?1 WHERE id = ?2",
            params![timestamp, id],
        )?;
        events.push(Store::event(
            &tx,
            "community.reminder_due",
            json!({"reminderId": id, "roomId": room_id, "title": title, "startAt": start_at}),
        )?);
        reminders.push(ReminderView {
            id,
            room_id,
            title,
            start_at,
            remind_at,
            delivered_at: Some(timestamp.clone()),
        });
    }
    tx.commit()?;
    for event in events {
        emit(app, &event);
    }
    Ok(reminders)
}

fn count(conn: &Connection, table: &str, condition: &str) -> Result<u32> {
    Ok(conn.query_row(
        &format!("SELECT COUNT(*) FROM {table} WHERE {condition}"),
        [],
        |row| row.get(0),
    )?)
}

pub fn overview(
    state: &CommunityState,
    kind: Option<&str>,
    query: Option<&str>,
) -> Result<CommunityOverview> {
    let store = state.store()?;
    Ok(CommunityOverview {
        posts: posts(&store.conn, kind, query)?,
        groups: groups(&store.conn)?,
        rooms: rooms(&store.conn)?,
        stats: CommunityStats {
            posts: count(&store.conn, "community_posts", "1")?,
            replies: count(&store.conn, "community_replies", "1")?,
            reviews: count(&store.conn, "community_posts", "kind = 'peer_review'")?,
            accepted_replies: count(&store.conn, "community_replies", "accepted = 1")?,
            likes: count(&store.conn, "community_reactions", "kind = 'like'")?,
            bookmarks: count(&store.conn, "community_reactions", "kind = 'bookmark'")?,
            joined_groups: count(&store.conn, "community_group_members", "left_at IS NULL")?,
            active_reservations: count(
                &store.conn,
                "community_reservations",
                "cancelled_at IS NULL",
            )?,
        },
        now: now()?,
    })
}

#[cfg(test)]
mod tests;
