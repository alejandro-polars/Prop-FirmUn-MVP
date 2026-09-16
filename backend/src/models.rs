use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::Type, PartialEq)]
#[sqlx(type_name = "TEXT", rename_all = "UPPERCASE")]
#[serde(rename_all = "UPPERCASE")]
pub enum AuditStatus {
    Pending,
    Processing,
    Completed,
    Flagged,
    Failed,
}

#[derive(Debug, Deserialize)]
pub struct SubmitLogsRequest {
    pub user_id: String,
    pub account_id: String,
    pub raw_logs: String,
    pub metadata: Option<serde_json::Value>,
}

#[derive(Debug, Serialize)]
pub struct SubmitLogsResponse {
    pub audit_id: Uuid,
    pub status: AuditStatus,
    pub submitted_at: DateTime<Utc>,
    pub message: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct AuditStatusResponse {
    pub id: Uuid,
    pub status: AuditStatus,
    pub submitted_at: DateTime<Utc>,
    pub processed_at: Option<DateTime<Utc>>,
    pub ai_verdict: Option<serde_json::Value>,
}
