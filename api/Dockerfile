FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# LightGBM necesita la librería OpenMP (libgomp). Sin ella, el contenedor se cae al importar el modelo.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /uvx /bin/

# Solo las dependencias de la API (api/pyproject.toml). Sin xgboost, pyspark ni CUDA.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-install-project

# Código del paquete (fuente único, en src/) y el modelo de R1
COPY src/calcusol /app/src/calcusol
COPY models/radiacion/v1 /app/models/radiacion/v1
# En Windows el modelo puede tener saltos de línea \r\n y LightGBM no los lee: se pasan a \n
RUN sed -i 's/\r$//' /app/models/radiacion/v1/modelo_lgbm.txt
COPY api/main.py /app/main.py

ENV PYTHONPATH=/app/src
ENV MODELO_RUTA=/app/models/radiacion/v1/modelo_lgbm.txt

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
