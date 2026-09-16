import os
import time
import uuid

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8080")
POLL_INTERVAL = 2
POLL_TIMEOUT = 300

st.set_page_config(page_title="PropFirmUnion · Auditoría", layout="wide")
st.title("🛡️ PropFirmUnion — Capa de Evidencia")
st.caption("Sube tus logs crudos de trading y consulta el resultado de la auditoría IA.")

# --------------------------------------------------------------------------- #
# Sidebar: identidad de usuario
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Identidad")
    user_id = st.text_input("User ID", value=f"trader_{uuid.uuid4().hex[:6]}")
    account_id = st.text_input("Account ID", value="ACC-001")

# --------------------------------------------------------------------------- #
# Estado de sesión
# --------------------------------------------------------------------------- #
if "audits" not in st.session_state:
    st.session_state.audits = []   # lista de dicts con audit_id y estado

# --------------------------------------------------------------------------- #
# Subida de logs
# --------------------------------------------------------------------------- #
st.subheader("1. Subir logs")
uploaded = st.file_uploader("Archivo de logs (CSV, JSON, TXT)", type=["csv", "json", "txt", "log"])
manual = st.text_area("…o pega los logs aquí", height=150)

raw_logs = ""
if uploaded is not None:
    raw_logs = uploaded.read().decode("utf-8", errors="replace")
elif manual.strip():
    raw_logs = manual

if st.button("🚀 Enviar a auditoría", type="primary", disabled=not raw_logs):
    payload = {
        "user_id": user_id,
        "account_id": account_id,
        "raw_logs": raw_logs,
    }
    try:
        r = requests.post(f"{API_BASE}/api/v1/audits", json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        st.success(f"✅ Auditoría encolada: `{data['audit_id']}`")
        st.session_state.audits.insert(0, {
            "audit_id": data["audit_id"],
            "submitted_at": data["submitted_at"],
        })
    except requests.HTTPError as e:
        st.error(f"Error {e.response.status_code}: {e.response.text}")
    except Exception as e:
        st.error(f"Error de conexión: {e}")

# --------------------------------------------------------------------------- #
# Consultar una auditoría por ID
# --------------------------------------------------------------------------- #
st.subheader("2. Consultar auditoría por ID")
col1, col2 = st.columns([3, 1])
with col1:
    lookup_id = st.text_input("Audit ID", placeholder="Pega aquí el UUID…")
with col2:
    poll = st.checkbox("Auto-refresh", value=True)

if lookup_id:
    placeholder = st.empty()
    deadline = time.time() + POLL_TIMEOUT

    while True:
        try:
            r = requests.get(f"{API_BASE}/api/v1/audits/{lookup_id}", timeout=10)
            if r.status_code == 404:
                placeholder.warning("Auditoría no encontrada")
                break
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            placeholder.error(f"Error: {e}")
            break

        status = data["status"]
        with placeholder.container():
            icon = {
                "PENDING": "⏳",
                "PROCESSING": "🔄",
                "COMPLETED": "✅",
                "FLAGGED": "🚩",
                "FAILED": "❌",
            }.get(status, "❓")
            st.markdown(f"### {icon} Estado: `{status}`")
            st.json(data)

            if status == "COMPLETED" and data.get("ai_verdict"):
                verdict = data["ai_verdict"]
                st.markdown("#### Veredicto IA")
                cols = st.columns(3)
                cols[0].metric("Fraude", "SÍ" if verdict.get("is_fraudulent") else "NO")
                cols[1].metric("Confianza", f"{verdict.get('confidence', 0):.2f}")
                cols[2].metric("Acción", verdict.get("recommended_action", "—"))
                st.info(verdict.get("summary", ""))

                anomalies = verdict.get("anomalies", [])
                if anomalies:
                    st.markdown("#### Anomalías detectadas")
                    st.dataframe(pd.DataFrame(anomalies), use_container_width=True)
            elif status == "FLAGGED" and data.get("ai_verdict"):
                st.error("🚩 Registro marcado como fraudulento. Revisa el veredicto.")

        if not poll or status in ("COMPLETED", "FLAGGED", "FAILED"):
            break
        if time.time() > deadline:
            st.warning("Timeout de polling")
            break
        time.sleep(POLL_INTERVAL)

# --------------------------------------------------------------------------- #
# Historial del usuario
# --------------------------------------------------------------------------- #
st.subheader("3. Historial del usuario")
if st.button("Refrescar historial"):
    try:
        r = requests.get(f"{API_BASE}/api/v1/users/{user_id}/audits?limit=20", timeout=10)
        r.raise_for_status()
        rows = r.json()
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Sin auditorías para este usuario")
    except Exception as e:
        st.error(f"Error: {e}")
