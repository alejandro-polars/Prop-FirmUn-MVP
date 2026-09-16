use chrono::{DateTime, Utc};
use sqlx::{sqlite::SqlitePoolOptions, SqlitePool};
use uuid::Uuid;

use crate::models::AuditStatusResponse;

pub async fn init_pool(database_url: &str) -> Result<SqlitePool, sqlx::Error> {
    let pool = SqlitePoolOptions::new()
        .max_connections(10)
        .connect(database_url)
        .await?;

    sqlx::query(
        r#"
        CREATE TABLE IF NOT EXISTS audits (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL,
            account_id    TEXT NOT NULL,
            raw_logs      TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'PENDING',
            submitted_at  TEXT NOT NULL,
            processed_at  TEXT,
            ai_verdict    TEXT,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audits_status ON audits(status);
        CREATE INDEX IF NOT EXISTS idx_audits_user   ON audits(user_id);
        "#,
    )
    .execute(&pool)
    .await?;

    Ok(pool)
}

pub async fn insert_pending_audit(
    pool: &SqlitePool,
    user_id: &str,
    account_id: &str,
    raw_logs: &str,
) -> Result<(Uuid, DateTime<Utc>), sqlx::Error> {
    let id = Uuid::new_v4();
    let now = Utc::now();

    sqlx::query(
        r#"
        INSERT INTO audits (id, user_id, account_id, raw_logs, status, submitted_at)
        VALUES (?, ?, ?, ?, 'PENDING', ?)
        "#,
    )
    .bind(id.to_string())
    .bind(user_id)
    .bind(account_id)
    .bind(raw_logs)
    .bind(now.to_rfc3339())
    .execute(pool)
    .await?;

    Ok((id, now))
}

/// Fila "cruda" tal como viene de SQLite (todo TEXT/NULL).
#[derive(sqlx::FromRow)]
struct RawAuditRow {
    id: String,
    status: String,
    submitted_at: String,
    processed_at: Option<String>,
    ai_verdict: Option<String>,
}

fn parse_status(s: &str) -> crate::models::AuditStatus {
    use crate::models::AuditStatus::*;
    match s.to_uppercase().as_str() {
        "PENDING" => Pending,
        "PROCESSING" => Processing,
        "COMPLETED" => Completed,
        "FLAGGED" => Flagged,
        "FAILED" => Failed,
        _ => Pending,
    }
}

fn parse_row(r: RawAuditRow) -> Option<AuditStatusResponse> {
    let id = Uuid::parse_str(&r.id).ok()?;
    let submitted_at = DateTime::parse_from_rfc3339(&r.submitted_at)
        .ok()?
        .with_timezone(&Utc);
    let processed_at = r
        .processed_at
        .as_deref()
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|d| d.with_timezone(&Utc));
    let ai_verdict = r
        .ai_verdict
        .as_deref()
        .and_then(|s| serde_json::from_str(s).ok());

    Some(AuditStatusResponse {
        id,
        status: parse_status(&r.status),
        submitted_at,
        processed_at,
        ai_verdict,
    })
}

pub async fn get_audit_status(
    pool: &SqlitePool,
    id: Uuid,
) -> Result<Option<AuditStatusResponse>, sqlx::Error> {
    let row: Option<RawAuditRow> = sqlx::query_as(
        r#"
        SELECT id, status, submitted_at, processed_at, ai_verdict
        FROM audits WHERE id = ?
        "#,
    )
    .bind(id.to_string())
    .fetch_optional(pool)
    .await?;

    Ok(row.and_then(parse_row))
}

pub async fn list_user_audits(
    pool: &SqlitePool,
    user_id: &str,
    limit: i64,
) -> Result<Vec<AuditStatusResponse>, sqlx::Error> {
    let rows: Vec<RawAuditRow> = sqlx::query_as(
        r#"
        SELECT id, status, submitted_at, processed_at, ai_verdict
        FROM audits
        WHERE user_id = ?
        ORDER BY submitted_at DESC
        LIMIT ?
        "#,
    )
    .bind(user_id)
    .bind(limit)
    .fetch_all(pool)
    .await?;

    Ok(rows.into_iter().filter_map(parse_row).collect())
}
