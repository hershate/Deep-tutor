use super::super::error::{BackendError, Result};
use super::super::learning::LearningState;
use super::super::store::Store;
use super::super::{materials, plugins};
use super::archive::{FileEntry, OpenBackup, TABLES};
use rusqlite::{params, params_from_iter, Connection, OptionalExtension};
use serde_json::Value;
use sha2::Digest;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
struct Column {
    name: String,
    kind: String,
    primary: bool,
}

type Row = BTreeMap<String, Value>;
type IdMap = HashMap<&'static str, HashMap<String, Value>>;

struct Transfer {
    tables: BTreeMap<&'static str, Vec<Row>>,
    schemas: BTreeMap<&'static str, Vec<Column>>,
    maps: IdMap,
    reused: HashSet<String>,
    plugin_manifests: HashMap<String, String>,
    counts: BTreeMap<String, u64>,
}

fn key(value: &Value) -> Result<String> {
    serde_json::to_string(value).map_err(Into::into)
}

fn invalid_record() -> BackendError {
    BackendError::invalid(
        "invalid_backup_record",
        "备份记录类型、标识或内容不符合当前结构",
    )
}

fn read_value(row: &rusqlite::Row<'_>, index: usize, kind: &str) -> rusqlite::Result<Value> {
    use rusqlite::types::ValueRef;
    Ok(match row.get_ref(index)? {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(value) => Value::from(value),
        ValueRef::Real(value) => Value::from(value),
        ValueRef::Text(value) => Value::String(
            std::str::from_utf8(value)
                .map_err(|_| {
                    rusqlite::Error::InvalidColumnType(
                        index,
                        "invalid UTF-8".into(),
                        rusqlite::types::Type::Text,
                    )
                })?
                .to_owned(),
        ),
        ValueRef::Blob(_) => {
            return Err(rusqlite::Error::InvalidColumnType(
                index,
                kind.into(),
                rusqlite::types::Type::Blob,
            ))
        }
    })
}

fn schema(conn: &Connection, table: &str) -> Result<Vec<Column>> {
    let mut statement = conn.prepare(&format!("PRAGMA table_info(\"{table}\")"))?;
    let rows = statement
        .query_map([], |row| {
            Ok(Column {
                name: row.get(1)?,
                kind: row.get::<_, String>(2)?.to_ascii_uppercase(),
                primary: row.get::<_, i64>(5)? == 1,
            })
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    Ok(rows)
}

fn read_tables(source: &Connection, target: &Connection) -> Result<Transfer> {
    let mut tables = BTreeMap::new();
    let mut schemas = BTreeMap::new();
    for table in TABLES {
        let columns = schema(target, table)?;
        let source_columns = schema(source, table)?;
        if columns
            .iter()
            .map(|column| &column.name)
            .collect::<BTreeSet<_>>()
            != source_columns.iter().map(|column| &column.name).collect()
        {
            return Err(BackendError::invalid(
                "unsupported_backup_schema",
                "备份表字段与当前版本不一致",
            ));
        }
        let mut statement = source.prepare(&format!("SELECT * FROM \"{table}\" ORDER BY rowid"))?;
        let rows = statement
            .query_map([], |row| {
                let mut values = BTreeMap::new();
                for (index, column) in columns.iter().enumerate() {
                    values.insert(column.name.clone(), read_value(row, index, &column.kind)?);
                }
                Ok(values)
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        drop(statement);
        tables.insert(*table, rows);
        schemas.insert(*table, columns);
    }
    Ok(Transfer {
        tables,
        schemas,
        maps: IdMap::new(),
        reused: HashSet::new(),
        plugin_manifests: HashMap::new(),
        counts: BTreeMap::new(),
    })
}

fn primary<'a>(transfer: &'a Transfer, table: &str) -> Option<&'a Column> {
    let columns = transfer.schemas.get(table)?;
    let primary = columns
        .iter()
        .filter(|column| column.primary)
        .collect::<Vec<_>>();
    if primary.len() == 1 {
        primary.first().copied()
    } else {
        None
    }
}

fn sql_value(value: &Value) -> Result<rusqlite::types::Value> {
    match value {
        Value::Null => Ok(rusqlite::types::Value::Null),
        Value::Bool(value) => Ok((*value).into()),
        Value::Number(value) if value.is_i64() || value.is_u64() => {
            Ok(value.as_i64().ok_or_else(invalid_record)?.into())
        }
        Value::Number(value) => Ok(value.as_f64().ok_or_else(invalid_record)?.into()),
        Value::String(value) => Ok(value.clone().into()),
        _ => Err(invalid_record()),
    }
}

fn exists(conn: &Connection, table: &str, column: &str, value: &Value) -> Result<bool> {
    Ok(conn.query_row(
        &format!("SELECT EXISTS(SELECT 1 FROM \"{table}\" WHERE \"{column}\" = ?1)"),
        params![sql_value(value)?],
        |row| row.get(0),
    )?)
}

fn free_text_id(
    conn: &Connection,
    table: &str,
    column: &str,
    allocated: &HashSet<String>,
) -> Result<Value> {
    for number in 1..1_000_000_u32 {
        let candidate = Value::String(format!("imported-{number}"));
        if !exists(conn, table, column, &candidate)? && !allocated.contains(&key(&candidate)?) {
            return Ok(candidate);
        }
    }
    Err(BackendError::new(
        "backup_id_exhausted",
        "无法分配追加导入标识",
    ))
}

fn prepare_ids(transfer: &mut Transfer, target: &Connection) -> Result<()> {
    for table in TABLES {
        if *table == "kv" {
            continue;
        }
        let Some(primary) = primary(transfer, table).cloned() else {
            continue;
        };
        if reference(table, &primary.name).is_some() {
            continue;
        }
        let mut allocated = HashSet::new();
        let mut rows = std::mem::take(transfer.tables.get_mut(*table).expect("known table"));
        for row in &mut rows {
            let original = row.get(&primary.name).cloned().ok_or_else(invalid_record)?;
            if original.is_null() {
                return Err(invalid_record());
            }
            let original_key = key(&original)?;
            let reuse_key = format!("{table}:{original_key}");
            let mut mapped = original.clone();
            if *table == "materials" {
                if let Some(hash) = row.get("sha256").and_then(Value::as_str) {
                    if let Some(id) = target
                        .query_row(
                            "SELECT id FROM materials WHERE sha256 = ?1",
                            params![hash],
                            |row| row.get::<_, i64>(0),
                        )
                        .optional()?
                    {
                        mapped = Value::from(id);
                        transfer.reused.insert(reuse_key.clone());
                    }
                }
            }
            if *table == "shared_plans" {
                if let Some(hash) = row.get("token_hash").and_then(Value::as_str) {
                    if let Some(id) = target
                        .query_row(
                            "SELECT id FROM shared_plans WHERE token_hash = ?1",
                            params![hash],
                            |row| row.get::<_, i64>(0),
                        )
                        .optional()?
                    {
                        mapped = Value::from(id);
                        transfer.reused.insert(reuse_key.clone());
                    }
                }
            }
            if !transfer.reused.contains(&reuse_key)
                && !["meta", "provider_meta", "revisions", "sync_config"].contains(table)
                && (exists(target, table, &primary.name, &mapped)?
                    || allocated.contains(&key(&mapped)?))
            {
                if primary.kind == "INTEGER" {
                    let mut next = target.query_row(
                        &format!(
                            "SELECT COALESCE(MAX(\"{}\"), 0) + 1 FROM \"{table}\"",
                            primary.name
                        ),
                        [],
                        |row| row.get::<_, i64>(0),
                    )?;
                    while exists(target, table, &primary.name, &Value::from(next))?
                        || allocated.contains(&key(&Value::from(next))?)
                    {
                        next += 1;
                    }
                    mapped = Value::from(next);
                } else {
                    mapped = free_text_id(target, table, &primary.name, &allocated)?;
                }
            }
            allocated.insert(key(&mapped)?);
            transfer
                .maps
                .entry(table)
                .or_default()
                .insert(original_key, mapped.clone());
            row.insert(primary.name.clone(), mapped);
        }
        transfer.tables.insert(table, rows);
    }
    let material_map = transfer.maps.get("materials").cloned().unwrap_or_default();
    let resources = transfer
        .tables
        .get("kv")
        .expect("known table")
        .iter()
        .filter(|row| row.get("namespace").and_then(Value::as_str) == Some("resources"));
    for row in resources {
        let original = row.get("id").cloned().ok_or_else(invalid_record)?;
        let numeric = Value::from(
            original
                .as_str()
                .ok_or_else(invalid_record)?
                .parse::<i64>()
                .map_err(|_| invalid_record())?,
        );
        let mapped = material_map.get(&key(&numeric)?).cloned().ok_or_else(|| {
            BackendError::new("invalid_backup_reference", "资料镜像缺少对应原件记录")
        })?;
        transfer
            .maps
            .entry("kv:resources")
            .or_default()
            .insert(key(&original)?, mapped);
    }
    let mut allocated: HashMap<&str, HashSet<String>> = HashMap::new();
    for row in transfer.tables.get("kv").expect("known table") {
        let namespace = row
            .get("namespace")
            .and_then(Value::as_str)
            .ok_or_else(invalid_record)?;
        if ![
            "tasks",
            "notes",
            "projects",
            "preferences",
            "drafts",
            "resources",
        ]
        .contains(&namespace)
        {
            return Err(invalid_record());
        }
        if namespace == "resources" {
            continue;
        }
        let original = row.get("id").cloned().ok_or_else(invalid_record)?;
        let original_key = key(&original)?;
        let mut mapped = original.clone();
        if namespace != "preferences" {
            let conflict = |value: &Value| -> Result<bool> {
                let id = value
                    .as_str()
                    .map(str::to_string)
                    .or_else(|| value.as_i64().map(|id| id.to_string()))
                    .ok_or_else(invalid_record)?;
                Ok(target.query_row(
                    "SELECT EXISTS(SELECT 1 FROM kv WHERE namespace = ?1 AND id = ?2)",
                    params![namespace, sql_value(value)?],
                    |row| row.get::<_, bool>(0),
                )? || allocated
                    .get(namespace)
                    .is_some_and(|items| items.contains(&id)))
            };
            if conflict(&original)? {
                let mut next = target.query_row(
                    "SELECT COALESCE(MAX(CAST(id AS INTEGER)), 0) + 1 FROM kv WHERE namespace = ?1",
                    params![namespace],
                    |row| row.get::<_, i64>(0),
                )?;
                if namespace == "drafts" {
                    next = target.query_row("SELECT COALESCE(MAX(CAST(substr(id, 19) AS INTEGER)), 0) + 1 FROM kv WHERE namespace = 'drafts'", [], |row| row.get::<_, i64>(0))?;
                }
                loop {
                    let candidate = if namespace == "drafts" {
                        Value::String(format!("deep-tutor-draft-{next}"))
                    } else {
                        Value::from(next)
                    };
                    if !conflict(&candidate)? {
                        mapped = candidate;
                        break;
                    }
                    next += 1;
                }
            }
            let id = mapped
                .as_str()
                .map(str::to_string)
                .or_else(|| mapped.as_i64().map(|id| id.to_string()))
                .ok_or_else(invalid_record)?;
            allocated.entry(namespace).or_default().insert(id);
        }
        transfer
            .maps
            .entry(match namespace {
                "tasks" => "kv:tasks",
                "notes" => "kv:notes",
                "projects" => "kv:projects",
                "drafts" => "kv:drafts",
                _ => "kv:other",
            })
            .or_default()
            .insert(original_key, mapped);
    }
    Ok(())
}

fn map_plain(value: &Value, target: &str, maps: &IdMap) -> Result<Value> {
    let original = if target == "provider_profiles" && value.is_string() {
        let text = value.as_str().unwrap_or_default();
        Value::from(
            text.strip_prefix('p')
                .unwrap_or(text)
                .parse::<i64>()
                .map_err(|_| invalid_record())?,
        )
    } else if target == "learning_plans" && value.is_string() {
        let text = value.as_str().unwrap_or_default();
        if text.is_empty() {
            return Ok(Value::String(String::new()));
        }
        Value::from(
            text.strip_prefix("plan-")
                .unwrap_or(text)
                .parse::<i64>()
                .map_err(|_| invalid_record())?,
        )
    } else if target.starts_with("kv:") && value.is_number() {
        Value::String(value.to_string())
    } else if target == "agent_sessions" && value.is_string() {
        let text = value.as_str().unwrap_or_default();
        Value::from(
            text.strip_prefix("agent-")
                .unwrap_or(text)
                .parse::<i64>()
                .map_err(|_| invalid_record())?,
        )
    } else {
        value.clone()
    };
    let key = key(&original)?;
    let mapped = maps
        .get(target)
        .and_then(|items| items.get(&key))
        .cloned()
        .ok_or_else(|| {
            BackendError::new(
                "invalid_backup_reference",
                &format!("备份关联目标 {target} 缺少记录"),
            )
        })?;
    if target == "provider_profiles" && value.is_string() {
        let id = mapped.as_i64().ok_or_else(invalid_record)?;
        Ok(Value::String(
            if value.as_str().unwrap_or_default().starts_with('p') {
                format!("p{id}")
            } else {
                id.to_string()
            },
        ))
    } else if target == "learning_plans" && value.is_string() {
        Ok(Value::String(format!(
            "plan-{}",
            mapped.as_i64().ok_or_else(invalid_record)?
        )))
    } else {
        Ok(mapped)
    }
}

fn reference(table: &str, column: &str) -> Option<&'static str> {
    if table == "provider_profiles" {
        return None;
    }
    if column == "provider_id" {
        return Some("provider_profiles");
    }
    if column == "plan_id" && table != "learning_plans" {
        return Some("learning_plans");
    }
    if [
        "request_id",
        "grader_request_id",
        "current_request_id",
        "upstream_request_id",
    ]
    .contains(&column)
        && !["provider_requests", "provider_route_runs"].contains(&table)
    {
        return Some("provider_requests");
    }
    match column {
        "project_id" => Some("kv:projects"),
        "note_id" => Some("kv:notes"),
        "task_id" => Some("kv:tasks"),
        "tool_call_id" => Some("agent_tool_calls"),
        "mastered_question_id" => Some("practice_questions"),
        "variant_of" if ["practice_questions", "practice_sets"].contains(&table) => {
            Some(if table == "practice_questions" {
                "practice_questions"
            } else {
                "practice_sets"
            })
        }
        _ => None,
    }
}

fn foreign_keys(conn: &Connection, table: &str) -> Result<Vec<(String, &'static str)>> {
    let mut statement = conn.prepare(&format!("PRAGMA foreign_key_list(\"{table}\")"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(3)?, row.get::<_, String>(2)?))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    drop(statement);
    rows.into_iter()
        .map(|(column, target)| {
            let target = TABLES
                .iter()
                .copied()
                .find(|name| *name == target)
                .ok_or_else(|| {
                    BackendError::invalid("unsupported_backup_schema", "备份外键目标不受支持")
                })?;
            Ok((column, target))
        })
        .collect()
}

fn order(transfer: &Transfer, target: &Connection) -> Result<Vec<&'static str>> {
    let mut dependencies = BTreeMap::new();
    for table in TABLES {
        let mut required = foreign_keys(target, table)?
            .into_iter()
            .map(|(_, target)| target)
            .collect::<BTreeSet<_>>();
        for column in transfer.schemas.get(*table).expect("known schema") {
            if let Some(parent) = reference(table, &column.name) {
                required.insert(parent);
            }
        }
        required.remove(*table);
        dependencies.insert(*table, required);
    }
    let mut remaining = TABLES.iter().copied().collect::<BTreeSet<_>>();
    let mut ordered = Vec::new();
    while !remaining.is_empty() {
        let next = remaining.iter().copied().find(|table| {
            dependencies[*table]
                .iter()
                .all(|dependency| !remaining.contains(dependency))
        });
        let Some(next) = next else {
            return Err(BackendError::new(
                "invalid_backup_references",
                "备份依赖关系存在无法解析的循环",
            ));
        };
        ordered.push(next);
        remaining.remove(next);
    }
    Ok(ordered)
}

fn map_request(value: &Value, maps: &IdMap) -> Result<Value> {
    map_plain(value, "provider_requests", maps).or_else(|error| {
        if error.code == "invalid_backup_reference" {
            map_plain(value, "provider_route_runs", maps)
        } else {
            Err(error)
        }
    })
}

fn rewrite_json(value: &mut Value, table: &str, maps: &IdMap) -> Result<()> {
    match value {
        Value::Array(items) => {
            for item in items {
                rewrite_json(item, table, maps)?;
            }
        }
        Value::Object(object) => {
            let keys = object.keys().cloned().collect::<Vec<_>>();
            for name in keys {
                let target = match name.as_str() {
                    "materialId" | "materialIds" => Some("materials"),
                    "planId" => Some("learning_plans"),
                    "providerId"
                    | "aggregateProviderIds"
                    | "aggregateDefault"
                    | "failover"
                    | "activeProvider" => Some("provider_profiles"),
                    "requestId" | "upstreamRequestId" | "graderRequestId" => {
                        Some("provider_requests")
                    }
                    "setId" | "examId" => Some("practice_sets"),
                    "questionId" => Some("practice_questions"),
                    "answerId" => Some("practice_answers"),
                    "historyId" => Some("review_history"),
                    "cardId" => Some("review_cards"),
                    "projectId" => Some("kv:projects"),
                    "taskId" => Some("kv:tasks"),
                    "noteId" => Some("kv:notes"),
                    "sessionId" => Some(
                        if table.starts_with("agent_") || table.starts_with("event:agent.") {
                            "agent_sessions"
                        } else {
                            "knowledge_sessions"
                        },
                    ),
                    "runId" => Some("agent_runs"),
                    "toolCallId" => Some("agent_tool_calls"),
                    "postId" => Some("community_posts"),
                    "replyId" => Some("community_replies"),
                    "groupId" => Some("community_groups"),
                    "roomId" => Some("community_rooms"),
                    "reminderId" => Some("community_reminders"),
                    "artifactId" => Some("agent_artifacts"),
                    "approvalId" => Some("agent_approvals"),
                    "shareId" => Some("shared_plans"),
                    "pluginId" => Some("plugins"),
                    "eventId" => Some("events"),
                    "jobId" => Some("material_jobs"),
                    _ => None,
                };
                if name == "snapshot" {
                    if let Some(sources) = object.get_mut(&name).and_then(Value::as_array_mut) {
                        for source in sources {
                            if let Some(id) = source.get("id").cloned() {
                                source["id"] = map_plain(&id, "materials", maps)?;
                            }
                        }
                    }
                }
                if name == "tool_calls_json" {
                    continue;
                }
                if let Some(target) = target {
                    if let Some(item) = object.get_mut(&name) {
                        if item.is_array() {
                            for item in item.as_array_mut().expect("array") {
                                *item = if target == "provider_requests" {
                                    map_request(item, maps)?
                                } else {
                                    map_plain(item, target, maps)?
                                };
                            }
                        } else if !item.is_null() {
                            *item = if target == "provider_requests" {
                                map_request(item, maps)?
                            } else {
                                map_plain(item, target, maps)?
                            };
                        }
                    }
                } else if let Some(item) = object.get_mut(&name) {
                    rewrite_json(item, table, maps)?;
                }
            }
            if table == "provider_requests" || table == "agent_messages" {
                if let Some(calls) = object.get_mut("toolCalls").and_then(Value::as_array_mut) {
                    for call in calls {
                        if let Some(id) = call.get("id").cloned() {
                            call["id"] = map_plain(&id, "agent_tool_calls", maps)?;
                        }
                    }
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn rewrite_json_field(row: &mut Row, table: &str, column: &str, maps: &IdMap) -> Result<()> {
    if matches!(row.get(column), Some(Value::Null) | None) {
        return Ok(());
    }
    let text = row
        .get(column)
        .and_then(Value::as_str)
        .ok_or_else(invalid_record)?;
    let mut value: Value = serde_json::from_str(text).map_err(|_| invalid_record())?;
    if table == "events" {
        let kind = row.get("kind").and_then(Value::as_str).unwrap_or_default();
        rewrite_json(&mut value, &format!("event:{kind}"), maps)?;
    } else {
        rewrite_json(&mut value, table, maps)?;
    }
    row.insert(column.into(), Value::String(serde_json::to_string(&value)?));
    Ok(())
}

fn transform(transfer: &Transfer, table: &str, row: &mut Row, target: &Connection) -> Result<()> {
    if table == "events" {
        rewrite_json_field(row, table, "payload", &transfer.maps)?;
    }
    if table == "provider_requests" {
        let structured = row
            .get("output")
            .and_then(Value::as_str)
            .is_some_and(|text| {
                let text = text.trim_start();
                (text.starts_with('{') || text.starts_with('['))
                    && serde_json::from_str::<Value>(text).is_ok()
            });
        if structured {
            rewrite_json_field(row, table, "output", &transfer.maps)?;
        }
    }
    if table == "plugins" {
        if let Some(id) = row.get("id").and_then(Value::as_str) {
            let content = transfer.plugin_manifests.get(id).cloned().ok_or_else(|| {
                BackendError::new("invalid_backup_reference", "插件记录缺少声明式清单")
            })?;
            row.insert("manifest_json".into(), Value::String(content));
        }
    }
    if table == "provider_meta" {
        let key = row.get("key").and_then(Value::as_str).unwrap_or_default();
        if key == "active_provider" {
            let value = row.get("value").cloned().ok_or_else(invalid_record)?;
            row.insert(
                "value".into(),
                map_plain(&value, "provider_profiles", &transfer.maps)?,
            );
        } else if key == "routing_settings" {
            rewrite_json_field(row, table, "value", &transfer.maps)?;
        }
    }
    if table == "learning_node_evidence" {
        if let Some(key) = row.get("evidence_key").and_then(Value::as_str) {
            let (prefix, old, parent) = if let Some(old) = key.strip_prefix("practice-answer-") {
                ("practice-answer-", old, "practice_answers")
            } else if let Some(old) = key.strip_prefix("review-history-") {
                ("review-history-", old, "review_history")
            } else {
                ("", "", "")
            };
            if !old.is_empty() {
                let mapped = map_plain(
                    &Value::from(old.parse::<i64>().map_err(|_| invalid_record())?),
                    parent,
                    &transfer.maps,
                )?;
                row.insert(
                    "evidence_key".into(),
                    Value::String(format!(
                        "{prefix}{}",
                        mapped.as_i64().ok_or_else(invalid_record)?
                    )),
                );
            }
        }
    }
    let mut mapped_columns = BTreeSet::new();
    for column in transfer.schemas.get(table).expect("known schema") {
        if column.name.ends_with("_json") && !(table == "plugins" && column.name == "manifest_json")
        {
            rewrite_json_field(row, table, &column.name, &transfer.maps)?;
        }
        if let Some(parent) = reference(table, &column.name) {
            if matches!(row.get(&column.name), Some(Value::Null) | None) {
                continue;
            }
            let value = row.get(&column.name).cloned().ok_or_else(invalid_record)?;
            if value == Value::String(String::new()) {
                continue;
            }
            match map_plain(&value, parent, &transfer.maps) {
                Ok(mapped) => {
                    row.insert(column.name.clone(), mapped);
                }
                Err(error)
                    if parent == "provider_requests"
                        && error.code == "invalid_backup_reference" =>
                {
                    row.insert(
                        column.name.clone(),
                        map_plain(&value, "provider_route_runs", &transfer.maps)?,
                    );
                }
                Err(error) => return Err(error),
            }
            mapped_columns.insert(column.name.clone());
        }
    }
    for (column, parent) in foreign_keys(target, table)? {
        if mapped_columns.contains(&column) || matches!(row.get(&column), Some(Value::Null) | None)
        {
            continue;
        }
        let value = row.get(&column).cloned().ok_or_else(invalid_record)?;
        row.insert(column, map_plain(&value, parent, &transfer.maps)?);
    }
    Ok(())
}

fn insert_row(tx: &Connection, table: &str, schema: &[Column], row: &Row) -> Result<()> {
    let names = schema
        .iter()
        .map(|column| format!("\"{}\"", column.name))
        .collect::<Vec<_>>()
        .join(",");
    let placeholders = (1..=schema.len())
        .map(|index| format!("?{index}"))
        .collect::<Vec<_>>()
        .join(",");
    let values = schema
        .iter()
        .map(|column| sql_value(row.get(&column.name).unwrap_or(&Value::Null)))
        .collect::<Result<Vec<_>>>()?;
    tx.execute(
        &format!("INSERT INTO \"{table}\" ({names}) VALUES ({placeholders})"),
        params_from_iter(values),
    )
    .map_err(|error| {
        BackendError::new(
            "backup_constraint_failed",
            &format!("备份表 {table} 未通过当前数据库约束：{error}；未导入任何记录"),
        )
    })?;
    Ok(())
}

fn import_kv(transfer: &mut Transfer, tx: &Connection) -> Result<()> {
    let rows = transfer.tables.get("kv").cloned().unwrap_or_default();
    for row in rows {
        let namespace = row
            .get("namespace")
            .and_then(Value::as_str)
            .ok_or_else(invalid_record)?;
        if namespace == "resources" {
            continue;
        }
        let original = Value::String(
            row.get("id")
                .and_then(Value::as_str)
                .ok_or_else(invalid_record)?
                .into(),
        );
        let group = match namespace {
            "tasks" => "kv:tasks",
            "notes" => "kv:notes",
            "projects" => "kv:projects",
            "drafts" => "kv:drafts",
            _ => "kv:other",
        };
        let key = key(&original)?;
        let mapped = transfer
            .maps
            .get(group)
            .and_then(|map| map.get(&key))
            .cloned()
            .ok_or_else(invalid_record)?;
        let id = mapped
            .as_str()
            .map(str::to_string)
            .or_else(|| mapped.as_i64().map(|id| id.to_string()))
            .ok_or_else(invalid_record)?;
        let mut data: Value = serde_json::from_str(
            row.get("data")
                .and_then(Value::as_str)
                .ok_or_else(invalid_record)?,
        )
        .map_err(|_| invalid_record())?;
        if ["tasks", "notes", "projects"].contains(&namespace) {
            data["id"] = Value::from(id.parse::<i64>().map_err(|_| invalid_record())?);
            super::super::store::validate_collection(namespace, &Value::Array(vec![data.clone()]))?;
        } else if namespace == "drafts" {
            super::super::store::validate_draft(&data)?;
        } else if namespace == "preferences" {
            if id != "current" {
                return Err(invalid_record());
            }
            let exists: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM kv WHERE namespace = 'preferences' AND id = 'current')", [], |row| row.get(0))?;
            if exists {
                continue;
            }
            super::super::store::validate_preferences(&data)?;
        }
        Store::insert_json(tx, namespace, &id, &data)?;
        Store::next_revision(tx, namespace)?;
        *transfer.counts.entry(namespace.into()).or_default() += 1;
    }
    materials::sync_resources(tx)?;
    Store::next_revision(tx, "resources")?;
    Ok(())
}

fn global_conflict(tx: &Connection, table: &str, row: &Row) -> Result<bool> {
    let key = match table {
        "meta" | "provider_meta" => "key",
        "revisions" => "namespace",
        "sync_config" => "id",
        _ => return Ok(false),
    };
    let value = row.get(key).ok_or_else(invalid_record)?;
    exists(tx, table, key, value)
}

fn import_rows(transfer: &mut Transfer, tx: &Connection, ordered: &[&'static str]) -> Result<()> {
    import_kv(transfer, tx)?;
    for table in ordered {
        if *table == "kv" {
            continue;
        }
        let mut rows = transfer.tables.get(*table).cloned().unwrap_or_default();
        for row in &mut rows {
            if global_conflict(tx, table, row)? {
                continue;
            }
            if table == &"agent_memory" {
                let original = row
                    .get("key")
                    .and_then(Value::as_str)
                    .ok_or_else(invalid_record)?
                    .to_string();
                if exists(tx, table, "key", &Value::String(original.clone()))? {
                    row.insert(
                        "key".into(),
                        Value::String(format!(
                            "{original} · 导入 {}",
                            transfer.maps.get("agent_memory").map_or(0, HashMap::len) + 1
                        )),
                    );
                }
            }
            if table == &"shared_plans" {
                let hash = row
                    .get("token_hash")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if exists(tx, table, "token_hash", &Value::String(hash.into()))? {
                    continue;
                }
            }
            transform(transfer, table, row, tx)?;
            if *table == "materials" {
                let id = row.get("id").cloned().ok_or_else(invalid_record)?;
                let source = transfer.maps.get("materials").and_then(|map| {
                    map.iter()
                        .find(|(_, value)| **value == id)
                        .map(|(source, _)| source.clone())
                });
                if source
                    .is_some_and(|source| transfer.reused.contains(&format!("materials:{source}")))
                {
                    continue;
                }
            }
            insert_row(
                tx,
                table,
                transfer.schemas.get(*table).expect("known schema"),
                row,
            )?;
            *transfer.counts.entry((*table).into()).or_default() += 1;
        }
    }
    Ok(())
}

#[derive(Clone)]
struct StagedFile {
    staged: PathBuf,
    target: PathBuf,
}

fn stage_file(workspace: &Path, index: usize, bytes: &[u8]) -> Result<PathBuf> {
    let directory = workspace.join("files");
    fs::create_dir_all(&directory)?;
    let path = directory.join(index.to_string());
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    std::io::Write::write_all(&mut file, bytes)?;
    file.sync_all()?;
    Ok(path)
}

fn target_relative(entry: &FileEntry, maps: &IdMap) -> Result<String> {
    let parts = entry.path.split('/').collect::<Vec<_>>();
    match parts.as_slice() {
        ["materials", "originals", name] => {
            if Path::new(name).file_stem().and_then(|value| value.to_str())
                != Some(entry.sha256.as_str())
            {
                return Err(BackendError::invalid(
                    "invalid_backup_path",
                    "资料文件名与内容哈希不一致",
                ));
            }
            Ok(entry.path.clone())
        }
        ["plugins", id, "manifest.json"] => {
            let mapped = map_plain(&Value::String((*id).into()), "plugins", maps)?
                .as_str()
                .ok_or_else(invalid_record)?
                .to_string();
            Ok(format!("plugins/{mapped}/manifest.json"))
        }
        ["agent-workspaces", session, "scratch", path @ ..] if !path.is_empty() => {
            let session = map_plain(
                &Value::from(session.parse::<i64>().map_err(|_| invalid_record())?),
                "agent_sessions",
                maps,
            )?
            .as_i64()
            .ok_or_else(invalid_record)?;
            let relative = path.join("/");
            if !matches!(
                Path::new(&relative)
                    .extension()
                    .and_then(|value| value.to_str()),
                Some("json" | "md" | "txt")
            ) {
                return Err(invalid_record());
            }
            Ok(format!("agent-workspaces/{session}/scratch/{relative}"))
        }
        _ => Err(BackendError::invalid(
            "invalid_backup_path",
            "备份文件不属于允许的应用目录",
        )),
    }
}

fn stage_files(
    transfer: &mut Transfer,
    backup: &mut OpenBackup,
    root: &Path,
) -> Result<Vec<StagedFile>> {
    let entries = backup.manifest.files.clone();
    let mut staged = Vec::new();
    for (index, entry) in entries.iter().enumerate() {
        let mut bytes = backup.read_file(entry)?;
        let relative = target_relative(entry, &transfer.maps)?;
        if relative.starts_with("plugins/") {
            let content = String::from_utf8(bytes).map_err(|_| invalid_record())?;
            plugins::validate_manifest_content(&content)?;
            let old_id = entry.path.split('/').nth(1).ok_or_else(invalid_record)?;
            let mut manifest: Value = serde_json::from_str(&content)?;
            if manifest.get("id").and_then(Value::as_str) != Some(old_id) {
                return Err(BackendError::new(
                    "plugin_manifest_mismatch",
                    "插件清单标识与文件路径不一致",
                ));
            }
            let id = relative
                .split('/')
                .nth(1)
                .ok_or_else(invalid_record)?
                .to_string();
            manifest["id"] = Value::String(id.clone());
            let content = serde_json::to_string_pretty(&manifest)?;
            plugins::validate_manifest_content(&content)?;
            transfer.plugin_manifests.insert(id, content.clone());
            bytes = content.into_bytes();
        }
        let target = root.join(&relative);
        if target.exists() {
            if format!("{:x}", sha2::Sha256::digest(fs::read(&target)?))
                != format!("{:x}", sha2::Sha256::digest(&bytes))
            {
                return Err(BackendError::new(
                    "backup_file_conflict",
                    "目标文件已存在且内容不同，未覆盖原文件",
                ));
            }
            continue;
        }
        staged.push(StagedFile {
            staged: stage_file(backup.workspace.path(), index, &bytes)?,
            target,
        });
    }
    Ok(staged)
}

fn install_files(root: &Path, files: &[StagedFile]) -> Result<Vec<PathBuf>> {
    let canonical_root = fs::canonicalize(root)?;
    let mut installed = Vec::new();
    for file in files {
        let parent = file.target.parent().ok_or_else(invalid_record)?;
        fs::create_dir_all(parent)?;
        if !fs::canonicalize(parent)?.starts_with(&canonical_root)
            || fs::symlink_metadata(&file.target)
                .map(|metadata| metadata.file_type().is_symlink())
                .unwrap_or(false)
        {
            return Err(BackendError::new(
                "backup_path_blocked",
                "备份文件目标超出应用目录，已阻止写入",
            ));
        }
        fs::hard_link(&file.staged, &file.target)?;
        installed.push(file.target.clone());
    }
    Ok(installed)
}

pub fn import(
    backup: &mut OpenBackup,
    store: &Store,
    state: &LearningState,
) -> Result<BTreeMap<String, u64>> {
    let source =
        Connection::open_with_flags(&backup.database, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    source.execute("PRAGMA query_only = ON", [])?;
    let mut transfer = read_tables(&source, &store.conn)?;
    prepare_ids(&mut transfer, &store.conn)?;
    let ordered = order(&transfer, &store.conn)?;
    let root = state
        .database_path()
        .parent()
        .unwrap_or_else(|| Path::new("."));
    let staged = stage_files(&mut transfer, backup, root)?;
    let mut installed = Vec::new();
    let outcome = (|| {
        let tx = store.conn.unchecked_transaction()?;
        import_rows(&mut transfer, &tx, &ordered)?;
        installed = install_files(root, &staged)?;
        tx.commit()?;
        Ok(transfer.counts)
    })();
    if outcome.is_err() {
        for path in installed {
            let _ = fs::remove_file(path);
        }
    }
    outcome
}
