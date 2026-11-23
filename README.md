# Calcusol · Proyecto integrador 1

## Idea principal

Calcusol responde una pregunta: ¿conviene instalar paneles solares en una casa de Colombia y en cuántos años se recupera la inversión?

Para responderla necesitamos saber cuánto sol recibe esa casa. Hay dos fuentes:

- **NASA:** estima la radiación desde satélites para todo el país, pero tiende a sobreestimarla.
- **IDEAM:** mide la radiación en tierra, pero solo en unas pocas estaciones.

Un modelo de aprendizaje automático aprende a corregir los datos de NASA usando las mediciones del IDEAM. Con esa radiación corregida:

1. Un motor solar (`pvlib`) calcula cuánta energía produce un panel.
2. Una calculadora financiera la convierte en ahorro en la factura y en años de Payback.

## Flujo de datos

Los datos pasan por tres capas en la nube (AWS):

- **Bronze:** datos crudos, tal como llegan de NASA e IDEAM.
- **Silver:** datos limpios y unidos por estación y hora.
- **Gold:** la tabla final con la que se entrena el modelo (3.573.402 registros, 123 estaciones en 28 departamentos, 2005–2022).

```
NASA POWER + IDEAM → bronze → silver → gold → modelo de corrección (R1) → física y finanzas (R3) → API → dashboard
                                            ↘ evaluación del modelo (R2)
```

## El modelo

LightGBM de 17 variables que predice el **residuo** (medición del IDEAM menos estimación de NASA); la radiación corregida es la de NASA más ese residuo. El modelo oficial está en [`models/radiacion/v1/`](models/radiacion/v1/) (`modelo_lgbm.txt` y `metadatos.json`); se abre directamente, sin convertirlo a `.joblib`.

Resultados con el modelo v1, frente a NASA sin corregir:

| Examen | Error mensual | Mejora mensual | Mejora por hora |
|---|---|---|---|
| Espacial (24 estaciones nunca vistas, la cifra que se defiende) | 60,80 → 47,73 Wh/m² | 21,5 % | 4,6 % |
| Temporal (estaciones conocidas, 2021 en adelante) | 56,47 → 18,44 Wh/m² | 67,3 % | 14,2 % |
| Validación cruzada de 112 sitios | | 13,5 % | 3,5 % |

El sesgo de NASA baja de 12,3 % a 2,8 % en estaciones nuevas. El detalle y los límites están en [`docs/modelo.md`](docs/modelo.md).

## Estructura del repositorio

| Carpeta | Qué contiene |
|---|---|
| `src/calcusol/` | Paquete único con toda la lógica: `bronze`, `silver`, `gold`, `ml`, `fisica_finanzas`, `validacion`, `lambdas`, `stepfunctions` y `auditoria` |
| `src/calcusol/ml/` | Variables, entrenamiento, evaluación, comparación de modelos, búsqueda de hiperparámetros, registro y promoción de versiones |
| `src/calcusol/validacion/` | `evaluar_modelo.py` y el Excel de evaluación que lee R3 (`resultados/evaluacion_lightgbm.xlsx`) |
| `src/calcusol/fisica_finanzas/` | Motor solar y financiero de R3, su ficha de supuestos (`config/config_r3.yaml`) y el notebook paso a paso |
| `api/` | API FastAPI (modelo de radiación y cálculos de R3), con su propio `pyproject.toml` |
| `dashboard/` | Dashboard web (HTML, CSS y JavaScript) y los programas que generan sus datos |
| `models/` | Registro de versiones del modelo (`radiacion/v1/`) |
| `glue/`, `aws/`, `athena/`, `infra/mlops/` | Trabajos de Glue, roles, consultas de Athena y despliegue del entrenamiento |
| `notebooks/` | Notebooks para correr en Colab (comparación de modelos y evaluación) |
| `tests/` | Pruebas de `pytest` (modelo activo, registro, API, R3) |
| `docs/` | Documentación técnica (ver abajo) |
| `reports/` | Reportes de las evaluaciones y auditorías |

`src/calcusol/rutas.py` reúne las rutas compartidas (el parquet de gold, el modelo y el Excel de evaluación). Dos variables de entorno cambian de dónde se lee: `CALCUSOL_DATA_ROOT` (raíz de los datos, local o `s3://`) y `CALCUSOL_MODELOS_ROOT` (raíz del registro de modelos); `MODELO_RUTA` fuerza el archivo del modelo.

## Puesta en marcha

Requisitos: Python 3.11, [`uv`](https://docs.astral.sh/uv/) y, para las etapas con Spark, Docker.

```bash
uv sync --frozen                      # instala las dependencias del proyecto
docker compose build                  # imagen con Spark, Java y las dependencias
```

Los comandos se pueden correr dentro del contenedor, con `docker compose run --rm calcusol uv run python -m calcusol.…`, o directamente con Python desde la raíz del repositorio.

### Datos

```bash
python -m calcusol.pipeline                       # silver → gold → validación (limpieza completa, con Spark)
python -m calcusol.gold.consolidar                # gold particionado → un solo parquet: data/gold/dataset_modelo.parquet
```

La ingesta de bronze se describe en `src/calcusol/bronze/` (`python -m calcusol.bronze.pipeline`). Los datos viven en `data/` (fuera de git).

### Modelo

```bash
python -m calcusol.ml.train                       # entrena una versión nueva (nunca sobrescribe v1)
python -m calcusol.ml.evaluate                    # evalúa en los tres exámenes (temporal, espacial y espacio-temporal)
python src/calcusol/validacion/evaluar_modelo.py  # evaluación completa con validación cruzada; genera el Excel de R3
```

`evaluar_modelo.py` tarda varias horas (de 2 a 3 en un computador modesto). Sus opciones se pueden cambiar con variables `EVALUAR_*` (por ejemplo `EVALUAR_CV_CURVA=false`), y [`notebooks/evaluar_modelo.ipynb`](notebooks/evaluar_modelo.ipynb) la corre en Colab con más memoria.

Para comparar familias de modelos y buscar hiperparámetros hacen falta `xgboost` y `optuna`:

```bash
uv sync --frozen --extra comparacion              # o: pip install xgboost optuna
python -m calcusol.ml.comparar_modelos --muestra 0.25
python -m calcusol.ml.buscar_hiperparametros --trials 25 --muestra 0.25
```

Con todas las filas es pesado; [`notebooks/comparacion_modelos.ipynb`](notebooks/comparacion_modelos.ipynb) lo corre en Colab, con GPU para XGBoost. El versionado, la promoción y el rollback de modelos están en [`docs/mlops.md`](docs/mlops.md).

### Física y finanzas (R3)

El notebook [`R3_paso_a_paso_V5_1.ipynb`](src/calcusol/fisica_finanzas/R3_paso_a_paso_V5_1.ipynb) recorre el cálculo completo: año típico de 8.760 horas, producción con `pvlib`, balance con el consumo, factura con la regla de la CREG 174 de 2021, y Payback, VPN y TIR para tres lugares con tres fuentes de radiación (IDEAM, NASA y el modelo). Todos los supuestos están en `config/config_r3.yaml`.

### API

```bash
uv run --project api uvicorn main:app --app-dir api --port 8000
```

Con `PYTHONPATH=src` y `MODELO_RUTA=models/radiacion/v1/modelo_lgbm.txt` definidos (también se puede construir la imagen con `docker build -f api/Dockerfile -t calcusol-api .`). La documentación interactiva queda en `http://localhost:8000/docs`.

| Ruta | Qué hace |
|---|---|
| `GET /health` | Estado y modelo en servicio |
| `POST /predict` | Corrige la radiación de NASA de una hora |
| `GET /v1/supuestos`, `GET /v1/parametros` | Ficha de supuestos de R3 |
| `POST /v1/produccion`, `/v1/consumo`, `/v1/ahorro`, `/v1/finanzas` | Pasos del cálculo por separado |
| `POST /v1/simulacion`, `/v1/simulacion/climatologia` | Cálculo completo desde una serie horaria o una climatología (el dashboard usa la segunda) |
| `POST /v1/punto` | Descarga NASA de un punto, corrige con el modelo y calcula (necesita internet) |
| `POST /v1/casos` | Los 3 lugares de R3 (mayor mejora, mediana y menor mejora) |

### Dashboard

```bash
cd dashboard
make setup    # entorno del programa que genera los datos
make data     # genera dashboard/dashboard/data/colombia.json desde data/gold/dataset_modelo.parquet
make check    # verifica el motor de cálculo del navegador con los datos reales
make serve    # sirve el dashboard
```

Sin `make` (por ejemplo en Windows), los mismos pasos son `python pipeline/build_data.py`, `node tests/check_model.mjs` y `python -m http.server 8080 -d dashboard`.

La puesta en marcha completa, con la API y con datos de demostración, está en [`dashboard/README.md`](dashboard/README.md).

### Pruebas

```bash
python -m pytest -q tests
```

## Documentación

| Archivo | De qué trata |
|---|---|
| [`docs/modelo.md`](docs/modelo.md) | Qué hace el modelo, decisiones, resultados y límites |
| [`docs/particion_y_evaluacion.md`](docs/particion_y_evaluacion.md) | Cómo se divide el dataset y cómo se evalúa |
| [`docs/contrato_datos.md`](docs/contrato_datos.md) | Contrato de datos de gold y decisiones sobre las horas |
| [`docs/mlops.md`](docs/mlops.md) | Versiones del modelo, promoción y rollback |
| [`dashboard/README.md`](dashboard/README.md) | Puesta en marcha y estructura del dashboard |

## Equipo

- R1 · Modelos: Juan Diego
- R2 · Validación y evaluación: Manuela
- R3 · Física y finanzas: Luciana
- R4 · Ingesta y nube: Juan Pablo
- R5 · Limpieza e integración: Felipe M
