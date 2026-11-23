
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /uvx /bin/

# Entorno virtual fuera del volumen montado
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${JAVA_HOME}/bin:${PATH}"

# Instalar dependencias durante la construcción de la imagen
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

CMD ["python"]