from typing import List, Literal

"""
PropFirmUnion - AI Worker
Worker asíncrono que reclama atómicamente auditorías PENDING,
las procesa con Gemini (salida estructurada) y actualiza su estado.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from dotenv import load_dotenv
from google import genai
from google.genai import types

from schemas import AuditVerdict

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "../backend/propfirmunion.db")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "3"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_LOG_CHARS = 50_000
MAX_CONCURRENT_AI = int(os.getenv("MAX_CONCURRENT_AI", "4"))
STALE_PROCESSING_MINUTES = int(os.getenv("STALE_PROCESSING_MINUTES", "15"))

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("worker")

# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    log.critical("Falta GEMINI_API_KEY en el entorno")
    sys.exit(1)

_gemini_client = genai.Client(api_key=_api_key)


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=20000;")
    return conn


def _db_claim_pending(limit: int) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id, user_id, account_id, raw_logs, submitted_at
            FROM audits
            WHERE status = 'PENDING'
            ORDER BY submitted_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE audits SET status='PROCESSING' WHERE id IN ({placeholders})",
                ids,
            )
        conn.execute("COMMIT")
        return rows
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _db_mark_completed(audit_id: str, verdict: dict[str, Any], flagged: bool) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE audits
            SET status = ?, processed_at = ?, ai_verdict = ?
            WHERE id = ?
            """,
            (
                "FLAGGED" if flagged else "COMPLETED",
                datetime.now(timezone.utc).isoformat(),
                json.dumps(verdict),
                audit_id,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _db_mark_failed(audit_id: str, error: str) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE audits
            SET status='FAILED', processed_at=?, error_message=?
            WHERE id=?
            """,
            (datetime.now(timezone.utc).isoformat(), error[:500], audit_id),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _db_reap_stale(minutes: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE audits
            SET status='FAILED',
                processed_at=?,
                error_message='Stale: worker interrumpido durante PROCESSING'
            WHERE status='PROCESSING' AND submitted_at < ?
            """,
            (datetime.now(timezone.utc).isoformat(), cutoff),
        )
        count = cur.rowcount
        conn.execute("COMMIT")
        return count
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Retry helpers
# --------------------------------------------------------------------------- #
async def _run_with_retry(
    fn: Callable[..., Any],
    *args: Any,
    retries: int = 3,
    base_delay: float = 0.5,
    **kwargs: Any,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except sqlite3.OperationalError as exc:
            last_exc = exc
            msg = str(exc).lower()
            if ("locked" in msg or "busy" in msg) and attempt < retries - 1:
                delay = base_delay * (2**attempt)
                log.warning(
                    "SQLite ocupada (intento %d/%d). Reintento en %.1fs",
                    attempt + 1,
                    retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Estado imposible en _run_with_retry")


async def fetch_and_claim_pending(limit: int) -> list[sqlite3.Row]:
    return await _run_with_retry(_db_claim_pending, limit)


async def mark_completed(audit_id: str, verdict: dict[str, Any], flagged: bool) -> None:
    await _run_with_retry(_db_mark_completed, audit_id, verdict, flagged)


async def mark_failed(audit_id: str, error: str) -> None:
    await _run_with_retry(_db_mark_failed, audit_id, error)


# --------------------------------------------------------------------------- #
# Preprocesado + Gemini
# --------------------------------------------------------------------------- #
def preprocess_logs(raw: str) -> str:
    if len(raw) <= MAX_LOG_CHARS:
        return raw
    lines = raw.splitlines()
    if len(lines) <= 800:
        head = raw[: MAX_LOG_CHARS // 2]
        tail = raw[-(MAX_LOG_CHARS // 2) :]
        return f"{head}\n... [truncado {len(raw) - MAX_LOG_CHARS} chars] ...\n{tail}"
    head = "\n".join(lines[:400])
    tail = "\n".join(lines[-400:])
    return f"{head}\n... [truncado {len(lines) - 800} líneas] ...\n{tail}"


_PROMPT_TEMPLATE = """Eres un auditor experto en trading de prop firms.
Analiza los siguientes logs y detecta anomalías como: wash trading, arbitraje de latencia,
copy trading, manipulación de tamaño, operaciones fuera de horario, o cualquier patrón
sospechoso de fraude.

Sé riguroso: cada anomalía debe estar sustentada por evidencia textual del log.
Si no hay anomalías, devuelve is_fraudulent=false, anomalies=[], recommended_action="approve".

LOGS:
{logs}
"""


def _call_gemini_sync(prompt: str) -> AuditVerdict:
    response = _gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AuditVerdict,
            temperature=0.1,
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini devolvió respuesta vacía")
    return AuditVerdict.model_validate_json(response.text)


async def analyze_with_gemini(raw_logs: str) -> AuditVerdict:
    logs = preprocess_logs(raw_logs)
    prompt = _PROMPT_TEMPLATE.format(logs=logs)
    return await asyncio.to_thread(_call_gemini_sync, prompt)


# --------------------------------------------------------------------------- #
# Procesamiento
# --------------------------------------------------------------------------- #
async def process_one(row: sqlite3.Row, semaphore: asyncio.Semaphore) -> None:
    audit_id = row["id"]
    async with semaphore:
        log.info("Procesando auditoría %s (user=%s)", audit_id, row["user_id"])
        try:
            verdict = await analyze_with_gemini(row["raw_logs"])
            is_flagged = verdict.is_fraudulent or verdict.recommended_action == "reject"
            await mark_completed(audit_id, verdict.model_dump(), flagged=is_flagged)
            log.info(
                "[OK] %s -> %s (conf=%.2f, anomalías=%d)",
                audit_id,
                "FLAGGED" if is_flagged else "COMPLETED",
                verdict.confidence,
                len(verdict.anomalies),
            )
        except Exception:
            log.exception("Fallo procesando auditoría %s", audit_id)
            try:
                await mark_failed(audit_id, "Ver logs del worker")
            except Exception:
                log.exception("No se pudo marcar FAILED para %s", audit_id)


# --------------------------------------------------------------------------- #
# Loop principal
# --------------------------------------------------------------------------- #
_shutdown_event = asyncio.Event()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _handler() -> None:
        log.info("Shutdown solicitado")
        _shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handler())


async def main() -> None:
    try:
        reaped = await _run_with_retry(_db_reap_stale, STALE_PROCESSING_MINUTES)
        if reaped:
            log.warning("Reaper: %d registros marcados como FAILED (stale)", reaped)
    except Exception:
        log.exception("Fallo ejecutando reaper inicial")

    log.info(
        "Worker iniciado | db=%s | modelo=%s | batch=%d | poll=%.1fs",
        DB_PATH,
        GEMINI_MODEL,
        BATCH_SIZE,
        POLL_INTERVAL,
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_AI)

    while not _shutdown_event.is_set():
        try:
            pending = await fetch_and_claim_pending(BATCH_SIZE)
            if pending:
                log.info("Reclamados %d registros", len(pending))
                await asyncio.gather(
                    *(process_one(row, semaphore) for row in pending),
                    return_exceptions=True,
                )
            else:
                try:
                    await asyncio.wait_for(
                        _shutdown_event.wait(), timeout=POLL_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            log.info("Loop cancelado")
            raise
        except Exception:
            log.exception("Error en loop principal, continuando...")
            await asyncio.sleep(POLL_INTERVAL)

    log.info("Worker detenido")


def _entrypoint() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _install_signal_handlers(loop)
    try:
        loop.run_until_complete(main())
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


if __name__ == "__main__":
    _entrypoint()
