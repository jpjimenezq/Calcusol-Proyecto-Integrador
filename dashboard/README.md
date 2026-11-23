# Calcusol · Dashboard de paneles solares en Colombia

Dashboard interactivo que responde, para un hogar de Colombia (todos los departamentos con estaciones, con filtro por departamento), si instalar un sistema solar fotovoltaico es viable y en cuántos años se recupera la inversión. Usa la radiación satelital de NASA POWER corregida con el modelo de Calcusol y la contrasta con las mediciones del IDEAM.

## Puesta en marcha

Requisitos: Python 3.11 o superior, Node 18 o superior (solo para las verificaciones) y `make`.

```bash
make setup    # crea .venv e instala las dependencias del pipeline
make data     # aplica el modelo a todo el país y genera dashboard/data/colombia.json
make check    # verifica el modelo energético y financiero con los datos reales
make serve    # abre el dashboard en http://localhost:8000
```

El dashboard carga los datos con `fetch`, así que hay que abrirlo con `make serve` (o cualquier servidor estático), no con doble clic sobre el archivo.

## Con la API de cálculo

El dashboard puede delegar la física y las finanzas en la API de Calcusol (`../api`, los cálculos de R3). Si la API responde, calcula ella; si no, el dashboard usa `model.js` en el navegador como respaldo. La cabecera indica cuál se está usando.

```bash
# 1. API (desde la raíz del repositorio)
docker build -f api/Dockerfile -t calcusol-api .
docker run --rm -p 8000:8000 calcusol-api

# 2. Dashboard
python -m http.server 8080 -d dashboard/dashboard      # y abrir http://localhost:8080
```

- La API se busca en `http://127.0.0.1:8000`. Para otra dirección: `http://localhost:8080/?api=http://127.0.0.1:8001`. Con `?api=off` se fuerza el cálculo local.
- El dashboard usa `POST /v1/simulacion/climatologia`: recibe la climatología mes × hora de la estación, arma un año de 8.760 horas y devuelve totales, balance mensual, flujo de caja y el día promedio de cada mes. Son 6 llamadas por pantalla (3 tamaños × radiación corregida y NASA); la API acepta cualquier puerto de `localhost` (CORS) y, para otros orígenes, la variable `CORS_ORIGENES`.
- Los valores por defecto (hogar, tarifas por estrato, costos, tasas, inversor) salen de la ficha de supuestos de R3 (`src/calcusol/fisica_finanzas/config/config_r3.yaml`) mediante `GET /v1/parametros`; sin API se usan los de `model.js`, que repiten la ficha. Al cambiar el estrato se cargan sus tarifas de EPM (subsistencia y plena).
- Con la API las cifras difieren algo de `model.js`: usa las tarifas por estrato de la ficha de supuestos de R3 (reescaladas a la «Tarifa del operador»), el panel inclinado y el reemplazo del inversor en el año 15.

**Sin el parquet.** `make demo-data` (necesita internet) genera `dashboard/data/colombia.json` con radiación de NASA POWER en las 32 capitales de departamento, corregida con el modelo de la API. No son estaciones del IDEAM; el archivo lleva `meta.demo` y el dashboard lo avisa. Para datos reales, `make data` con `dataset_modelo.parquet`, o `build_data_aws.py` con la tabla gold de Athena.

## Estructura

| Ruta | Contenido |
|---|---|
| `dataset_modelo.parquet` | Datos horarios IDEAM + NASA POWER de 29 departamentos (fuente) |
| `modelo_radiacion_calcusol.joblib` | Modelo XGBoost que corrige la radiación satelital |
| `pipeline/build_data.py` | Aplica el modelo al parquet y exporta el JSON del dashboard (`--departamento` para uno solo) |
| `pipeline/build_demo_data.py` | Datos de demostración: NASA POWER en las capitales, corregida con el modelo de la API |
| `pipeline/build_geo.py` | Genera los contornos de Colombia (país, departamentos y municipios) desde geoBoundaries |
| `pipeline/formato.py` | Piezas comunes de los scripts de datos (geografía, matrices mes × hora, formato del JSON) |
| `dashboard/index.html` | Página del dashboard |
| `dashboard/css/styles.css` | Línea gráfica de la presentación (colores, tipografía, tarjetas) |
| `dashboard/js/model.js` | Modelo energético y financiero (funciones puras) |
| `dashboard/js/charts.js` | Gráficos SVG sin dependencias |
| `dashboard/js/app.js` | Filtros, estado y composición de las secciones |
| `dashboard/data/colombia.json` | Climatología mes × hora por estación y contornos del país, los departamentos y los municipios (generado; no va en git) |
| `pipeline/geo/colombia.geojson` | Contorno continental de Colombia (geoBoundaries / Wikimedia, CC0) |
| `pipeline/geo/colombia_departamentos.geojson` | 33 departamentos (geoBoundaries / OpenStreetMap, licencia ODbL) |
| `pipeline/geo/colombia_municipios.geojson` | 1.122 municipios con su departamento (geoBoundaries / DANE, licencia CC BY 4.0) |
| `pipeline/geo/antioquia*.geojson` | Contornos de Antioquia de la versión anterior (ya no se usan) |
| `tests/check_model.mjs` | Comprobaciones del modelo |
| `docs/PLAN.md` | Plan: preguntas de negocio, gráficos, supuestos y pendientes |
| `.github/workflows/deploy.yml` | Despliegue automático en GitHub Pages |

## Cómo fluyen los datos

1. `build_data.py` lee el parquet (todos los departamentos, o uno con `--departamento`) y se queda con las horas 7 a 18.
2. Aplica el modelo a cada registro para obtener la radiación corregida.
3. Promedia por estación, mes y hora la radiación medida (IDEAM), la satelital (NASA), la corregida (ML) y la temperatura.
4. El navegador simula con esa climatología la generación, el autoconsumo, la factura, el Payback y el VPN cada vez que cambia un filtro. No hay servidor.

## Despliegue en AWS (producción)

| Pieza | Servicio | Nombre |
|---|---|---|
| Dashboard | S3 (privado) + CloudFront | bucket `calcusol-dashboard-607581913067`, distribución `E2ZC5DM55MEH63` → https://d2zdnkizc9b8p1.cloudfront.net |
| API | ECS Express Mode (Fargate, 0,5 vCPU, 1 GB, 1 a 2 tareas) | servicio `calcusol-api` → https://ca-660aa7a821e841e6944d63c7643ff905.ecs.us-east-1.on.aws |
| Imagen de la API | ECR | `calcusol-api` |
| Datos | Athena `calcusol.gold_v1` | bucket `calcusol-datos-eafit` |

Todo se maneja con el perfil `calcusol-despliegue` del AWS CLI (rol con permisos solo sobre recursos `calcusol-*`). La API no tiene autenticación: está abierta a internet y solo acepta llamadas desde el navegador del dashboard (CORS, variable `CORS_ORIGENES`).

**Actualizar los datos o el tablero.** Regenera los datos desde Athena y publícalos:

```powershell
python dashboard/pipeline/build_data_aws.py
.\dashboard\desplegar_aws.ps1 -ApiUrl https://ca-660aa7a821e841e6944d63c7643ff905.ecs.us-east-1.on.aws
```

**Actualizar la API** (cambió algo en `api/` o `src/`). Con una etiqueta nueva, porque con `latest` ECS no sabe que hay una imagen distinta:

```powershell
docker build -f api/Dockerfile -t calcusol-api .
docker tag calcusol-api 607581913067.dkr.ecr.us-east-1.amazonaws.com/calcusol-api:AAAA-MM-DD
aws ecr get-login-password --profile calcusol-despliegue --region us-east-1 | docker login --username AWS --password-stdin 607581913067.dkr.ecr.us-east-1.amazonaws.com
docker push 607581913067.dkr.ecr.us-east-1.amazonaws.com/calcusol-api:AAAA-MM-DD
aws ecs update-express-gateway-service --profile calcusol-despliegue --region us-east-1 --service-arn arn:aws:ecs:us-east-1:607581913067:service/default/calcusol-api --primary-container '{"image":"607581913067.dkr.ecr.us-east-1.amazonaws.com/calcusol-api:AAAA-MM-DD","containerPort":8000}'
```

En Git Bash, anteponga `MSYS_NO_PATHCONV=1` a los comandos de `aws` con rutas como `/health`: Git Bash las convierte en rutas de Windows (así quedó mal la comprobación de salud la primera vez).

**Apagarlo todo** (deja de cobrar): `aws ecs delete-express-gateway-service`, vaciar y borrar el bucket, deshabilitar y borrar la distribución de CloudFront, y borrar el repositorio `calcusol-api` de ECR y los roles `calcusol-ecs-*`.

## Despliegue

El dashboard es un sitio estático: basta publicar la carpeta `dashboard/`.

**GitHub Pages (incluido).**

```bash
git init -b main
git add .
git commit -m "Dashboard Calcusol Colombia"
git remote add origin <URL del repositorio>
git push -u origin main
```

Después, en el repositorio: *Settings → Pages → Source: GitHub Actions*. Cada push a `main` ejecuta las verificaciones y publica el sitio.

**Otras opciones.** Netlify, Vercel o Cloudflare Pages: directorio de publicación `dashboard`, sin comando de build.

Si cambian el parquet o el modelo, hay que ejecutar `make data` y subir el nuevo `colombia.json`; el despliegue no lo regenera.

## Compartir un caso

El estado inicial se puede fijar en la URL:

```
?lat=6.15&lon=-75.37&consumo=250&estrato=3&tarifa=950&perfil=diurno&mes=7&escenario=2
```

`lat` y `lon` marcan la ubicación del hogar y seleccionan la estación más cercana; también se puede fijar la estación con `estacion=<código>` y el departamento con `departamento=Antioquia` (el mapa se acerca a él).

`perfil`: `tipico`, `diurno` o `nocturno`. `mes`: 1 a 12. `escenario`: 1 (pequeño), 2 (mediano) o 3 (máximo). Los códigos de estación están en `colombia.json`.

## Qué es dato y qué es supuesto

| Elemento | Estado |
|---|---|
| Radiación IDEAM, NASA POWER y temperatura por estación | Dato del parquet |
| Radiación corregida | Predicción del modelo; su error se midió sobre los mismos registros históricos, no es una validación independiente |
| Tarifa, costos de instalación, comercialización, precio de bolsa, tasas | Valores de referencia editables en el dashboard; hay que validarlos antes de presentar cifras como definitivas |
| Perfiles horarios de consumo | Formas supuestas, no mediciones |

El detalle de fórmulas y limitaciones está en [docs/PLAN.md](docs/PLAN.md).
