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
    let configuration = json!({"name": "默认", "new": {"delays": [1, 10], "ints": [1, 4], "initialFactor": 2500, "perDay": 20, "bury": false}, "rev": {"perDay": 200, "ease4": 1.3, "ivlFct": 1.0, "maxIvl": 36500, "bury": false}, "lapse": {"delays": [10], "mult": 0.0, "minInt": 1, "leechFails": 8}});
    {
        let connection = Connection::open(&temporary.0)?;
        connection.execute_batch(
            "CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null, ver integer not null, dty integer not null, usn integer not null, ls integer not null, conf text not null, models text not null, decks text not null, dconf text not null, tags text not null);
             CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null, usn integer not null, tags text not null, flds text not null, sfld text not null, csum integer not null, flags integer not null, data text not null);
             CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null, mod integer not null, usn integer not null, type integer not null, queue integer not null, due integer not null, ivl integer not null, factor integer not null, reps integer not null, lapses integer not null, left integer not null, odue integer not null, odid integer not null, flags integer not null, data text not null);
             CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null, ivl integer not null, lastIvl integer not null, factor integer not null, time integer not null, type integer not null);
             CREATE TABLE graves (oid integer not null, type integer not null, usn integer not null);",
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
            let guid = format!(
                "{:x}",
                Sha256::digest(format!("{note_id}:{}", card.front).as_bytes())
            )
            .chars()
            .take(10)
            .collect::<String>();
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
                "INSERT INTO cards VALUES (?1, ?2, ?3, 0, ?4, 0, 0, 0, ?5, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                params![note_id + 1_000_000, note_id, deck_id, now, index as i64],
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
