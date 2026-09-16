
propfirmunion/
├── README.md
├── docker-compose.yml
├── backend/                    # Rust + Axum
│   ├── Cargo.toml
│   ├── .env
│   ├── Dockerfile
│   └── src/
│       ├── main.rs
│       ├── state.rs
│       ├── models.rs
│       ├── errors.rs
│       ├── db.rs
│       └── routes.rs
├── ai_worker/                  # Python + Gemini
│   ├── worker.py
│   ├── schemas.py
│   ├── requirements.txt
│   ├── .env
│   └── Dockerfile
└── frontend/                   # Streamlit
    ├── app.py
    ├── requirements.txt
    └── Dockerfile
