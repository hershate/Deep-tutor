use super::super::error::{BackendError, Result};
use super::super::learning::LearningState;
use super::formats::escape_html;
use super::ExportDocument;
use rusqlite::{params, Connection};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use time::OffsetDateTime;

static TEMP_ID: AtomicU64 = AtomicU64::new(0);

struct TempDatabase(PathBuf);

impl Drop for TempDatabase {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

/// 复习卡的 Anki due（11#3、15#4）。Anki schema 中 queue=2（复习）卡的
/// due 是「自 collection crt 起的天数」，而 col.crt 写入的是导出时刻
/// （下方 INSERT INTO col 的 ?1=now），因此这里返回到期时刻与导出时刻的
/// 差值天数（个位数量级，负值表示已过期）。解析失败或为空时回退 0
/// （今天到期），卡片仍进入复习队列。
fn anki_due_day(due_at: &str, now: i64) -> Result<i64> {
    if due_at.is_empty() {
        return Ok(0);
    }
    Ok(
        time::OffsetDateTime::parse(due_at, &time::format_description::well_known::Rfc3339)
            .map(|moment| (moment.unix_timestamp() - now) / 86_400)
            .unwrap_or(0),
    )
}

fn stable_id(value: &str) -> i64 {
    let digest = Sha256::digest(value.as_bytes());
    i64::from(u32::from_be_bytes([digest[0], digest[1], digest[2], digest[3]]) & 0x7fff_ffff)
}

fn field(value: &str) -> String {
    escape_html(value).replace('\n', "<br>")
}

pub fn render(state: &LearningState, document: &ExportDocument) -> Result<Vec<u8>> {
    if document.cards.is_empty() {
        return Err(BackendError::new(
            "empty_export",
            "当前范围没有可导出的复习卡",
        ));
    }
    let root = state
        .workspace_path()
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join("exports");
    fs::create_dir_all(&root)?;
    let temporary = TempDatabase(root.join(format!(
        "anki-{}-{}.db",
        std::process::id(),
        TEMP_ID.fetch_add(1, Ordering::Relaxed)
    )));
    let now = OffsetDateTime::now_utc().unix_timestamp();
    let now_ms = now
        .checked_mul(1000)
        .ok_or_else(|| BackendError::new("export_failed", "Anki 时间戳无效"))?;
    let model_id = stable_id("Deep Tutor Basic v1");
    let deck_id = stable_id(&format!("Deep Tutor deck:{}", document.title));
    let model = json!({
        "id": model_id.to_string(), "name": "Deep Tutor Basic", "type": 0, "mod": now, "usn": 0,
        "tags": [], "did": deck_id, "vers": [], "req": [[0, "all", [0]]],
        "flds": [
            {"name": "正面", "ord": 0, "sticky": false, "rtl": false, "font": "Arial", "size": 20, "media": []},
            {"name": "背面", "ord": 1, "sticky": false, "rtl": false, "font": "Arial", "size": 20, "media": []}
        ],
        "tmpls": [{"name": "正面 → 背面", "ord": 0, "qfmt": "{{正面}}", "afmt": "{{FrontSide}}<hr id=answer>{{背面}}", "did": null, "bqfmt": "", "bafmt": ""}]
    });
    let deck = json!({"id": deck_id, "name": document.title, "desc": "由知舟原生后端导出的真实卡片", "mod": now, "usn": 0, "collapsed": false, "newToday": [0, 0], "revToday": [0, 0], "lrnToday": [0, 0], "timeToday": [0, 0], "dyn": 0, "extendNew": 10, "extendRev": 50, "conf": 1});
    // 11#5：dconf 覆盖官方 2.1 scheduler 读取的常用键（order/separate/fuzz/
    // minSpace/leechAction/maxTaken/timer/replayq），避免新版 Anki 迁移时回退默认
    let configuration = json!({
        "id": 1, "name": "默认", "mod": 0, "usn": 0, "maxTaken": 60, "autoplay": true, "timer": 0, "replayq": true,
        "new": {"delays": [1, 10], "ints": [1, 4], "initialFactor": 2500, "perDay": 20, "bury": false, "separate": true, "order": 1},
        "rev": {"perDay": 200, "ease4": 1.3, "ivlFct": 1.0, "maxIvl": 36500, "bury": false, "fuzz": 0.05, "minSpace": 1},
        "lapse": {"delays": [10], "mult": 0.0, "minInt": 1, "leechFails": 8, "leechAction": 0}
    });
    {
        let connection = Connection::open(&temporary.0)?;
        connection.execute_batch(
            "CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null, ver integer not null, dty integer not null, usn integer not null, ls integer not null, conf text not null, models text not null, decks text not null, dconf text not null, tags text not null);
             CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null, usn integer not null, tags text not null, flds text not null, sfld text not null, csum integer not null, flags integer not null, data text not null);
             CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null, mod integer not null, usn integer not null, type integer not null, queue integer not null, due integer not null, ivl integer not null, factor integer not null, reps integer not null, lapses integer not null, left integer not null, odue integer not null, odid integer not null, flags integer not null, data text not null);
             CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null, ivl integer not null, lastIvl integer not null, factor integer not null, time integer not null, type integer not null);
             CREATE TABLE graves (oid integer not null, type integer not null, usn integer not null);
             CREATE INDEX ix_cards_nid ON cards (nid);
             CREATE INDEX ix_cards_sched ON cards (did, queue, due);
             CREATE INDEX ix_revlog_cid ON revlog (cid);
             CREATE INDEX ix_notes_csum ON notes (csum);",
        )?;
        connection.execute(
            "INSERT INTO col VALUES (1, ?1, ?2, ?2, 11, 0, 0, 0, ?3, ?4, ?5, ?6, '{}')",
            params![now, now_ms, json!({"nextPos": 1, "estTimes": true, "activeDecks": [deck_id], "sortType": "noteFld", "sortBackwards": false, "addToCur": true, "curDeck": deck_id, "newSpread": 0, "dueCounts": true, "curModel": model_id.to_string(), "collapseTime": 1200}).to_string(), json!({model_id.to_string(): model}).to_string(), json!({deck_id.to_string(): deck}).to_string(), json!({"1": configuration}).to_string()],
        )?;
        for (index, card) in document.cards.iter().enumerate() {
            let note_id = now_ms + index as i64;
            let checksum = Sha256::digest(card.front.as_bytes());
            let checksum = i64::from(u32::from_be_bytes([
                checksum[0],
                checksum[1],
                checksum[2],
                checksum[3],
            ]));
            // 11#4：guid 锚定稳定卡片 id + 正面内容（复习卡用 review 表 id，
            // 其余来源用正面内容哈希），再次导出同一张卡 guid 不变，
            // Anki 按 guid 更新而不是插入整套重复笔记
            let anchor = if card.id != 0 {
                format!("{}:{}", card.id, card.front)
            } else {
                // B-6：无稳定 id 的卡必须把背面纳入锚——多条未命名笔记
                // front 恒为「未命名笔记」，仅按 front 锚定会 guid 碰撞，
                // Anki 按 guid 合并导致内容静默丢失；内容全同的卡合并
                // 无损失，front 同 back 不同的卡因此得以区分
                let back_digest = Sha256::digest(card.back.as_bytes());
                format!(
                    "content:{}:{:x}",
                    card.front,
                    u32::from_be_bytes([
                        back_digest[0],
                        back_digest[1],
                        back_digest[2],
                        back_digest[3]
                    ])
                )
            };
            let guid = format!("{:x}", Sha256::digest(anchor.as_bytes()))
                .chars()
                .take(10)
                .collect::<String>();
            // 11#3：SM-2 调度迁移——有复习记录的卡按到期日/间隔/难度写入
            // cards 表，而不是全部清零回新卡队列
            let (card_type, queue, due, ivl, factor) = if card.repetitions == 0 {
                (0, 0, index as i64, 0, (card.ease * 1000.0) as i64)
            } else if card.suspended {
                (
                    2,
                    -1,
                    anki_due_day(&card.due_at, now)?,
                    card.interval_days.round() as i64,
                    (card.ease * 1000.0) as i64,
                )
            } else {
                (
                    2,
                    2,
                    anki_due_day(&card.due_at, now)?,
                    card.interval_days.round() as i64,
                    (card.ease * 1000.0) as i64,
                )
            };
            connection.execute(
                "INSERT INTO notes VALUES (?1, ?2, ?3, ?4, 0, ?5, ?6, ?7, ?8, 0, '')",
                params![
                    note_id,
                    guid,
                    model_id,
                    now,
                    format!(" {} ", card.tag.replace([' ', '\n', '\r'], "_")),
                    format!("{}\u{1f}{}", field(&card.front), field(&card.back)),
                    card.front,
                    checksum
                ],
            )?;
            connection.execute(
                "INSERT INTO cards VALUES (?1, ?2, ?3, 0, ?4, 0, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 0, 0, 0, 0, '')",
                params![
                    note_id + 1_000_000,
                    note_id,
                    deck_id,
                    now,
                    card_type,
                    queue,
                    due,
                    ivl,
                    factor,
                    card.repetitions as i64,
                    card.lapses as i64
                ],
            )?;
        }
    }
    let bytes = fs::read(&temporary.0)?;
    let mut archive = Vec::new();
    {
        let mut writer = zip::ZipWriter::new(std::io::Cursor::new(&mut archive));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        writer.start_file("collection.anki2", options)?;
        writer.write_all(&bytes)?;
        writer.start_file("media", options)?;
        writer.write_all(b"{}")?;
        writer.finish()?;
    }
    Ok(archive)
}
