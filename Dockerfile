# ---------------------------------------------------------------------------
# PagLuz — Agente IA (backend FastAPI + painel admin)
# ---------------------------------------------------------------------------
# Single-stage, imagem slim, usuário não-root, dados em /data (volume).
# IMPORTANTE: rode com 1 worker apenas. O queue_manager mantém estado em
# memória (fila por remoteJid, timers de debounce), então múltiplos workers
# quebrariam o agrupamento de mensagens. Para escalar horizontalmente seria
# necessário mover o estado para Redis.
# ---------------------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Deps de sistema: build-essential para wheels que precisem compilar
# (removido logo após o install para manter a imagem pequena).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Camada de dependências (cache friendly)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Código da aplicação
COPY app ./app

# Diretório de dados (SQLite: sessions.db + admin.db) — montar como volume
RUN mkdir -p /data \
 && useradd -u 1000 -m -s /bin/bash app \
 && chown -R app:app /app /data

USER app

# Defaults de produção: bancos no volume /data
ENV AGENT_DB_FILE=/data/sessions.db \
    ADMIN_DB_FILE=/data/admin.db \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=5).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
