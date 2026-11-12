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
            "通过公开 DuckDuckGo HTML 检索并实际抓取验证候选网页，只返回真实可访问结果",
            json!({"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 5}}, "required": ["query"]}),
        ),
        definition(
            "web.fetch",
            "安全抓取公开 HTML 或纯文本网页并提取真实文字，不执行网页脚本",
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
            BackendError::new(
                "invalid_tool_arguments",
                format!("工具参数 {key} 必须是非空字符串").as_str(),
            )
        })
}

fn number(arguments: &Value, key: &str) -> Result<i64> {
    let value = arguments.get(key).and_then(Value::as_i64).ok_or_else(|| {
        BackendError::new(
            "invalid_tool_arguments",
            format!("工具参数 {key} 必须是整数").as_str(),
        )
    })?;
    if value <= 0 {
        return Err(BackendError::new(
            "invalid_tool_arguments",
            format!("工具参数 {key} 必须为正数").as_str(),
        ));
    }
    Ok(value)
}

fn index(arguments: &Value, key: &str) -> Result<usize> {
    let value = arguments.get(key).and_then(Value::as_u64).ok_or_else(|| {
        BackendError::new(
            "invalid_tool_arguments",
            format!("工具参数 {key} 必须是非负整数").as_str(),
        )
    })?;
    usize::try_from(value).map_err(|_| {
        BackendError::new(
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
        .map_err(|_| BackendError::new("invalid_artifact_content", "产物不是有效的讲义单元"))?;
    learning::lecture::validate_content(&content, sources)?;
    Ok(content)
}

fn scratch_root(state: &AgentState, session: &AgentSession) -> Result<PathBuf> {
    let root = super::super::security::workspace_path(&state.database_path)
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
        return Err(BackendError::new(
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
        return Err(BackendError::new(
            "invalid_artifact_path",
            "产物路径不得包含绝对路径、父级目录或前缀",
        ));
    }
    if !matches!(
        path.extension().and_then(|extension| extension.to_str()),
        Some("json" | "md" | "txt")
    ) {
        return Err(BackendError::new(
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
    // 107#3：写入/落盘失败（盘满等）须回收 temporary——与 materials 的
    // 99#4 同口径，`?` 提前返回会残留 .tmp 部分文件
    if let Err(error) = (|| -> std::io::Result<()> {
        std::io::Write::write_all(&mut file, bytes)?;
        file.sync_all()
    })() {
        drop(file);
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    drop(file);
    let existed = path.exists();
    if existed {
        // 107#3：rename 失败同样回收 temporary（目标文件未被触碰）
        if let Err(error) = fs::rename(path, &backup) {
            let _ = fs::remove_file(&temporary);
            return Err(error.into());
        }
    }
    match fs::rename(&temporary, path) {
        Ok(()) => {
            // 107#2：清理 .previous 失败不应把已落盘的写入报为失败——
            // `?` 上抛会让 DB 停留旧哈希，此后 read/promote 永久
            // artifact_modified_externally；残留 .previous 由维护孤儿
            // 扫描兜底回收（与 59#1 的 atomic_export 语义对齐）
            let _ = fs::remove_file(backup);
            Ok(())
        }
        Err(error) => {
            if existed {
                // 137#5：回滚 rename 也失败时不再无声——原内容留存 .previous
                // 可手动恢复，并入错误消息告知（materials 侧同型口径）
                if let Err(rollback) = fs::rename(backup, path) {
                    // 139#3：双失败分支提前返回也要回收 temporary——原先
                    // return 先于下方清理执行，agent scratch 目录残留 .tmp
                    // 孤儿（maintenance 孤儿扫描可覆盖，仍按同口径即时回收）
                    let _ = fs::remove_file(&temporary);
                    return Err(BackendError::new(
                        "storage_failed",
                        &format!(
                            "写入失败且回滚未完成（{rollback}）；原内容保留在 .previous 文件中，可手动恢复"
                        ),
                    ));
                }
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
    let store = state.store()?;
    let timestamp = now()?;
    let expires = (time::OffsetDateTime::now_utc() + time::Duration::days(1))
        .format(&time::format_description::well_known::Rfc3339)?;
    let tx = store.conn.unchecked_transaction()?;
    let existing = tx
        .query_row(
            "SELECT id, status FROM agent_artifacts WHERE session_id = ?1 AND path = ?2",
            params![session.id, path],
            |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
        )
        .optional()?;
    // 97#4：已提升的路径冻结——原先覆盖同名产物无条件把行状态重置回
    // committed（promoted→committed 非单调转移），提升痕迹只存于
    // lecture 版本表，产物视图回退为草稿；复用已提升路径要求换新 path。
    // 101#1：检查必须在 atomic_write 之前——原先先覆盖 scratch 文件再报
    // artifact_promoted，DB 行仍是 promoted+旧哈希，此后 read/promote 走
    // read_artifact 哈希校验永久 artifact_modified_externally，新草稿
    // 只存在于无行指向的孤儿文件
    if let Some((_, status)) = &existing {
        if status == "promoted" {
            return Err(BackendError::new(
                "artifact_promoted",
                "该路径产物已提升为正式讲义，请新路径保存新草稿",
            ));
        }
    }
    // 156#6：更新已存在行时先备份旧内容——atomic_write 先落新文件、
    // tx.commit() 后置，commit 失败（busy/落盘错）原先使文件为新哈希而
    // DB 回滚到旧哈希，read_artifact 哈希校验从此恒
    // artifact_modified_externally，该产物 read/edit/promote 全废
    let previous_bytes = if existing.is_some() && target.is_file() {
        std::fs::read(&target).ok()
    } else {
        None
    };
    atomic_write(&target, &bytes)?;
    let id = if let Some((id, _)) = existing {
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
    if let Err(error) = tx.commit() {
        // 156#6：commit 失败补偿——已存在行回写旧内容（或移除本次新建的
        // 孤儿文件），文件与 DB 哈希重新一致，产物可用性保住
        if let Some(previous) = previous_bytes {
            let _ = atomic_write(&target, &previous);
        } else if existing.is_none() {
            let _ = std::fs::remove_file(&target);
        }
        return Err(error.into());
    }
    emit(app, &event);
    Ok(
        json!({"status": "committed", "artifactId": id, "path": path, "sha256": hash, "expiresAt": expires}),
    )
}

fn read_artifact(
    state: &AgentState,
    session: &AgentSession,
    id: i64,
) -> Result<(String, String, usize, LectureContent, String)> {
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
    Ok((
        path,
        stage_id,
        unit_index,
        serde_json::from_str(&content)?,
        expected,
    ))
}

fn approval<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session: &AgentSession,
    kind: &str,
    payload: Value,
) -> Result<Value> {
    let store = state.store()?;
    let payload_text = serde_json::to_string(&payload)?;
    // 97#3：同 kind+payload 的 pending 审批复用同一行——模型在两个步骤各
    // 调一次 artifact.promote（或重试）会产出两条 pending，用户先后批准
    // 造成同一内容双重版本提升；取消另一条又把已提升产物翻成 cancelled
    let existing: Option<i64> = store
        .conn
        .query_row(
            "SELECT id FROM agent_approvals WHERE session_id = ?1 AND kind = ?2 AND payload_json = ?3 AND status = 'pending'",
            params![session.id, kind, payload_text],
            |row| row.get(0),
        )
        .optional()?;
    if let Some(id) = existing {
        return Ok(
            json!({"status": "approval_required", "approvalId": id, "kind": kind, "reused": true}),
        );
    }
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO agent_approvals (session_id, kind, payload_json, status, created_at) VALUES (?1, ?2, ?3, 'pending', ?4)", params![session.id, kind, payload_text, now()?])?;
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
        return Err(BackendError::new(
            "invalid_memory_key",
            "记忆键须为 1 到 64 位字母、数字、短横线或下划线",
        ));
    }
    Ok(value.into())
}

fn memory_value(value: &str) -> Result<String> {
    if value.trim().is_empty() || value.len() > 4000 {
        return Err(BackendError::new(
            "invalid_memory_value",
            "记忆内容不能为空且不能超过 4000 字节",
        ));
    }
    Ok(value.into())
}

/// 97#2：工具结果统一上限。此前完整结果原样入库并整包进事件 payload——
/// web.fetch 的 256KB 正文会三级放大（DB 行 → 事件 → history.load 再读回
/// 再落库，存储随步数滚雪球）。超 64KB 的结果替换为状态 + 头部预览并
/// 注明截断；发往模型的份额另有 truncate_for_model 的 16KB 二级截断，
/// 模型可见内容不变，仅持久层/事件面收敛
fn cap_tool_result(result: Value) -> Value {
    let serialized = match serde_json::to_string(&result) {
        Ok(text) => text,
        Err(_) => {
            return json!({"status": "failed", "error": {"code": "invalid_tool_result", "message": "工具结果无法序列化"}})
        }
    };
    if serialized.len() <= 64 * 1024 {
        return result;
    }
    let preview: String = serialized.chars().take(2048).collect();
    json!({
        "status": result.get("status").cloned().unwrap_or_else(|| json!("done")),
        "truncated": true,
        "notice": "工具结果超过 64KB 上限，已截断为头部预览；请缩小读取范围（如 history.load 降低条数）后重试",
        "preview": preview,
    })
}

/// 把登记为 running 的工具调用收尾为 failed（参数校验失败路径专用）。
fn finish_failed_tool_call(store: &Store, id: &str, message: &str) -> Result<()> {
    store.conn.execute(
        "UPDATE agent_tool_calls SET status = 'failed', result_json = ?1, finished_at = ?2 WHERE id = ?3 AND status = 'running'",
        params![
            serde_json::to_string(&json!({"status": "failed", "error": message}))?,
            now()?,
            id
        ],
    )?;
    Ok(())
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
        return Err(BackendError::new(
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
    // 参数校验失败也要收尾登记行：否则 status 停留 running，
    // 同 id 重放会命中 tool_active 被永久拒绝（07#2）
    let arguments: Value = match serde_json::from_str::<Value>(&call.arguments) {
        Ok(value) if value.is_object() => value,
        Ok(_) => {
            finish_failed_tool_call(&store, &call.id, "工具参数必须是 JSON 对象")?;
            return Err(BackendError::new(
                "invalid_tool_arguments",
                "工具参数必须是 JSON 对象",
            ));
        }
        Err(_) => {
            finish_failed_tool_call(&store, &call.id, "工具参数不是有效 JSON")?;
            return Err(BackendError::new(
                "invalid_tool_arguments",
                "工具参数不是有效 JSON",
            ));
        }
    };
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
                        .map_err(|_| BackendError::new("invalid_reference", "产物引用无效"))?;
                    let (_, _, _, content, _) = read_artifact(state, session, id)?;
                    Ok(json!({"status": "done", "content": content}))
                }
                _ => Err(BackendError::new(
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
                let (path, stage_id, unit_index, content, _) = read_artifact(state, session, id)?;
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
                        BackendError::new("invalid_artifact_content", "编辑后的讲义结构无效")
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
            "web.search" => {
                // 122#4：limit 钳制到目录声明区间（1-5，对齐 history.load 的
                // clamp 风格）——原先超界值透传 web 层校验整步报错，浪费一步
                let limit = arguments
                    .get("limit")
                    .and_then(Value::as_u64)
                    .unwrap_or(3)
                    .clamp(1, 5);
                super::super::web::search(string(&arguments, "query")?, limit as usize)
                    .map(|result| json!({"status":"done","search":result}))
            }
            "web.fetch" => super::super::web::fetch(string(&arguments, "url")?)
                .map(|page| json!({"status":"done","page":page})),
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
                // 34#23：读活库而非 run 开始时的会话快照——多步 run 中模型
                // 调用本工具应看到已落库的本 run 消息（快照读不到，与工具
                // 描述矛盾并诱导重复提问）
                let store = state.store()?;
                let mut statement = store.conn.prepare(
                    "SELECT role, content FROM agent_messages WHERE session_id = ?1 ORDER BY id DESC LIMIT ?2",
                )?;
                let messages = statement
                    .query_map(rusqlite::params![session.id, limit], |row| {
                        Ok(json!({
                            "role": row.get::<_, String>(0)?,
                            "content": row.get::<_, String>(1)?,
                        }))
                    })?
                    .collect::<std::result::Result<Vec<_>, _>>()?;
                drop(statement);
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
                let (_, _, _, _, sha256) = read_artifact(state, session, id)?;
                approval(
                    app,
                    state,
                    session,
                    "artifact.promote",
                    json!({"artifactId": id, "sha256": sha256}),
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
    // 101#3：cap 预览只落持久层（agent_tool_calls）与事件副本——返回
    // 原始结果给 run_loop，模型份额沿用 truncate_for_model 的 16KB 截断
    //（97#2 前的行为）；原先模型对 >64KB 结果只能看到 2048 字符头部预览，
    // 回答质量回归且重复抓取同一页面反复命中同一截断。
    // 122#3：该口径只在首次执行成立——resume 命中重放分支（上方
    // registered==0）时返回的是持久层 cap 副本，>64KB 结果对模型退化为
    // 预览；重执行需放开幂等键（会重复产生副作用），按预览恢复是取舍
    let persisted = cap_tool_result(result.clone());
    let status = persisted
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("failed");
    let tx = store.conn.unchecked_transaction()?;
    let changed = tx.execute(
        "UPDATE agent_tool_calls SET status = ?1, result_json = ?2, finished_at = ?3 WHERE id = ?4 AND status = 'running'",
        params![status, serde_json::to_string(&persisted)?, now()?, call.id],
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
        json!({"runId": run_id, "toolCallId": call.id, "name": call.name, "status": status, "result": persisted}),
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
                    .ok_or_else(|| BackendError::new("invalid_approval", "记忆审批缺少键"))?,
            )?;
            let value = memory_value(
                payload
                    .get("value")
                    .and_then(Value::as_str)
                    .ok_or_else(|| BackendError::new("invalid_approval", "记忆审批缺少内容"))?,
            )?;
            // 31#42：IMMEDIATE——与 25#18 注释宣称一致，DEFERRED 下配额计数仍处读快照
            let tx = store.write_transaction()?;
            // 122#9：key_exists 复查挪进写事务内（IMMEDIATE 下天然串行）——
            // 原先事务外读取，同键 memory.delete 审批并发提交删除后，save
            // 以「覆盖已有键」路径插入新行，单槽位配额差一
            // 10#18：agent_memory 是 agent 域此前唯一无条数配额的持久写入口；
            // 覆盖已有键不新增条数，新键写入前过配额
            // 25#18：配额计数与插入同事务，关闭 check-then-insert 并发窗口
            let key_exists: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM agent_memory WHERE key = ?1)",
                params![key],
                |row| row.get(0),
            )?;
            if !key_exists {
                store.ensure_quota_in_tx(&tx, "agent_memory", 1)?;
            }
            tx.execute("INSERT INTO agent_memory (key, value, updated_at) VALUES (?1, ?2, ?3) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", params![key, value, now()?])?;
            resolve_pending(&tx, id, "approved", &now()?)?;
            let event = Store::event(
                &tx,
                "agent.memory_changed",
                // 129#6：补归属键——原先既无 sessionId 又无 runId，事件驱动
                // 消费者需反查 approvals 表才能关联会话
                json!({"approvalId": id, "sessionId": session.id, "key": key, "action": "saved"}),
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
                    .ok_or_else(|| BackendError::new("invalid_approval", "记忆审批缺少键"))?,
            )?;
            let tx = store.conn.unchecked_transaction()?;
            tx.execute("DELETE FROM agent_memory WHERE key = ?1", params![key])?;
            resolve_pending(&tx, id, "approved", &now()?)?;
            let event = Store::event(
                &tx,
                "agent.memory_changed",
                json!({"approvalId": id, "sessionId": session.id, "key": key, "action": "deleted"}),
            )?;
            tx.commit()?;
            emit(app, &event);
            Ok(())
        }
        "artifact.promote" => {
            let artifact_id = payload
                .get("artifactId")
                .and_then(Value::as_i64)
                .ok_or_else(|| BackendError::new("invalid_approval", "产物审批缺少 ID"))?;
            let expected_sha256 = payload
                .get("sha256")
                .and_then(Value::as_str)
                .filter(|value| {
                    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
                })
                .ok_or_else(|| {
                    BackendError::new("approval_outdated", "旧审批未绑定产物哈希，请重新请求提升")
                })?;
            promote(app, state, &session, id, artifact_id, expected_sha256)
        }
        _ => Err(BackendError::new("invalid_approval", "未知审批类型")),
    }
}

/// 156#1：promote 拒绝分支收口 pending 审批（101#2 只修了
/// artifact_not_committed 分支）——拒绝时审批停留 pending 使审批卡
/// 永驻且反复报错；统一 resolve 为 expired 并发事件
fn reject_with_pending<R: Runtime>(
    app: &AppHandle<R>,
    store: &Store,
    approval_id: i64,
    artifact_id: i64,
    code: &str,
    message: &str,
) -> BackendError {
    let result = (|| -> Result<()> {
        let tx = store.conn.unchecked_transaction()?;
        resolve_pending(&tx, approval_id, "expired", &now()?)?;
        let event = Store::event(
            &tx,
            "agent.approval_expired",
            json!({"approvalId": approval_id, "artifactId": artifact_id}),
        )?;
        tx.commit()?;
        emit(app, &event);
        Ok(())
    })();
    if let Err(error) = result {
        eprintln!("收口审批 {approval_id} 失败：{}", error.message);
    }
    BackendError::new(code, message)
}

fn promote<R: Runtime>(
    app: &AppHandle<R>,
    state: &AgentState,
    session: &AgentSession,
    approval_id: i64,
    artifact_id: i64,
    expected_sha256: &str,
) -> Result<()> {
    let store = state.store()?;
    // 97#8：产物行已被维护 purge 物理删除时，同步 resolve 审批为 expired，
    // 不留死 pending 条目（promote 自身的 24h 过期路径会 resolve，
    // 产物行被删的路径原先不会）
    let artifact = match store.conn.query_row("SELECT stage_id, unit_index, expires_at, sha256, status FROM agent_artifacts WHERE id = ?1 AND session_id = ?2", params![artifact_id, session.id], |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?, row.get::<_, String>(2)?, row.get::<_, String>(3)?, row.get::<_, String>(4)?))).optional() {
        Ok(Some(artifact)) => artifact,
        Ok(None) => {
            let tx = store.conn.unchecked_transaction()?;
            if resolve_pending(&tx, approval_id, "expired", &now()?).is_ok() {
                let event = Store::event(&tx, "agent.approval_expired", json!({"approvalId": approval_id, "artifactId": artifact_id}))?;
                tx.commit()?;
                emit(app, &event);
            }
            return Err(BackendError::new(
                "not_found",
                "待提升产物已被清理，审批已过期",
            ));
        }
        Err(error) => return Err(error.into()),
    };
    // 97#3：产物已提升（或已取消/过期）时拒绝再次 promote——同一产物的
    // 第二条审批（去重缝隙漏入或历史遗留）原先检查全通过，重复批准造成
    // 相同内容双重版本提升
    // 101#2：拒绝分支同步 resolve 审批为 expired——原先直接 return，该
    // 审批永久停留 pending，用户批准反复「批准失败」只能手动取消
    // （与 97#8 的 purge 分支对称收口）
    if artifact.4 != "committed" {
        let tx = store.conn.unchecked_transaction()?;
        if resolve_pending(&tx, approval_id, "expired", &now()?).is_ok() {
            let event = Store::event(
                &tx,
                "agent.approval_expired",
                json!({"approvalId": approval_id, "artifactId": artifact_id}),
            )?;
            tx.commit()?;
            emit(app, &event);
        }
        return Err(BackendError::new(
            "artifact_not_committed",
            "产物已处理（提升/取消/过期），审批已过期，不能重复提升",
        ));
    }
    if time::OffsetDateTime::parse(&artifact.2, &time::format_description::well_known::Rfc3339)
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
    // 156#1：read_artifact 抛错（如 artifact_modified_externally）的传播
    // 路径与三个拒绝分支统一收口 pending——原先直接 return 使审批卡永驻
    let (_, stage_id, unit_index, current, actual_sha256) =
        match read_artifact(state, session, artifact_id) {
            Ok(value) => value,
            Err(error) => {
                return Err(reject_with_pending(
                    app,
                    &store,
                    approval_id,
                    artifact_id,
                    &error.code,
                    &error.message,
                ))
            }
        };
    if artifact.3 != expected_sha256
        || actual_sha256 != expected_sha256
        || stage_id != artifact.0
        || unit_index as i64 != artifact.1
    {
        return Err(reject_with_pending(
            app,
            &store,
            approval_id,
            artifact_id,
            "artifact_approval_stale",
            "产物已在请求批准后改变，请重新请求提升",
        ));
    }
    let sources = sources(state, session)?;
    let content = content(serde_json::to_value(current)?, &sources)?;
    let lecture = learning::lecture::get(&state.learning, &session.plan_id, &artifact.0, None)?;
    if lecture.status != "done" {
        return Err(reject_with_pending(
            app,
            &store,
            approval_id,
            artifact_id,
            "lecture_not_ready",
            "请先完成基础讲义，再批准产物提升",
        ));
    }
    if artifact.1 < 0 || artifact.1 as usize >= lecture.units.len() {
        return Err(reject_with_pending(
            app,
            &store,
            approval_id,
            artifact_id,
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
    let promoted = tx.execute("UPDATE learning_lectures SET version = ?1, status = 'done', current_unit = ?2, error_code = NULL, error_message = NULL, updated_at = ?3 WHERE plan_id = ?4 AND stage_id = ?5 AND version = ?6", params![version, lecture.units.len() as i64, timestamp, learning::plan_id(&session.plan_id)?, artifact.0, lecture.version])?;
    if promoted != 1 {
        return Err(BackendError::new(
            "lecture_version_conflict",
            "基础讲义已更新，请基于新版本重新生成 scratch 产物",
        ));
    }
    tx.execute("INSERT INTO learning_lecture_versions (plan_id, stage_id, version, created_at) VALUES (?1, ?2, ?3, ?4)", params![learning::plan_id(&session.plan_id)?, artifact.0, version, timestamp])?;
    tx.execute(
        "UPDATE agent_artifacts SET status = 'promoted' WHERE id = ?1",
        params![artifact_id],
    )?;
    let event = Store::event(
        &tx,
        "agent.artifact_promoted",
        json!({"sessionId": session.id, "approvalId": approval_id, "artifactId": artifact_id, "planId": session.plan_id, "stageId": artifact.0, "version": version}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}

pub fn cancel_approval<R: Runtime>(app: &AppHandle<R>, state: &AgentState, id: &str) -> Result<()> {
    let id = numeric_id(id)?;
    let store = state.store()?;
    let (kind, payload, session_id): (String, String, i64) = store
        .conn
        .query_row(
            "SELECT kind, payload_json, session_id FROM agent_approvals WHERE id = ?1 AND status = 'pending'",
            params![id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .optional()?
        .ok_or_else(|| BackendError::new("approval_resolved", "审批不存在或已处理"))?;
    let payload: Value = serde_json::from_str(&payload)?;
    let tx = store.conn.unchecked_transaction()?;
    resolve_pending(&tx, id, "cancelled", &now()?)?;
    if kind == "artifact.promote" {
        if let Some(artifact_id) = payload.get("artifactId").and_then(Value::as_i64) {
            // 97#3：仅在产物仍为 committed 时置 cancelled——另一条审批已把
            // 产物提升为 promoted 后取消本条，原先无条件 UPDATE 把提升过
            // 的产物翻成 cancelled，状态与「讲义版本已实际提升」相反
            tx.execute(
                "UPDATE agent_artifacts SET status = 'cancelled' WHERE id = ?1 AND status = 'committed'",
                params![artifact_id],
            )?;
        }
    }
    let event = Store::event(
        &tx,
        "agent.approval_cancelled",
        json!({"approvalId": id, "sessionId": session_id, "kind": kind}),
    )?;
    tx.commit()?;
    emit(app, &event);
    Ok(())
}
