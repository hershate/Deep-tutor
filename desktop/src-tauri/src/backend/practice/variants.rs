use super::{PracticeState, RubricCriterion};
use crate::backend::error::{BackendError, Result};
use crate::backend::learning::{self, Citation};
use crate::backend::store;
use rusqlite::{params, Connection, OptionalExtension};

pub struct VariantSource {
    pub id: i64,
    pub kind: String,
    pub prompt: String,
    pub options_json: String,
    pub answer_json: String,
    pub hint: String,
    pub explanation: String,
    pub citations: Vec<Citation>,
    pub node_ids: Vec<String>,
    pub rubric: Vec<RubricCriterion>,
    pub difficulty: u8,
    pub stage_id: Option<String>,
    pub feedback_mode: String,
    pub reason: String,
}

impl VariantSource {
    pub fn prompt_json(&self) -> Result<String> {
        Ok(serde_json::to_string(&serde_json::json!({
            "id": self.id,
            "kind": self.kind,
            "prompt": self.prompt,
            "options": serde_json::from_str::<serde_json::Value>(&self.options_json)?,
            "answer": serde_json::from_str::<serde_json::Value>(&self.answer_json)?,
            "hint": self.hint,
            "explanation": self.explanation,
            "citations": self.citations,
            "nodeIds": self.node_ids,
            "rubric": self.rubric,
            "difficulty": self.difficulty,
            "previousMistake": self.reason,
        }))?)
    }
}

fn ensure_column(conn: &Connection, table: &str, name: &str, declaration: &str) -> Result<()> {
    // 46#4：与 learning 共用 store 的幂等补列实现，消除双份内联漂移面
    store::ensure_column(conn, table, name, declaration)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    ensure_column(conn, "practice_sets", "variant_of", "variant_of INTEGER")?;
    ensure_column(
        conn,
        "practice_mistakes",
        "mastered_question_id",
        "mastered_question_id INTEGER",
    )?;
    Ok(())
}

pub fn source(state: &PracticeState, question_id: i64, plan_id: &str) -> Result<VariantSource> {
    let plan_id = learning::plan_id(plan_id)?;
    let store = state.store()?;
    let exists: bool = store.conn.query_row(
        "SELECT EXISTS(SELECT 1 FROM practice_mistakes WHERE question_id = ?1)",
        params![question_id],
        |row| row.get(0),
    )?;
    if !exists {
        return Err(BackendError::new(
            "variant_source_not_mistake",
            "只能从真实错题生成变式练习",
        ));
    }
    store.conn.query_row(
        "SELECT q.id, q.kind, q.prompt, q.options_json, q.answer_json, q.hint, q.explanation, q.citations_json, q.node_ids_json, q.rubric_json, q.difficulty, s.stage_id, s.feedback_mode, m.reason FROM practice_questions q JOIN practice_sets s ON s.id = q.set_id JOIN practice_mistakes m ON m.question_id = q.id WHERE q.id = ?1 AND s.plan_id = ?2",
        params![question_id, plan_id],
        |row| {
            let nodes: String = row.get(8)?;
            let rubric: String = row.get(9)?;
            let citations: String = row.get(7)?;
            Ok(VariantSource {
                id: row.get(0)?,
                kind: row.get(1)?,
                prompt: row.get(2)?,
                options_json: row.get(3)?,
                answer_json: row.get(4)?,
                hint: row.get(5)?,
                explanation: row.get(6)?,
                citations: serde_json::from_str(&citations).unwrap_or_default(),
                node_ids: serde_json::from_str(&nodes).unwrap_or_default(),
                rubric: serde_json::from_str(&rubric).unwrap_or_default(),
                difficulty: row.get::<_, i64>(10)? as u8,
                stage_id: row.get(11)?,
                feedback_mode: row.get(12)?,
                reason: row.get(13)?,
            })
        },
    ).optional()?.ok_or_else(|| BackendError::new("variant_source_out_of_scope", "原错题不属于当前学习方案"))
}

pub fn mark_mastered(
    conn: &Connection,
    variant_question_id: i64,
    correct: bool,
) -> Result<Option<i64>> {
    let source: Option<i64> = conn.query_row(
        "SELECT variant_of FROM practice_questions WHERE id = ?1",
        params![variant_question_id],
        |row| row.get(0),
    )?;
    if !correct {
        return Ok(None);
    }
    let Some(source) = source else {
        return Ok(None);
    };
    // 03#13-25：沿 variant_of 链向上传播到原错题——对变式再生成变式并答对时，
    // 中间错题与原错题一并出“待巩固”，与“自动更新原错题”的文案一致
    let mut chain = vec![source];
    let mut current = source;
    loop {
        let parent: Option<i64> = conn.query_row(
            "SELECT variant_of FROM practice_questions WHERE id = ?1",
            params![current],
            |row| row.get(0),
        )?;
        match parent {
            Some(id) if id != variant_question_id && !chain.contains(&id) => {
                chain.push(id);
                current = id;
            }
            _ => break,
        }
    }
    let mut changed = 0;
    for id in &chain {
        changed += conn.execute(
            "UPDATE practice_mistakes SET mastered = 1, mastered_question_id = ?1 WHERE question_id = ?2",
            params![variant_question_id, id],
        )?;
    }
    if changed == 0 {
        return Err(BackendError::new(
            "variant_source_missing",
            "变式的原错题记录不存在",
        ));
    }
    Ok(Some(*chain.last().unwrap_or(&source)))
}
