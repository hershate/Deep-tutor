use super::openai::TokenUsage;
use super::{ProviderState, Store};
use crate::backend::commands::emit;
use crate::backend::error::{BackendError, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::HashMap;
use tauri::{AppHandle, Runtime};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};

const SETTINGS_KEY: &str = "usage.settings";

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageSettings {
    pub session_usd: f64,
    pub daily_usd: f64,
    pub regeneration_limit: u8,
}

impl Default for UsageSettings {
    fn default() -> Self {
        Self {
            session_usd: 1.0,
            daily_usd: 10.0,
            regeneration_limit: 2,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RateInput {
    pub provider_id: String,
    pub model: String,
    pub input_per_million: f64,
    pub output_per_million: f64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageRate {
    pub id: i64,
    pub provider_id: String,
    pub provider_name: String,
    pub model: String,
    pub input_per_million: f64,
    pub output_per_million: f64,
    pub updated_at: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageEntry {
    pub request_id: String,
    pub provider_id: String,
    pub provider_name: String,
    pub model: String,
    pub task: String,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cost: Option<f64>,
    pub status: String,
    pub attempts: u32,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageSummary {
    pub today_cost: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cancelled_cost: f64,
    pub failed_cost: f64,
    pub unpriced_requests: u32,
    pub missing_usage: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageDay {
    pub date: String,
    pub cost: f64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub requests: u32,
    pub unpriced_requests: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelUsage {
    pub provider_id: String,
    pub provider_name: String,
    pub model: String,
    pub tokens: u64,
    pub cost: f64,
    pub requests: u32,
    pub unpriced_requests: u32,
    pub share: f64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageOverview {
    pub settings: UsageSettings,
    pub summary: UsageSummary,
    pub days: Vec<UsageDay>,
    pub models: Vec<ModelUsage>,
    pub ledger: Vec<UsageEntry>,
    pub rates: Vec<UsageRate>,
}

fn now() -> Result<String> {
    Ok(OffsetDateTime::now_utc().format(&Rfc3339)?)
}

pub fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS usage_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            input_per_million REAL NOT NULL,
            output_per_million REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(provider_id, model)
        );",
    )?;
    Ok(())
}

pub fn clear_all(conn: &Connection) -> Result<()> {
    conn.execute("DELETE FROM usage_rates", [])?;
    conn.execute(
        "DELETE FROM provider_meta WHERE key = ?1",
        params![SETTINGS_KEY],
    )?;
    Ok(())
}

pub fn settings(state: &ProviderState) -> Result<UsageSettings> {
    let store = state.store()?;
    let saved: Option<String> = store
        .conn
        .query_row(
            "SELECT value FROM provider_meta WHERE key = ?1",
            params![SETTINGS_KEY],
            |row| row.get(0),
        )
        .optional()?;
    match saved {
        Some(value) => Ok(serde_json::from_str(&value)?),
        None => Ok(UsageSettings::default()),
    }
}

pub fn save_settings<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    settings: UsageSettings,
) -> Result<UsageSettings> {
    if !settings.session_usd.is_finite()
        || !settings.daily_usd.is_finite()
        || settings.session_usd < 0.0
        || settings.daily_usd < 0.0
        || !(1..=3).contains(&settings.regeneration_limit)
    {
        return Err(BackendError::invalid(
            "invalid_usage_settings",
            "预算须为不小于 0 的有限金额，方案重生成上限须为 1 到 3 次",
        ));
    }
    let store = state.store()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute("INSERT INTO provider_meta (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = excluded.value", params![SETTINGS_KEY, serde_json::to_string(&settings)?])?;
    let event = Store::event(&tx, "usage.settings_saved", json!({"settings": settings}))?;
    tx.commit()?;
    emit(app, &event);
    Ok(settings)
}

pub fn rates(conn: &Connection) -> Result<Vec<UsageRate>> {
    let mut statement = conn.prepare("SELECT r.id, r.provider_id, p.name, r.model, r.input_per_million, r.output_per_million, r.updated_at FROM usage_rates r LEFT JOIN provider_profiles p ON p.id = r.provider_id ORDER BY r.provider_id, r.model")?;
    let rows = statement.query_map([], |row| {
        Ok(UsageRate {
            id: row.get(0)?,
            provider_id: format!("p{}", row.get::<_, i64>(1)?),
            provider_name: row
                .get::<_, Option<String>>(2)?
                .unwrap_or_else(|| "已删除供应商档案".into()),
            model: row.get(3)?,
            input_per_million: row.get(4)?,
            output_per_million: row.get(5)?,
            updated_at: row.get(6)?,
        })
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

pub fn upsert_rate<R: Runtime>(
    app: &AppHandle<R>,
    state: &ProviderState,
    input: RateInput,
) -> Result<UsageRate> {
    let profile = super::get(state, &input.provider_id)?;
    if !profile.models.contains(&input.model) {
        return Err(BackendError::invalid(
            "invalid_rate_model",
            "费率必须关联供应商档案中的真实模型",
        ));
    }
    if !input.input_per_million.is_finite()
        || !input.output_per_million.is_finite()
        || input.input_per_million < 0.0
        || input.output_per_million < 0.0
        || input.input_per_million > 1_000_000.0
        || input.output_per_million > 1_000_000.0
    {
        return Err(BackendError::invalid(
            "invalid_usage_rate",
            "费率须为有限、非负且合理的金额",
        ));
    }
    let store = state.store()?;
    let provider_id = profile
        .id
        .trim_start_matches('p')
        .parse::<i64>()
        .map_err(|_| BackendError::invalid("invalid_provider_id", "供应商标识无效"))?;
    let timestamp = now()?;
    let tx = store.conn.unchecked_transaction()?;
    tx.execute(
        "INSERT INTO usage_rates (provider_id, model, input_per_million, output_per_million, created_at, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?5) ON CONFLICT(provider_id, model) DO UPDATE SET input_per_million = excluded.input_per_million, output_per_million = excluded.output_per_million, updated_at = excluded.updated_at",
        params![provider_id, input.model, input.input_per_million, input.output_per_million, timestamp],
    )?;
    let event = Store::event(
        &tx,
        "usage.rate_saved",
        json!({"providerId": profile.id, "model": input.model}),
    )?;
    tx.commit()?;
    emit(app, &event);
    rates(&store.conn)?
        .into_iter()
        .find(|rate| rate.provider_id == profile.id && rate.model == input.model)
        .ok_or_else(|| BackendError::new("rate_save_failed", "费率保存后未找到记录"))
}

/// 费率→成本换算（缺 usage 或缺费率返回 None）——聚合口径与 budget_spend
/// SQL 侧一致（缺 usage 或缺费率的行贡献 0）
fn cost_from_rate(rate: Option<(f64, f64)>, usage: &Option<TokenUsage>) -> Option<f64> {
    let usage = usage.as_ref()?;
    let input = usage.input_tokens?;
    let output = usage.output_tokens?;
    let (input_rate, output_rate) = rate?;
    Some(input as f64 * input_rate / 1_000_000.0 + output as f64 * output_rate / 1_000_000.0)
}

fn entry(row: &rusqlite::Row<'_>) -> Result<UsageEntry> {
    let provider_id: i64 = row.get(1)?;
    let model: String = row.get(2)?;
    let raw_usage: Option<String> = row.get(4)?;
    let usage: Option<TokenUsage> = match raw_usage {
        // 75#3：坏 usage_json 计为无用量行（进 unpriced 统计）——原先直穿
        // 使用量总览与 CSV 导出全失败
        Some(value) => serde_json::from_str(&value).unwrap_or(None),
        None => None,
    };
    let rate: Option<(f64, f64)> = match (
        row.get::<_, Option<f64>>(9)?,
        row.get::<_, Option<f64>>(10)?,
    ) {
        (Some(input), Some(output)) => Some((input, output)),
        _ => None,
    };
    Ok(UsageEntry {
        request_id: row.get(0)?,
        provider_id: format!("p{provider_id}"),
        provider_name: row
            .get::<_, Option<String>>(5)?
            .unwrap_or_else(|| format!("已删除供应商 #{provider_id}")),
        model: model.clone(),
        task: row
            .get::<_, Option<String>>(6)?
            .unwrap_or_else(|| "direct".into()),
        input_tokens: usage.as_ref().and_then(|usage| usage.input_tokens),
        output_tokens: usage.as_ref().and_then(|usage| usage.output_tokens),
        cost: cost_from_rate(rate, &usage),
        status: row.get(3)?,
        attempts: row.get(7)?,
        created_at: row.get(8)?,
    })
}

fn entries(conn: &Connection) -> Result<Vec<UsageEntry>> {
    // 34#22：failover 上游行（{request_id}-fN）经 attempts.upstream_request_id
    // 反查逻辑请求取 task——原先精确 join 不到，真实计费行被归为 direct；
    // 53#7：费率一并 LEFT JOIN（usage_rates 按 provider+model 唯一，无行
    // 放大），替代逐行点查的 N+1——provider_requests 是库中最大表

    let mut statement = conn.prepare("SELECT r.request_id, r.provider_id, r.model, r.status, r.usage_json, p.name, routes.task, r.attempts, r.created_at, ur.input_per_million, ur.output_per_million FROM provider_requests r LEFT JOIN provider_profiles p ON p.id = r.provider_id LEFT JOIN provider_route_attempts attempts ON attempts.upstream_request_id = r.request_id LEFT JOIN provider_route_runs routes ON routes.request_id = COALESCE(attempts.request_id, r.request_id) LEFT JOIN usage_rates ur ON ur.provider_id = r.provider_id AND ur.model = r.model ORDER BY r.rowid DESC")?;
    let mut rows = statement.query([])?;
    let mut entries = Vec::new();
    while let Some(row) = rows.next()? {
        entries.push(entry(row)?);
    }
    Ok(entries)
}

fn local_date(value: &str, offset: time::UtcOffset) -> Result<time::Date> {
    Ok(OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| BackendError::new("invalid_persisted_time", "用量时间格式无效"))?
        .to_offset(offset)
        .date())
}

pub fn overview(state: &ProviderState, days: Option<u32>) -> Result<UsageOverview> {
    let days = days.unwrap_or(7);
    if !(1..=31).contains(&days) {
        return Err(BackendError::invalid(
            "invalid_usage_days",
            "用量趋势仅支持 1 到 31 天",
        ));
    }
    let store = state.store()?;
    let entries = entries(&store.conn)?;
    let local = super::super::store::now_local();
    let today = local.date();
    let mut daily = Vec::new();
    for offset in (0..days).rev() {
        let date = today
            .checked_sub(Duration::days(offset as i64))
            .ok_or_else(|| BackendError::new("invalid_usage_days", "用量日期无效"))?;
        let mut day = UsageDay {
            date: date.to_string(),
            cost: 0.0,
            input_tokens: 0,
            output_tokens: 0,
            requests: 0,
            unpriced_requests: 0,
        };
        for entry in entries
            .iter()
            .filter(|entry| local_date(&entry.created_at, local.offset()).ok() == Some(date))
        {
            day.requests += 1;
            day.cost += entry.cost.unwrap_or(0.0);
            day.input_tokens += entry.input_tokens.unwrap_or(0);
            day.output_tokens += entry.output_tokens.unwrap_or(0);
            day.unpriced_requests += u32::from(entry.cost.is_none());
        }
        daily.push(day);
    }
    let today_entries = entries
        .iter()
        .filter(|entry| local_date(&entry.created_at, local.offset()).ok() == Some(today))
        .collect::<Vec<_>>();
    let summary = UsageSummary {
        today_cost: today_entries.iter().filter_map(|entry| entry.cost).sum(),
        input_tokens: today_entries
            .iter()
            .filter_map(|entry| entry.input_tokens)
            .sum(),
        output_tokens: today_entries
            .iter()
            .filter_map(|entry| entry.output_tokens)
            .sum(),
        cancelled_cost: today_entries
            .iter()
            .filter(|entry| entry.status == "cancelled")
            .filter_map(|entry| entry.cost)
            .sum(),
        failed_cost: today_entries
            .iter()
            .filter(|entry| entry.status == "failed")
            .filter_map(|entry| entry.cost)
            .sum(),
        unpriced_requests: today_entries
            .iter()
            .filter(|entry| entry.cost.is_none())
            .count() as u32,
        missing_usage: today_entries
            .iter()
            .filter(|entry| entry.input_tokens.is_none() || entry.output_tokens.is_none())
            .count() as u32,
    };
    let mut models: HashMap<(String, String), ModelUsage> = HashMap::new();
    for entry in &entries {
        let key = (entry.provider_id.clone(), entry.model.clone());
        let model = models.entry(key).or_insert_with(|| ModelUsage {
            provider_id: entry.provider_id.clone(),
            provider_name: entry.provider_name.clone(),
            model: entry.model.clone(),
            tokens: 0,
            cost: 0.0,
            requests: 0,
            unpriced_requests: 0,
            share: 0.0,
        });
        model.tokens += entry.input_tokens.unwrap_or(0) + entry.output_tokens.unwrap_or(0);
        model.cost += entry.cost.unwrap_or(0.0);
        model.requests += 1;
        model.unpriced_requests += u32::from(entry.cost.is_none());
    }
    let total_tokens: u64 = models.values().map(|model| model.tokens).sum();
    let mut models = models.into_values().collect::<Vec<_>>();
    for model in &mut models {
        model.share = if total_tokens == 0 {
            0.0
        } else {
            model.tokens as f64 / total_tokens as f64 * 100.0
        };
    }
    models.sort_by(|a, b| b.tokens.cmp(&a.tokens).then_with(|| a.model.cmp(&b.model)));
    Ok(UsageOverview {
        settings: settings(state)?,
        summary,
        days: daily,
        models,
        ledger: entries.into_iter().take(200).collect(),
        rates: rates(&store.conn)?,
    })
}

// 27#3：ensure_budget 改为 SQL 聚合（budget_spend），原内存版 known_spend 移除

enum BudgetScope {
    Daily {
        shift: String,
        today: String,
    },
    // 31#1：会话口径直接携带 session_id——请求 id 按 run_id 命名（agent-{run}-…），
    // session_id 与 run_id 是两张独立自增表，原先按 agent-{session}-* GLOB
    /// 匹配几乎恒空：护栏从不拦截、撞号时跨会话错账
    Session {
        session_id: i64,
    },
}

/// 27#3：预算聚合下推 SQL——原实现把 provider_requests 全表读入内存（每行
/// JSON 解析 + 费率点查）且在每次模型请求开始前执行，请求启动延迟随历史
/// 线性增长；聚合口径与 cost() 一致（缺 usage 或缺费率的行贡献 0）
fn budget_spend(conn: &Connection, scope: BudgetScope) -> Result<f64> {
    let (clause, values): (&str, Vec<String>) = match scope {
        BudgetScope::Daily { shift, today } => {
            (" AND date(r.created_at, ?1) = ?2", vec![shift, today])
        }
        BudgetScope::Session { session_id } => (
            // 31#1：经 agent_session_id 外键关联（导入经 FK 重映射保持关联）。
            // 34#16：GLOB 兜底只作用于无主行——前端自造型如 agent-{n}-x 的
            // 直连请求不得经字面前缀串扰他人会话预算；
            // 存量行无该列时回退按本会话 run id 的 GLOB 关联
            " AND (r.agent_session_id = ?1 OR (r.agent_session_id IS NULL AND EXISTS (SELECT 1 FROM agent_runs ar WHERE ar.session_id = ?1 AND r.request_id GLOB 'agent-' || ar.id || '-*')))",
            vec![session_id.to_string()],
        ),
    };
    let spend: f64 = conn.query_row(
        &format!(
            "SELECT COALESCE(SUM(CAST(json_extract(r.usage_json, '$.inputTokens') AS REAL) * rates.input_per_million / 1000000.0
                + CAST(json_extract(r.usage_json, '$.outputTokens') AS REAL) * rates.output_per_million / 1000000.0), 0.0)
             FROM provider_requests r JOIN usage_rates rates ON rates.provider_id = r.provider_id AND rates.model = r.model
             WHERE r.usage_json IS NOT NULL
               AND json_extract(r.usage_json, '$.inputTokens') IS NOT NULL
               AND json_extract(r.usage_json, '$.outputTokens') IS NOT NULL{clause}"
        ),
        rusqlite::params_from_iter(values),
        |row| row.get(0),
    )?;
    Ok(spend)
}

/// RFC 3339 偏移 → SQLite 时间修饰符（±HH:MM），供 date(created_at, ?) 本地日过滤
fn offset_modifier(offset: time::UtcOffset) -> String {
    let minutes = offset.whole_seconds() / 60;
    let sign = if minutes < 0 { '-' } else { '+' };
    let minutes = minutes.unsigned_abs();
    format!("{sign}{:02}:{:02}", minutes / 60, minutes % 60)
}

pub fn ensure_budget(state: &ProviderState, session_id: Option<i64>) -> Result<()> {
    let settings = settings(state)?;
    let store = state.store()?;
    let local = super::super::store::now_local();
    let daily = budget_spend(
        &store.conn,
        BudgetScope::Daily {
            shift: offset_modifier(local.offset()),
            today: local.date().to_string(),
        },
    )?;
    if daily >= settings.daily_usd {
        return Err(BackendError::new(
            "daily_budget_exceeded",
            "已记录费用已达到每日预算，未发起新的模型请求",
        ));
    }
    if let Some(session_id) = session_id {
        let session = budget_spend(&store.conn, BudgetScope::Session { session_id })?;
        if session >= settings.session_usd {
            return Err(BackendError::new(
                "session_budget_exceeded",
                "本会话已记录费用达到预算，未发起新的模型请求",
            ));
        }
    }
    Ok(())
}

fn csv(value: impl ToString) -> String {
    let mut value = value.to_string();
    if value.starts_with(['=', '+', '-', '@']) {
        value.insert(0, '\'');
    }
    format!("\"{}\"", value.replace('"', "\"\""))
}

pub fn export_csv(state: &ProviderState) -> Result<String> {
    let entries = entries(&state.store()?.conn)?;
    // 11#7：与其余 CSV 出口一致补 UTF-8 BOM，中文列在 Excel 双击打开不乱码
    let mut output = String::from("\u{feff}request_id,provider,model,task,input_tokens,output_tokens,cost_usd,status,attempts,created_at\r\n");
    for entry in entries {
        output.push_str(
            &[
                csv(entry.request_id),
                csv(entry.provider_name),
                csv(entry.model),
                csv(entry.task),
                csv(entry
                    .input_tokens
                    .map(|value| value.to_string())
                    .unwrap_or_default()),
                csv(entry
                    .output_tokens
                    .map(|value| value.to_string())
                    .unwrap_or_default()),
                csv(entry
                    .cost
                    .map(|value| format!("{value:.10}"))
                    .unwrap_or_default()),
                csv(entry.status),
                csv(entry.attempts),
                csv(entry.created_at),
            ]
            .join(","),
        );
        output.push_str("\r\n");
    }
    Ok(output)
}

#[cfg(test)]
mod tests;
