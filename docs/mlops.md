# MLOps del modelo de radiación (R1): versiones, promoción y rollback

Estado: fases A, B, C y D implementadas. C y D están desplegadas en AWS (tarea Fargate, máquina de estados, regla
mensual deshabilitada y API que lee el manifiesto).

## Principios

- Cada entrenamiento crea una versión nueva (`v2`, `v3`...). Una versión nunca se sobrescribe.
- El modelo en servicio lo dice un manifiesto explícito, no el nombre de la carpeta.
- Solo se promueve un candidato que cumple los criterios; promover cambia únicamente el manifiesto.
- La misma lógica corre en local y en S3 (`fsspec`): solo cambia la raíz del registro.

## Estructura

Raíz del registro (`CALCUSOL_MODELOS_ROOT` o `--salida`): local `models/`, AWS `s3://calcusol-datos-eafit/modelos`.

```
radiacion/v1/modelo_lgbm.txt | metadatos.json | metricas.json | decision.json
radiacion/v2/...
radiacion/active/manifest.json      modelo en servicio
radiacion/active/historial.json     manifiestos anteriores (rollback)
```

`manifest.json`: `model_version, model_key, metadata_key, metrics_key, gold_version, model_sha256, promoted_at, promoted_by, reason`.
Las claves son relativas a la raíz del registro. Sin manifiesto, el activo es `v1` (el modelo embebido en la imagen de la API).

## Comandos (desde la raíz del repositorio)

```powershell
# 1. Entrenar una versión nueva (no toca v1 ni el activo). Gold y salida pueden ser locales o s3://
python -m calcusol.ml.train --gold s3://calcusol-datos-eafit/gold/dataset --salida s3://calcusol-datos-eafit/modelos

# 2. Evaluar contra el activo en las mismas filas; escribe metricas.json y decision.json (código de salida 2 si se rechaza)
python -m calcusol.ml.promocion evaluar --version v2 --gold s3://calcusol-datos-eafit/gold/dataset --registro s3://calcusol-datos-eafit/modelos

# 3. Promover (automática si cumple; manual forzada solo con --forzar y --motivo)
python -m calcusol.ml.promocion promover --version v2 --por <nombre> --registro s3://calcusol-datos-eafit/modelos
python -m calcusol.ml.promocion promover --version v2 --por <nombre> --forzar --motivo "piloto" --registro ...

# 4. Volver al modelo anterior / ver el estado
python -m calcusol.ml.promocion rollback --por <nombre> --motivo "falló el health" --registro ...
python -m calcusol.ml.promocion estado --registro ...
```

Con Docker local, el comando de siempre sigue funcionando: `docker compose run --rm calcusol uv run python -m calcusol.ml.train`
(ahora crea la siguiente versión libre en `models/radiacion/` en vez de sobrescribir `v1`).

## Criterios de promoción (derivados de `validacion/evaluar_modelo.py`)

La métrica de negocio es el MAE mensual. Todos deben cumplirse (umbrales editables con `--umbrales archivo.json`):

| Criterio | Valor por defecto |
|---|---|
| Sin fuga (`verificar_sin_fuga`, sin sitios ni estaciones de prueba en el train) | "OK: sin fuga" |
| Mejora del MAE mensual espacial (estaciones nunca vistas) frente al activo | ≥ 0,5 % |
| Bootstrap pareado (2.000 remuestreos, semilla 42): remuestreos a favor | ≥ 95 % |
| Sesgo mensual espacial no empeora | ≤ 1 punto porcentual |
| Examen temporal y espacio-temporal no empeoran | ≤ 1 % |
| Supera a NASA sin corregir (MAE mensual) en los tres exámenes | sí |
| El artefacto carga con LightGBM, coincide con su sha256 y predice valores finitos | sí |

La regla D0 de R2 (conservar los datos preliminares solo si no empeoran el error más de 2 %) no se reevalúa
en cada reentrenamiento: el entrenamiento sigue usando solo datos definitivos.

## Pruebas

```powershell
python -m pytest tests -q
```

## Fase C: reentrenamiento en AWS

Flujo (`src/calcusol/stepfunctions/pipeline.json`): preparar estaciones → ingesta NASA → Silver → Gold →
validación → **ReentrenarModelo** (ECS Fargate) → aviso de éxito. Cualquier fallo envía la alerta a SNS y
termina en estado `Fail` (antes terminaba como éxito).

El contenedor ejecuta `python -m calcusol.ml.reentrenar --auto-promover`: lee Gold de S3, entrena la versión
siguiente, la evalúa contra el activo y la promueve solo si cumple los criterios. Un candidato rechazado
**no** es un error: queda `radiacion/runs/<version>.json` y el activo sigue igual.

| Archivo | Para qué |
|---|---|
| `infra/mlops/Dockerfile.entrenamiento` y `requirements-entrenamiento.txt` | Imagen liviana (sin Java ni Spark) con el mismo código de `src/calcusol` |
| `infra/mlops/tarea_entrenamiento.json` | Definición de tarea Fargate: 4 vCPU, 16 GB, 30 GB de disco, logs en `/calcusol/entrenamiento` |
| `infra/mlops/desplegar_mlops.ps1` | Crea logs, ECR, imagen, tarea, siembra `v1` en S3, actualiza la máquina de estados y (opcional) la regla mensual. Sin `-Aplicar` solo muestra los comandos |

### Variables de entorno

| Variable | Valor en AWS | Uso |
|---|---|---|
| `CALCUSOL_GOLD_RUTA` | `s3://calcusol-datos-eafit/gold/dataset` | Gold de entrada |
| `CALCUSOL_MODELOS_ROOT` | `s3://calcusol-datos-eafit/modelos` | Registro de versiones y manifiesto |
| `GIT_COMMIT` | argumento de construcción de la imagen | Queda en `metadatos.json` |

### Permisos (mínimos)

- Tarea de entrenamiento (`calcusol-entrenamiento-tarea`): lee `gold/*`, lee y escribe `modelos/*`. Nada más.
- Máquina de estados (`calcusol-stepfunctions-pipeline`): lanzar y esperar la tarea, `PassRole` del rol de la tarea, SNS.
- Quien despliega (`calcusol-despliegue` + `calcusol-mlops`): lo del apartado de IAM; no puede borrar objetos de S3.
- Para la regla mensual falta el rol `calcusol-eventbridge-pipeline` (confía en `events.amazonaws.com`, permite
  `states:StartExecution` sobre la máquina) y que `calcusol-mlops` permita `iam:PassRole` sobre él con
  `iam:PassedToService = events.amazonaws.com`.

### Costos estimados (us-east-1, a precios de lista; confirmar en la consola de facturación)

- Fargate 4 vCPU + 16 GB: unos 0,23 USD por hora; una corrida de 10 a 20 minutos cuesta del orden de 0,05 USD.
- S3: cada versión pesa unos 4 MB; despreciable. CloudWatch Logs con retención de 30 días: despreciable.
- El costo del pipeline mensual lo domina lo que ya existía (Lambda de NASA, Glue). El reentrenamiento suma centavos.
- La regla mensual se crea **deshabilitada**; mientras tanto no corre nada.

### Responsable y operación

- Dueño: el equipo de datos de Calcusol; quien activa la regla o promueve a mano queda en `promoted_by`.
- Ver el estado: `python -m calcusol.ml.promocion estado --registro s3://calcusol-datos-eafit/modelos`.
- Volver atrás: `... rollback --por <nombre> --motivo "..."`. No borra versiones.

## Fase D: la API sirve el modelo activo

Al arrancar, la API (`api/main.py` → `calcusol.ml.activo`) lee `radiacion/active/manifest.json` del registro
(`CALCUSOL_MODELOS_ROOT=s3://calcusol-datos-eafit/modelos`), descarga el modelo, verifica su sha256 y lo carga.
**Respaldo:** si no hay registro, no hay manifiesto o cualquier cosa falla (S3, archivo dañado, sha256), usa el modelo
embebido en la imagen (`v1`). La API nunca se queda sin modelo. `GET /health` informa cuál sirve:
`modelo_version`, `modelo_origen` (`registro` o `embebido`), `modelo_sha256`, `gold_version`, `promovido`.

La API lee el manifiesto **solo al arrancar**: tras promover hay que redesplegarla. Eso hace `publicar`:

```powershell
# Promueve v3, redespliega la API en ECS, espera, corre las pruebas de humo y, si fallan, hace rollback y redespliega
python -m calcusol.ml.publicar publicar --version v3 --registro s3://calcusol-datos-eafit/modelos --api-url https://ca-660aa7a821e841e6944d63c7643ff905.ecs.us-east-1.on.aws
# Solo redesplegar y verificar el activo actual (p. ej. tras la promoción automática del pipeline)
python -m calcusol.ml.publicar publicar --ya-promovido --registro s3://calcusol-datos-eafit/modelos --api-url ...
# Solo las pruebas de humo
python -m calcusol.ml.publicar humo --api-url ... --version v2
```

Pruebas de humo: `/health` (y que sirva la versión esperada), `/predict` (valor finito, entre 0 y 1500),
`/v1/parametros` y `/v1/simulacion/climatologia`. Si fallan tras una promoción hecha por `publicar`, vuelve al
manifiesto anterior, redespliega y vuelve a probar. Con `--ya-promovido` no toca el manifiesto: solo avisa.

**Permisos de la API:** rol de tarea `calcusol-api-tarea` (lee solo `modelos/*`). El servicio de ECS Express lo usa
desde el despliegue `2026-11-23-d`.

**El pipeline mensual promueve en S3 pero no redespliega la API.** Hasta que se agregue ese paso a la máquina de
estados, hay que ejecutar `publicar --ya-promovido` después de cada promoción automática.

**Dashboard:** `python dashboard/pipeline/build_data_aws.py --registro s3://calcusol-datos-eafit/modelos` calcula los datos
con el modelo activo (el mismo de la API) y luego se publica con `desplegar_aws.ps1`.

**Modelo explícito:** `python -m calcusol.ml.promocion fijar --version v1 ...` escribe el manifiesto del modelo que ya
está en servicio cuando aún no hay uno (no cambia qué modelo se sirve).
