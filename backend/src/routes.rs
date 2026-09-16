use axum::{
    extract::{Path, Query, State},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use uuid::Uuid;

use crate::{
    db,
    errors::AppError,
    models::{AuditStatus, AuditStatusResponse, SubmitLogsRequest, SubmitLogsResponse},
    state::AppState,
};

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/api/v1/audits", post(submit_logs))
        .route("/api/v1/audits/:id", get(get_status))
        .route("/api/v1/users/:user_id/audits", get(list_by_user))
        .with_state(state)
}

async fn health() -> &'static str {
    "OK"
}

async fn submit_logs(
    State(state): State<AppState>,
    Json(payload): Json<SubmitLogsRequest>,
) -> Result<Json<SubmitLogsResponse>, AppError> {
    if payload.raw_logs.is_empty() {
        return Err(AppError::BadRequest("raw_logs no puede estar vacío".into()));
    }
    if payload.raw_logs.len() > 10 * 1024 * 1024 {
        return Err(AppError::BadRequest("raw_logs excede 10MB".into()));
    }
    if payload.user_id.is_empty() || payload.account_id.is_empty() {
        return Err(AppError::BadRequest(
            "user_id y account_id son obligatorios".into(),
        ));
    }

    let (id, submitted_at) = db::insert_pending_audit(
        &state.pool,
        &payload.user_id,
        &payload.account_id,
        &payload.raw_logs,
    )
    .await?;

    tracing::info!(audit_id = %id, user = %payload.user_id, "Auditoría encolada");

    Ok(Json(SubmitLogsResponse {
        audit_id: id,
        status: AuditStatus::Pending,
        submitted_at,
        message: "Logs recibidos. Auditoría en cola.".into(),
    }))
}

async fn get_status(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Json<AuditStatusResponse>, AppError> {
    db::get_audit_status(&state.pool, id)
        .await?
        .map(Json)
        .ok_or_else(|| AppError::NotFound(format!("Auditoría {id} no existe")))
}

#[derive(Deserialize)]
struct ListQuery {
    #[serde(default = "default_limit")]
    limit: i64,
}
fn default_limit() -> i64 {
    20
}

async fn list_by_user(
    State(state): State<AppState>,
    Path(user_id): Path<String>,
    Query(q): Query<ListQuery>,
) -> Result<Json<Vec<AuditStatusResponse>>, AppError> {
    let limit = q.limit.clamp(1, 100);
    let rows = db::list_user_audits(&state.pool, &user_id, limit).await?;
    Ok(Json(rows))
}
