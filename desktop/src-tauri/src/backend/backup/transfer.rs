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
                primary: row.get::<_, i64>(5)? > 0,
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
        if !source_columns.is_empty() {
            let target_names = columns
                .iter()
                .map(|column| &column.name)
                .collect::<BTreeSet<_>>();
            let source_names = source_columns
                .iter()
                .map(|column| &column.name)
                .collect::<BTreeSet<_>>();
            // 允许旧版本备份缺少后续新增列（缺列走默认值）；
            // 备份出现当前未知列说明它来自更新版本，给出明确版本错误（08#9）
            let unknown = source_names
                .difference(&target_names)
                .copied()
                .collect::<BTreeSet<_>>();
            if !unknown.is_empty() {
                return Err(BackendError::invalid(
                    "unsupported_backup_schema",
                    "备份表字段来自更新版本的应用，请先升级再导入",
                ));
            }
        }
        if source_columns.is_empty() || super::archive::EXCLUDED_AUTH_TABLES.contains(table) {
            tables.insert(*table, Vec::new());
            schemas.insert(*table, columns);
            continue;
        }
        let mut statement = source.prepare(&format!("SELECT * FROM \"{table}\" ORDER BY rowid"))?;
        let rows = statement
            .query_map([], |row| {
                let mut values = BTreeMap::new();
                for (index, column) in source_columns.iter().enumerate() {
                    values.insert(column.name.clone(), read_value(row, index, &column.kind)?);
                }
                if table == &"focus_sessions" {
                    values.entry("paused_at".into()).or_insert(Value::Null);
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
    // 31#46：一次取最大已用号再自增，替代逐号 EXISTS 探测（编号多时 O(n²)）
    let max: i64 = conn.query_row(
        &format!(
            "SELECT COALESCE(MAX(CAST(SUBSTR(\"{column}\", 10) AS INTEGER)), 0) FROM \"{table}\" WHERE \"{column}\" GLOB 'imported-[0-9]*' AND \"{column}\" NOT GLOB 'imported-[0-9]*[^0-9]*'"
        ),
        [],
        |row| row.get(0),
    )?;
    for number in (max + 1).max(1)..1_000_000 {
        let candidate = Value::String(format!("imported-{number}"));
        if !allocated.contains(&key(&candidate)?) {
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
            // 31#43：重复导入幂等——provider_requests 的 TEXT 主键冲突原先一律
            // 改写 imported-N，同备份二次导入会复制出重复请求行且 agent-{run}-…
            // 前缀断裂（会话预算 GLOB 聚合失配、历史输出重复计费）。按内容签名
            // 找回目标中已存在的同一逻辑请求，映射到既有 id 交给 global_conflict
            // 跳过；目标同名行内容不同（真正的独立请求）才改写 imported-N
            if *table == "provider_requests" && exists(target, table, &primary.name, &original)? {
                let matched: Option<String> = target
                    .query_row(
                        "SELECT request_id FROM provider_requests WHERE model IS ?1 AND created_at IS ?2 AND finish_reason IS ?3 AND attempts IS ?4 AND latency_ms IS ?5 AND status IS ?6 LIMIT 1",
                        params![
                            row.get("model").map(sql_value).transpose()?,
                            row.get("created_at").map(sql_value).transpose()?,
                            row.get("finish_reason").map(sql_value).transpose()?,
                            row.get("attempts").map(sql_value).transpose()?,
                            row.get("latency_ms").map(sql_value).transpose()?,
                            row.get("status").map(sql_value).transpose()?,
                        ],
                        |row| row.get(0),
                    )
                    .optional()?;
                if let Some(existing) = matched {
                    mapped = Value::String(existing);
                    transfer.reused.insert(reuse_key.clone());
                }
            }
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
                // 25#13：study_checkins 主键是真实业务日期——改写会造出
                // "imported-N" 假日期脏行（按日期等值查询的日历静默漏算），
                // 与 meta 等表一样保留原键、交给 global_conflict 按日期跳过
                && ![
                    "meta",
                    "provider_meta",
                    "revisions",
                    "sync_config",
                    "study_checkins",
                    // 31#44：保留原键，import_rows 的「 · 导入 N」后缀分支才可达
                    "agent_memory",
                ]
                .contains(table)
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

/// 31#2：lenient 模式（events 表）下引用映射不到（invalid_backup_reference）
/// 时返回 None——调用方丢弃该字段继续，历史事件的断链不再炸整单导入
fn map_payload_field(
    value: &Value,
    target: &str,
    maps: &IdMap,
    lenient: bool,
) -> Result<Option<Value>> {
    let mapped = if target == "provider_requests" {
        map_request(value, maps)
    } else {
        map_plain(value, target, maps)
    };
    match mapped {
        Ok(value) => Ok(Some(value)),
        Err(error) if lenient && error.code == "invalid_backup_reference" => Ok(None),
        Err(error) => Err(error),
    }
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
                &format!("备份关联目标 {target} 缺少记录 {key:?}"),
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
    // 23#4：主键=外键的三张子表——主键必须随父表映射重写（prepare_ids 因此
    // 跳过对它们的自编号重映射），否则子表按自身 MAX+1 重映射后会挂错父
    // 记录或撞外键
    if column == "material_id" && table == "material_text" {
        return Some("materials");
    }
    if column == "group_id" && table == "community_group_members" {
        return Some("community_groups");
    }
    if column == "room_id" && table == "community_reservations" {
        return Some("community_rooms");
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
                    "userId" => Some("local_users"),
                    // 31#2：focus.* 事件的 sessionId 是 focus_sessions.id——原二分
                    // 把它指到 knowledge_sessions，用过专注但没用过 Wiki 的备份
                    // 整单报「缺少记录」不可导入
                    "sessionId" if table.starts_with("event:focus.") => Some("focus_sessions"),
                    "sessionId" if !table.starts_with("event:security.") => Some(
                        if table.starts_with("agent_") || table.starts_with("event:agent.") {
                            "agent_sessions"
                        } else {
                            "knowledge_sessions"
                        },
                    ),
                    // 31#1：agent 会话预算经 agent_session_id 关联（不再依赖
                    // request_id 字面前缀），导入时按外键重映射保持关联
                    "agent_session_id" => Some("agent_sessions"),
                    // 31#8：主键=外键的两张练习子表——主键随题目映射重写，
                    // 否则按本表自编号后撞「缺少记录」整单失败或静默挂错题
                    "question_id"
                        if table == "practice_mistakes" || table == "practice_bookmarks" =>
                    {
                        Some("practice_questions")
                    }
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
                    // 31#2：events 是历史事实流——payload 引用的行可能早已删除
                    //（任务/笔记删除后的残留事件）。映射不到时丢弃该字段而不是
                    // 让整单导入失败；实体表的外键列仍走严格路径
                    let lenient = table.starts_with("event:");
                    let existing = object.get(&name).cloned();
                    if let Some(item) = existing {
                        if item.is_array() {
                            let mut next = Vec::new();
                            for entry in item.as_array().expect("array") {
                                if let Some(value) =
                                    map_payload_field(entry, target, maps, lenient)?
                                {
                                    next.push(value);
                                }
                            }
                            object.insert(name.clone(), Value::Array(next));
                        } else if !item.is_null() {
                            match map_payload_field(&item, target, maps, lenient)? {
                                Some(value) => {
                                    object.insert(name.clone(), value);
                                }
                                None => {
                                    object.remove(&name);
                                }
                            }
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
    .map_err(|_| {
        // 29#3：不向用户透出 rusqlite 的 SQL 内部结构（"UNIQUE constraint
        // failed: …"），错误码 + 表名已足够定位
        BackendError::new(
            "backup_constraint_failed",
            &format!("备份表 {table} 未通过当前数据库约束，未导入任何记录"),
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
    // 23#5：security_quotas 是复合主键 (user_id, resource)，prepare_ids 不做
    // 主键重映射——登录用户重复恢复自己的备份时按原键整体跳过，不再撞
    // UNIQUE 导致整单回滚
    if table == "security_quotas" {
        let user = row
            .get("user_id")
            .and_then(Value::as_i64)
            .ok_or_else(invalid_record)?;
        let resource = row
            .get("resource")
            .and_then(Value::as_str)
            .ok_or_else(invalid_record)?
            .to_string();
        let occupied: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM security_quotas WHERE user_id = ?1 AND resource = ?2)",
            params![user, resource],
            |row| row.get(0),
        )?;
        return Ok(occupied);
    }
    // 31#8：主键=外键的练习子表按题目键跳过（题目已映射为目标 id，
    // 目标已有该题的错题/收藏即先前导入过，保留目标侧行）
    if table == "practice_mistakes" || table == "practice_bookmarks" {
        let question = row.get("question_id").ok_or_else(invalid_record)?;
        return exists(tx, table, "question_id", question);
    }
    // 31#43：同一 request_id 已存在 = 同一逻辑请求（追加导入幂等跳过）
    if table == "provider_requests" {
        let request = row
            .get("request_id")
            .and_then(Value::as_str)
            .ok_or_else(invalid_record)?;
        return exists(tx, table, "request_id", &Value::String(request.into()));
    }
    let key = match table {
        "meta" | "provider_meta" => "key",
        "revisions" => "namespace",
        "sync_config" => "id",
        "study_checkins" => "date",
        _ => return Ok(false),
    };
    let value = row.get(key).ok_or_else(invalid_record)?;
    exists(tx, table, key, value)
}

fn import_rows(transfer: &mut Transfer, tx: &Connection, ordered: &[&'static str]) -> Result<()> {
    // 31#46：一次性构建「按哈希复用目标已有资料」的目标 id 集合——原先
    // 每行资料在 transform 后对 IdMap 线性反查（O(n²)）
    let reused_material_targets: HashSet<String> = transfer
        .maps
        .get("materials")
        .map(|map| {
            map.iter()
                .filter(|(source, _)| transfer.reused.contains(&format!("materials:{source}")))
                .filter_map(|(_, mapped)| key(mapped).ok())
                .collect()
        })
        .unwrap_or_default();
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
            // 31#9：material_text 的复用判定要在改写前记录源侧资料 id
            // （transform 会改写 material_id；prepare_ids 不改 FK）
            let reused_material_source = if *table == "material_text" {
                row.get("material_id")
                    .map(|value| key(value).map(|key| format!("materials:{key}")))
                    .transpose()?
            } else {
                None
            };
            transform(transfer, table, row, tx).map_err(|error| {
                BackendError::new(
                    &error.code,
                    &format!("处理备份表 {table} 时：{}", error.message),
                )
            })?;
            // 31#46：资料复用按目标 id 判定（prepare_ids 已把主键改写为映射值）
            if *table == "materials" {
                let id = row
                    .get("id")
                    .map(|value| key(value))
                    .transpose()?
                    .unwrap_or_default();
                if reused_material_targets.contains(&id) {
                    continue;
                }
            }
            // 23#4：material_text 主键随父资料映射——父资料按同哈希复用目标侧
            // 已有记录时，目标侧已持有该资料的解析文本，跳过避免撞主键。
            // 31#9：检查须用 transform 前的源侧 id——reused 集合以源 id 为键，
            // 上方 transform 已把 material_id 改写为目标 id，两侧 id 不一致时
            // （跨设备同文件各自解析）原先必漏检、撞主键整单回滚
            if *table == "material_text" {
                if reused_material_source
                    .as_ref()
                    .is_some_and(|source| transfer.reused.contains(source))
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

/// 13#19：预演与导入共用同一套解析/ID 冲突映射/依赖排序——在回滚的
/// 事务里执行 import_rows，把“库已提交才暴露”的引用断链提前到预览。
/// 不执行 stage_files/install_files（文件安装是导入的副作用，预演不落盘）。
pub fn dry_run(
    backup: &mut OpenBackup,
    store: &Store,
    state: &LearningState,
) -> Result<BTreeMap<String, u64>> {
    let source =
        Connection::open_with_flags(&backup.database, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    source.execute("PRAGMA query_only = ON", [])?;
    let mut transfer = read_tables(&source, &store.conn)?;
    let root = state
        .workspace_path()
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let tx = store.conn.unchecked_transaction()?;
    // 25#14：与 import 同步——ID 分配在事务内执行，预演与真实导入的
    // 冲突判定保持同一套时序
    prepare_ids(&mut transfer, &tx)?;
    // stage_files 只写入备份临时工作区（RAII 随 OpenBackup 清理），
    // 目标文件系统仅读校验；插件清单校验也在这里完成。
    // 必须在 prepare_ids 之后：plugins/<id>/manifest.json 的目标路径
    // 要按重映射后的插件 id 落位（提前执行会拿空映射报缺少记录）
    stage_files(&mut transfer, backup, &root)?;
    let ordered = order(&transfer, &tx)?;
    import_rows(&mut transfer, &tx, &ordered)?;
    tx.rollback()?;
    Ok(transfer.counts)
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
    let root = state
        .workspace_path()
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let mut staged = Vec::new();
    let mut installed = Vec::new();
    let outcome = (|| {
        // 25#14：ID 冲突分配与依赖排序放进 IMMEDIATE 事务——原实现在
        // autocommit 态算 MAX+1 再开事务，窗口内其他连接写入同号 id 时
        // 以 backup_constraint_failed 整单回滚；持写锁后再分配可关闭该窗口
        let tx = rusqlite::Transaction::new_unchecked(
            &store.conn,
            rusqlite::TransactionBehavior::Immediate,
        )?;
        prepare_ids(&mut transfer, &tx)?;
        // stage_files 须在 prepare_ids 之后：插件清单目标路径按重映射后
        // 的插件 id 落位（提前执行会拿空映射报缺少记录）；staged 只写
        // 备份临时工作区，失败由 OpenBackup RAII 清理
        staged = stage_files(&mut transfer, backup, &root)?;
        let ordered = order(&transfer, &tx)?;
        import_rows(&mut transfer, &tx, &ordered)?;
        installed = install_files(&root, &staged)?;
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
