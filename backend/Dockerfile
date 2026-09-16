# ---------- Etapa 1: builder ----------
FROM rust:1.82-slim AS builder

WORKDIR /app

# 1) Cachear dependencias: copiamos solo los manifiestos y compilamos un dummy
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs && \
    cargo build --release && \
    rm -rf src

# 2) Ahora copiamos el código real (solo se recompila lo tuyo, no las deps)
COPY src ./src
# Touch para forzar la recompilación del binario real (por si Cargo no detecta cambios)
RUN touch src/main.rs && cargo build --release

# ---------- Etapa 2: runtime ----------
FROM debian:bookworm-slim

# Solo runtime mínimo
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Usuario no-root
RUN useradd -r -u 10001 -m appuser

COPY --from=builder /app/target/release/propfirmunion-backend /usr/local/bin/propfirmunion-backend

USER appuser

EXPOSE 8080

CMD ["propfirmunion-backend"]
