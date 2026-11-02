use super::super::commands::emit;
use super::super::error::{BackendError, Result};
use super::super::learning::lecture::LectureContent;
use super::super::learning::{self, Source};
use super::super::store::Store;
use super::*;
use rusqlite::{params, OptionalExtension};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Component, Path, PathBuf};

pub fn catalog() -> Vec<super::super::providers::tools::ToolDefinition> {
    fn definition(
        name: &str,
        description: &str,
        parameters: Value,
    ) -> super::super::providers::tools::ToolDefinition {
        super::super::providers::tools::ToolDefinition {
            kind: "function".into(),
            function: super::super::providers::tools::ToolDefinitionFunction {
                name: name.into(),
                description: description.into(),
                parameters,
            },
        }
    }
    let empty = || json!({"type": "object", "properties": {}});
    vec![
        definition(
            "workspace.list",
            "列出当前方案的阅读与 scratch 产物",
            empty(),
        ),
        definition(
            "workspace.read",
            "读取当前方案、讲义或 scratch 产物",
            json!({"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]}),
        ),
        definition(
            "workspace.write",
            "将有效的讲义单元保存为 scratch 产物",
            json!({"type": "object", "properties": {"path": {"type": "string"}, "stageId": {"type": "string"}, "unitIndex": {"type": "integer", "content": {"type": "object"}, "overwrite": {"type": "boolean"}}, "required": ["stageId", "unitIndex", "content"]}}),
        ),
        definition(
            "workspace.edit",
            "对已有 scratch 讲义做一次精确文本替换",
            json!({"type": "object", "properties": {"artifactId": {"type": "integer"}, "oldText": {"type": "string"}, "newText": {"type": "string"}}, "required": ["artifactId", "oldText", "newText"]}),
        ),
        definition(
            "web.search",
            "检索公开学习资源；当前未接入，返回真实不可用状态",
            json!({"type": "object", "properties": {"query": {"type": "string"}}}),
        ),
        definition(
            "web.fetch",
            "抓取公开网页；当前未接入，返回真实不可用状态",
            json!({"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),
        ),
        definition(
            "memory.save",
            "请求批准后保存一条学习偏好",
            json!({"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}),
        ),
        definition(
            "memory.recall",
            "按需召回已批准的学习偏好",
            json!({"type": "object", "properties": {"key": {"type": "string"}, "query": {"type": "string"}}}),
        ),
        definition(
            "memory.delete",
            "请求批准后删除一条学习偏好",
            json!({"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}),
        ),
        definition(
            "history.load",
            "读取当前会话的真实历史",
            json!({"type": "object", "properties": {"limit": {"type": "integer"}}}),
        ),
        definition(
            "history.search",
            "搜索真实会话与消息历史",
            json!({"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
        ),
        definition(
            "artifact.promote",
            "请求用户批准将 scratch 讲义提升为新版本",
            json!({"type": "object", "properties": {"artifactId": {"type": "integer"}}, "required": ["artifactId"]}),
        ),
    ]
}

fn string<'a>(arguments: &'a Value, key: &str) -> Result<&'a str> {
    arguments
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            BackendError::invalid(
                "invalid_tool_arguments",
                format!("工具参数 {key} 必须是非空字符串").as_str(),
            )
        })
}

fn number(arguments: &Value, key: &str) -> Result<i64> {
    let value = arguments.get(key).and_then(Value::as_i64).ok_or_else(|| {
        BackendError::invalid(
            "invalid_tool_arguments",
            format!("工具参数 {key} 必须是整数").as_str(),
        )
    })?;
    if value <= 0 {
        return Err(BackendError::invalid(
            "invalid_tool_arguments",
            format!("工具参数 {key} 必须为正数").as_str(),
        ));
    }
    Ok(value)
}

fn index(arguments: &Value, key: &str) -> Result<usize> {
    let value = arguments.get(key).and_then(Value::as_u64).ok_or_else(|| {
        BackendError::invalid(
            "invalid_tool_arguments",
            format!("工具参数 {key} 必须是非负整数").as_str(),
        )
    })?;
    usize::try_from(value).map_err(|_| {
        BackendError::invalid(
            "invalid_tool_arguments",
            format!("工具参数 {key} 超出索引范围").as_str(),
        )
    })
}

fn sources(state: &AgentState, session: &AgentSession) -> Result<Vec<Source>> {
    let plan = learning::get(&state.learning, &session.plan_id)?;
    serde_json::from_value(
        plan.artifacts
            .get("step0")
            .and_then(|step| step.get("snapshot"))
            .cloned()
            .ok_or_else(|| BackendError::new("invalid_persisted_plan", "工作区缺少资料快照"))?,
    )
    .map_err(Into::into)
}

fn content(value: Value, sources: &[Source]) -> Result<LectureContent> {
    let content: LectureContent = serde_json::from_value(value)
        .map_err(|_| BackendError::invalid("invalid_artifact_content", "产物不是有效的讲义单元"))?;
    learning::lecture::validate_content(&content, sources)?;
    Ok(content)
}

fn scratch_root(state: &AgentState, session: &AgentSession) -> Result<PathBuf> {
    let root = state
        .database_path
        .parent()
        .unwrap_or(Path::new("."))
        .join("agent-workspaces")
        .join(session.id.to_string())
        .join("scratch");
    fs::create_dir_all(&root)?;
    Ok(root)
}

fn artifact_path(state: &AgentState, session: &AgentSession, value: &str) -> Result<PathBuf> {
    if value.is_empty() || value.len() > 128 {
        return Err(BackendError::invalid(
            "invalid_artifact_path",
            "产物相对路径不能为空且不能超过 128 字节",
        ));
    }
    let path = Path::new(value);
    if !path
        .components()
        .all(|part| matches!(part, Component::Normal(_)))
        || path.components().count() > 6
    {
        return Err(BackendError::invalid(
            "invalid_artifact_path",
            "产物路径不得包含绝对路径、父级目录或前缀",
        ));
    }
    if !matches!(
        path.extension().and_then(|extension| extension.to_str()),
        Some("json" | "md" | "txt")
    ) {
        return Err(BackendError::invalid(
            "invalid_artifact_path",
            "产物仅支持 JSON、Markdown 或文本文件",
        ));
    }
    let root = scratch_root(state, session)?;
    let target = root.join(path);
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
        let canonical = fs::canonicalize(parent)?;
        if !canonical.starts_with(fs::canonicalize(&root)?) {
            return Err(BackendError::new(
                "artifact_path_blocked",
                "产物路径超出了当前 scratch 工作区",
            ));
        }
    }
    if fs::symlink_metadata(&target)
        .map(|meta| meta.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(BackendError::new(
            "artifact_path_blocked",
            "scratch 中不允许符号链接",
        ));
    }
    Ok(target)
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| BackendError::new("invalid_artifact_path", "产物路径缺少父目录"))?;
    let temporary = parent.join(format!(
        ".{}.tmp",
        &format!("{:x}", Sha256::digest(bytes))[..16]
    ));
    let backup = parent.join(format!(
        ".{}.previous",
        &format!("{:x}", Sha256::digest(bytes))[..16]
    ));
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    std::io::Write::write_all(&mut file, bytes)?;
    file.sync_all()?;
    drop(file);
    let existed = path.exists();
    if existed {
        fs::rename(path, &backup)?;
    }
    match fs::rename(&temporary, path) {
        Ok(()) => {
            if existed {
                fs::remove_file(backup)?;
            }
            Ok(())
        }
        Err(error) => {
            if existed {
                let _ = fs::rename(backup, path);
            }
            let _ = fs::remove_file(temporary);
            Err(error.into())
        }
    }
}

fn commit_artifact<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session: &AgentSession,
    stage_id: &str,
    unit_index: usize,
    path: Option<&str>,
    content: &LectureContent,
    overwrite: bool,
) -> Result<Value> {
    let plan = learning::get(&state.learning, &session.plan_id)?;
    if !plan.result.as_ref().is_some_and(|result| {
        result
            .stages
            .iter()
            .any(|stage| stage.id == stage_id && unit_index < stage.node_ids.len())
    }) {
        return Err(BackendError::new(
            "invalid_artifact_scope",
            "产物阶段或单元索引超出当前方案范围",
        ));
    }
    let sources = sources(state, session)?;
    learning::lecture::validate_content(content, &sources)?;
    let path = match path {
        Some(path) => path.to_string(),
        None => {
            let hash = format!("{:x}", Sha256::digest(stage_id.as_bytes()));
            format!("stage-{}-unit-{unit_index}.json", &hash[..12])
        }
    };
    let target = artifact_path(state, session, &path)?;
    if target.exists() && !overwrite {
        return Err(BackendError::new(
            "artifact_exists",
            "scratch 中已存在同名产物，请明确允许覆盖",
        ));
    }
    let bytes = serde_json::to_vec_pretty(content)?;
    let hash = format!("{:x}", Sha256::digest(&bytes));
    atomic_write(&target, &bytes)?;
    let store = state.store()?;
    let timestamp = now()?;
    let expires = (time::OffsetDateTime::now_utc() + time::Duration::days(1))
        .format(&time::format_description::well_known::Rfc3339)?;
    let tx = store.conn.unchecked_transaction()?;
    let existing = tx
        .query_row(
            "SELECT id FROM agent_artifacts WHERE session_id = ?1 AND path = ?2",
            params![session.id, path],
            |row| row.get::<_, i64>(0),
        )
        .optional()?;
    let id = if let Some(id) = existing {
        tx.execute("UPDATE agent_artifacts SET plan_id = ?1, stage_id = ?2, unit_index = ?3, content_json = ?4, sha256 = ?5, status = 'committed', expires_at = ?6 WHERE id = ?7", params![session.plan_id, stage_id, unit_index as i64, serde_json::to_string(content)?, hash, expires, id])?;
        id
    } else {
        tx.execute("INSERT INTO agent_artifacts (session_id, path, plan_id, stage_id, unit_index, content_json, sha256, status, created_at, expires_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'committed', ?8, ?9)", params![session.id, path, session.plan_id, stage_id, unit_index as i64, serde_json::to_string(content)?, hash, timestamp, expires])?;
        tx.last_insert_rowid()
    };
    let event = Store::event(
        &tx,
        "agent.artifact_committed",
        json!({"sessionId": session.id, "artifactId": id, "path": path, "status": "committed"}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(
        json!({"status": "committed", "artifactId": id, "path": path, "sha256": hash, "expiresAt": expires}),
    )
}

fn read_artifact(
    state: &AgentState,
    session: &AgentSession,
    id: i64,
) -> Result<(String, String, usize, LectureContent)> {
    let store = state.store()?;
    let (path, stage_id, unit_index, content, expected): (String, String, usize, String, String) = store.conn.query_row("SELECT path, stage_id, unit_index, content_json, sha256 FROM agent_artifacts WHERE id = ?1 AND session_id = ?2", params![id, session.id], |row| Ok((row.get(0)?, row.get(1)?, row.get::<_, i64>(2)? as usize, row.get(3)?, row.get(4)?))).optional()?.ok_or_else(|| BackendError::new("not_found", "scratch 产物不存在"))?;
    let target = artifact_path(state, session, &path)?;
    let bytes = fs::read(target)?;
    if format!("{:x}", Sha256::digest(&bytes)) != expected {
        return Err(BackendError::new(
            "artifact_modified_externally",
            "scratch 文件已被外部修改，不能直接提升",
        ));
    }
    Ok((path, stage_id, unit_index, serde_json::from_str(&content)?))
}

fn approval<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session: &AgentSession,
    kind: &str,
    payload: Value,
) -> Result<Value> {
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO agent_approvals (session_id, kind, payload_json, status, created_at) VALUES (?1, ?2, ?3, 'pending', ?4)", params![session.id, kind, serde_json::to_string(&payload)?, now()?])?;
    let id = tx.last_insert_rowid();
    let event = Store::event(
        &tx,
        "agent.approval_requested",
        json!({"sessionId": session.id, "approvalId": id, "kind": kind}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(json!({"status": "approval_required", "approvalId": id, "kind": kind}))
}

fn memory_key(value: &str) -> Result<String> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err(BackendError::invalid(
            "invalid_memory_key",
            "记忆键须为 1 到 64 位字母、数字、短横线或下划线",
        ));
    }
    Ok(value.into())
}

fn memory_value(value: &str) -> Result<String> {
    if value.trim().is_empty() || value.len() > 4000 {
        return Err(BackendError::invalid(
            "invalid_memory_value",
            "记忆内容不能为空且不能超过 4000 字节",
        ));
    }
    Ok(value.into())
}

pub fn execute<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    run_id: i64,
    session: &AgentSession,
    call: &ToolCall,
    step: u32,
) -> Result<Value> {
    if call.id.is_empty() || call.id.len() > 128 || call.arguments.len() > 64 * 1024 {
        return Err(BackendError::invalid(
            "invalid_tool_call",
            "工具调用标识或参数超过限制",
        ));
    }
    let store = state.store()?;
    let registered = store.conn.execute(
        "INSERT INTO agent_tool_calls (id, run_id, step_index, name, arguments_json, status, started_at) VALUES (?1, ?2, ?3, ?4, ?5, 'running', ?6) ON CONFLICT(id) DO NOTHING",
        params![call.id, run_id, step, call.name, call.arguments, now()?],
    )?;
    if registered == 0 {
        let (saved_run, saved_step, saved_name, saved_arguments, status, result): (
            i64,
            u32,
            String,
            String,
            String,
            Option<String>,
        ) = store.conn.query_row(
            "SELECT run_id, step_index, name, arguments_json, status, result_json FROM agent_tool_calls WHERE id = ?1",
            params![call.id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )?;
        if (saved_run, saved_step, saved_name, saved_arguments)
            != (run_id, step, call.name.clone(), call.arguments.clone())
        {
            return Err(BackendError::new(
                "tool_call_conflict",
                "工具调用标识已用于其他调用，拒绝重复执行",
            ));
        }
        return match result {
            Some(result) => Ok(serde_json::from_str(&result)?),
            None if status == "running" => Err(BackendError::new(
                "tool_active",
                "相同工具调用已登记但未完成，未重复产生副作用",
            )),
            None => Err(BackendError::new(
                "invalid_tool_state",
                "工具调用没有可恢复的结果",
            )),
        };
    }
    let arguments: Value = serde_json::from_str(&call.arguments)
        .map_err(|_| BackendError::invalid("invalid_tool_arguments", "工具参数不是有效 JSON"))?;
    if !arguments.is_object() {
        return Err(BackendError::invalid(
            "invalid_tool_arguments",
            "工具参数必须是 JSON 对象",
        ));
    }
    let result = (|| -> Result<Value> {
        match call.name.as_str() {
            "workspace.list" => {
                let plan = learning::get(&state.learning, &session.plan_id)?;
                Ok(
                    json!({"status": "done", "planId": session.plan_id, "stages": plan.result.as_ref().map(|result| &result.stages), "artifacts": get(state, &session.id.to_string())?.artifacts}),
                )
            }
            "workspace.read" => match string(&arguments, "reference")? {
                "current" | "plan" => Ok(
                    json!({"status": "done", "content": learning::get(&state.learning, &session.plan_id)?}),
                ),
                reference if reference.starts_with("lecture:") => {
                    let stage = reference.trim_start_matches("lecture:");
                    Ok(
                        json!({"status": "done", "content": learning::lecture::get(&state.learning, &session.plan_id, stage, None)?}),
                    )
                }
                reference if reference.starts_with("artifact:") => {
                    let id = reference
                        .trim_start_matches("artifact:")
                        .parse::<i64>()
                        .map_err(|_| BackendError::invalid("invalid_reference", "产物引用无效"))?;
                    let (_, _, _, content) = read_artifact(state, session, id)?;
                    Ok(json!({"status": "done", "content": content}))
                }
                _ => Err(BackendError::invalid(
                    "invalid_reference",
                    "仅支持 plan、lecture:<阶段> 或 artifact:<ID> 引用",
                )),
            },
            "workspace.write" => {
                let content = content(
                    arguments.get("content").cloned().unwrap_or(Value::Null),
                    &sources(state, session)?,
                )?;
                commit_artifact(
                    app,
                    state,
                    session,
                    string(&arguments, "stageId")?,
                    index(&arguments, "unitIndex")?,
                    arguments.get("path").and_then(Value::as_str),
                    &content,
                    arguments
                        .get("overwrite")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                )
            }
            "workspace.edit" => {
                let id = number(&arguments, "artifactId")?;
                let (path, stage_id, unit_index, content) = read_artifact(state, session, id)?;
                let old = string(&arguments, "oldText")?;
                let new = string(&arguments, "newText")?;
                let raw = serde_json::to_string_pretty(&content)?;
                if raw.matches(old).count() != 1 {
                    return Err(BackendError::new(
                        "edit_target_not_unique",
                        "原文片段不存在或不唯一，未修改 scratch",
                    ));
                }
                let updated: LectureContent = serde_json::from_str(&raw.replacen(old, new, 1))
                    .map_err(|_| {
                        BackendError::invalid("invalid_artifact_content", "编辑后的讲义结构无效")
                    })?;
                commit_artifact(
                    app,
                    state,
                    session,
                    &stage_id,
                    unit_index,
                    Some(&path),
                    &updated,
                    true,
                )
            }
            "web.search" | "web.fetch" => Ok(
                json!({"status": "failed", "error": {"code": "capability_unavailable", "message": "外部检索与网页抓取尚未接入，未生成或验证任何链接"}}),
            ),
            "memory.save" => {
                let key = memory_key(string(&arguments, "key")?)?;
                let value = memory_value(string(&arguments, "value")?)?;
                approval(
                    app,
                    state,
                    session,
                    "memory.save",
                    json!({"key": key, "value": value}),
                )
            }
            "memory.recall" => {
                let store = state.store()?;
                if let Ok(key) = string(&arguments, "key") {
                    let value: Option<String> = store
                        .conn
                        .query_row(
                            "SELECT value FROM agent_memory WHERE key = ?1",
                            params![key],
                            |row| row.get(0),
                        )
                        .optional()?;
                    Ok(
                        json!({"status": "done", "entries": value.map(|value| vec![json!({"key": key, "value": value})]).unwrap_or_default()}),
                    )
                } else {
                    let query = arguments
                        .get("query")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .replace('\\', "\\\\")
                        .replace('%', "\\%")
                        .replace('_', "\\_");
                    let mut statement = store.conn.prepare("SELECT key, value FROM agent_memory WHERE key LIKE ?1 ESCAPE '\\' OR value LIKE ?1 ESCAPE '\\' ORDER BY key LIMIT 50")?;
                    let entries = statement.query_map(params![format!("%{query}%")], |row| Ok(json!({"key": row.get::<_, String>(0)?, "value": row.get::<_, String>(1)?})))?.collect::<std::result::Result<Vec<_>, _>>()?;
                    Ok(json!({"status": "done", "entries": entries}))
                }
            }
            "memory.delete" => {
                let key = memory_key(string(&arguments, "key")?)?;
                approval(app, state, session, "memory.delete", json!({"key": key}))
            }
            "history.load" => {
                let limit = arguments
                    .get("limit")
                    .and_then(Value::as_i64)
                    .unwrap_or(20)
                    .clamp(1, 100);
                let messages = session
                    .messages
                    .iter()
                    .rev()
                    .take(limit as usize)
                    .collect::<Vec<_>>();
                Ok(json!({"status": "done", "messages": messages}))
            }
            "history.search" => {
                let query = string(&arguments, "query")?
                    .replace('\\', "\\\\")
                    .replace('%', "\\%")
                    .replace('_', "\\_");
                let store = state.store()?;
                let mut statement = store.conn.prepare("SELECT session_id, role, content FROM agent_messages WHERE content LIKE ?1 ESCAPE '\\' ORDER BY id DESC LIMIT 20")?;
                let messages = statement.query_map(params![format!("%{query}%")], |row| Ok(json!({"sessionId": row.get::<_, i64>(0)?, "role": row.get::<_, String>(1)?, "content": row.get::<_, String>(2)?})))?.collect::<std::result::Result<Vec<_>, _>>()?;
                Ok(json!({"status": "done", "messages": messages}))
            }
            "artifact.promote" => {
                let id = number(&arguments, "artifactId")?;
                let _ = read_artifact(state, session, id)?;
                approval(
                    app,
                    state,
                    session,
                    "artifact.promote",
                    json!({"artifactId": id}),
                )
            }
            _ => Ok(
                json!({"status": "failed", "error": {"code": "unsupported_tool", "message": "该工具未注册，未执行任何操作"}}),
            ),
        }
    })();
    let result = match result {
        Ok(value) => value,
        Err(error) => {
            json!({"status": "failed", "error": {"code": error.code, "message": error.message}})
        }
    };
    let status = result
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("failed");
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE agent_tool_calls SET status = ?1, result_json = ?2, finished_at = ?3 WHERE id = ?4 AND status = 'running'",
        params![status, serde_json::to_string(&result)?, now()?, call.id],
    )?;
    if changed == 0 {
        return Err(BackendError::new(
            "tool_call_conflict",
            "工具调用状态已改变，未提交重复结果",
        ));
    }
    let event = Store::event(
        &tx,
        "agent.tool_completed",
        json!({"runId": run_id, "toolCallId": call.id, "name": call.name, "status": status, "result": result}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(result)
}

fn resolve_pending(
    conn: &rusqlite::Connection,
    id: i64,
    status: &str,
    timestamp: &str,
) -> Result<()> {
    let changed = conn.execute(
        "UPDATE agent_approvals SET status = ?1, resolved_at = ?2 WHERE id = ?3 AND status = 'pending'",
        params![status, timestamp, id],
    )?;
    if changed != 1 {
        return Err(BackendError::new(
            "approval_resolved",
            "审批已被其他操作处理，未重复执行",
        ));
    }
    Ok(())
}

pub fn approve<R: Runtime>(app: &AppHandle<R>, state: &AgentState, id: &str) -> Result<()> {
    let id = numeric_id(id)?;
    let store = state.store()?;
    let (session_id, kind, payload): (i64, String, String) = store.conn.query_row("SELECT session_id, kind, payload_json FROM agent_approvals WHERE id = ?1 AND status = 'pending'", params![id], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?))).optional()?.ok_or_else(|| BackendError::new("approval_resolved", "审批不存在或已处理"))?;
    let session = get(state, &session_id.to_string())?;
    let payload: Value = serde_json::from_str(&payload)?;
    match kind.as_str() {
        "memory.save" => {
            let key = memory_key(
                payload
                    .get("key")
                    .and_then(Value::as_str)
                    .ok_or_else(|| BackendError::invalid("invalid_approval", "记忆审批缺少键"))?,
            )?;
            let value = memory_value(
                payload
                    .get("value")
                    .and_then(Value::as_str)
                    .ok_or_else(|| BackendError::invalid("invalid_approval", "记忆审批缺少内容"))?,
            )?;
            let tx = store.conn.unchecked_transaction()?;
            tx.execute("INSERT INTO agent_memory (key, value, updated_at) VALUES (?1, ?2, ?3) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", params![key, value, now()?])?;
            resolve_pending(&tx, id, "approved", &now()?)?;
            let event = Store::event(
                &tx,
                "agent.memory_changed",
                json!({"approvalId": id, "key": key, "action": "saved"}),
            )?;
            tx.commit()?;
            emit(app, &event);
            Ok(())
        }
        "memory.delete" => {
            let key = memory_key(
                payload
                    .get("key")
                    .and_then(Value::as_str)
                    .ok_or_else(|| BackendError::invalid("invalid_approval", "记忆审批缺少键"))?,
            )?;
            let tx = store.conn.unchecked_transaction()?;
            tx.execute("DELETE FROM agent_memory WHERE key = ?1", params![key])?;
            resolve_pending(&tx, id, "approved", &now()?)?;
            let event = Store::event(
                &tx,
                "agent.memory_changed",
                json!({"approvalId": id, "key": key, "action": "deleted"}),
            )?;
            tx.commit()?;
            emit(app, &event);
            Ok(())
        }
        "artifact.promote" => promote(
            app,
            state,
            &session,
            id,
            payload
                .get("artifactId")
                .and_then(Value::as_i64)
                .ok_or_else(|| BackendError::invalid("invalid_approval", "产物审批缺少 ID"))?,
        ),
        _ => Err(BackendError::invalid("invalid_approval", "未知审批类型")),
    }
}

fn promote<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session: &AgentSession,
    approval_id: i64,
    artifact_id: i64,
) -> Result<()> {
    let store = state.store()?;
    let artifact = store.conn.query_row("SELECT stage_id, unit_index, content_json, expires_at FROM agent_artifacts WHERE id = ?1 AND session_id = ?2", params![artifact_id, session.id], |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?, row.get::<_, String>(2)?, row.get::<_, String>(3)?))).optional()?.ok_or_else(|| BackendError::new("not_found", "待提升产物不存在"))?;
    if time::OffsetDateTime::parse(&artifact.3, &time::format_description::well_known::Rfc3339)
        .map_err(|_| BackendError::new("invalid_artifact_expiry", "产物过期时间格式无效"))?
        <= time::OffsetDateTime::now_utc()
    {
        let tx = store.conn.unchecked_transaction()?;
        resolve_pending(&tx, approval_id, "expired", &now()?)?;
        tx.execute(
            "UPDATE agent_artifacts SET status = 'expired' WHERE id = ?1",
            params![artifact_id],
        )?;
        let event = Store::event(
            &tx,
            "agent.approval_expired",
            json!({"approvalId": approval_id, "artifactId": artifact_id}),
        )?;
        tx.commit()?;
        emit(app, &event);
        return Err(BackendError::new(
            "approval_expired",
            "产物已超过 24 小时，不能提升",
        ));
    }
    let sources = sources(state, session)?;
    let content = content(serde_json::from_str(&artifact.2)?, &sources)?;
    let lecture = learning::lecture::get(&state.learning, &session.plan_id, &artifact.0, None)?;
    if lecture.status != "done" {
        return Err(BackendError::new(
            "lecture_not_ready",
            "请先完成基础讲义，再批准产物提升",
        ));
    }
    if artifact.1 < 0 || artifact.1 as usize >= lecture.units.len() {
        return Err(BackendError::new(
            "invalid_artifact_scope",
            "产物单元索引超出讲义范围",
        ));
    }
    let version = lecture.version + 1;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    resolve_pending(&tx, approval_id, "approved", &timestamp)?;
    tx.execute("INSERT INTO learning_lecture_units (plan_id, stage_id, version, unit_index, node_id, status, attempt, request_id, content_json, error_code, error_message, updated_at) SELECT plan_id, stage_id, ?1, unit_index, node_id, status, attempt, request_id, content_json, error_code, error_message, ?2 FROM learning_lecture_units WHERE plan_id = ?3 AND stage_id = ?4 AND version = ?5", params![version, timestamp, learning::plan_id(&session.plan_id)?, artifact.0, lecture.version])?;
    tx.execute("UPDATE learning_lecture_units SET content_json = ?1, status = 'done', request_id = NULL, error_code = NULL, error_message = NULL, updated_at = ?2 WHERE plan_id = ?3 AND stage_id = ?4 AND version = ?5 AND unit_index = ?6", params![serde_json::to_string(&content)?, timestamp, learning::plan_id(&session.plan_id)?, artifact.0, version, artifact.1])?;
    tx.execute("UPDATE learning_lectures SET version = ?1, status = 'done', current_unit = ?2, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE plan_id = ?4 AND stage_id = ?5", params![version, lecture.units.len() as i64, timestamp, learning::plan_id(&session.plan_id)?, artifact.0])?;
    tx.execute("INSERT INTO learning_lecture_versions (plan_id, stage_id, version, created_at) VALUES (?1, ?2, ?3, ?4)", params![learning::plan_id(&session.plan_id)?, artifact.0, version, timestamp])?;
    tx.execute(
        "UPDATE agent_artifacts SET status = 'promoted' WHERE id = ?1",
        params![artifact_id],
    )?;
    let event = Store::event(
        &tx,
        "agent.artifact_promoted",
        json!({"approvalId": approval_id, "artifactId": artifact_id, "version": version}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel_approval<R: Runtime>(app: &AppHandle<R>, state: &AgentState, id: &str) -> Result<()> {
    let id = numeric_id(id)?;
    let store = state.store()?;
    let (kind, payload): (String, String) = store
        .conn
        .query_row(
            "SELECT kind, payload_json FROM agent_approvals WHERE id = ?1 AND status = 'pending'",
            params![id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("approval_resolved", "审批不存在或已处理"))?;
    let payload: Value = serde_json::from_str(&payload)?;
    let tx = store.conn.unchecked_transaction()?;
    resolve_pending(&tx, id, "cancelled", &now()?)?;
    if kind == "artifact.promote" {
        if let Some(artifact_id) = payload.get("artifactId").and_then(Value::as_i64) {
            tx.execute(
                "UPDATE agent_artifacts SET status = 'cancelled' WHERE id = ?1",
                params![artifact_id],
            )?;
        }
    }
    let event = Store::event(
        &tx,
        "agent.approval_cancelled",
        json!({"approvalId": id, "kind": kind}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}
