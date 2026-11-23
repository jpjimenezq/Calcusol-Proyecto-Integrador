# Calcusol · Dashboard de paneles solares en Antioquia

Dashboard interactivo que responde, para un hogar de Antioquia, si instalar un sistema solar fotovoltaico es viable y en cuántos años se recupera la inversión. Usa la radiación satelital de NASA POWER corregida con el modelo de Calcusol y la contrasta con las mediciones del IDEAM.

## Puesta en marcha

Requisitos: Python 3.11 o superior, Node 18 o superior (solo para las verificaciones) y `make`.

```bash
make setup    # crea .venv e instala las dependencias del pipeline
make data     # filtra Antioquia, aplica el modelo y genera dashboard/data/antioquia.json
make check    # verifica el modelo energético y financiero con los datos reales
make serve    # abre el dashboard en http://localhost:8000
```

El dashboard carga los datos con `fetch`, así que hay que abrirlo con `make serve` (o cualquier servidor estático), no con doble clic sobre el archivo.

## Estructura

| Ruta | Contenido |
|---|---|
| `dataset_modelo.parquet` | Datos horarios IDEAM + NASA POWER de 29 departamentos (fuente) |
| `modelo_radiacion_calcusol.joblib` | Modelo XGBoost que corrige la radiación satelital |
| `pipeline/build_data.py` | Filtra Antioquia, aplica el modelo y exporta el JSON del dashboard |
| `dashboard/index.html` | Página del dashboard |
| `dashboard/css/styles.css` | Línea gráfica de la presentación (colores, tipografía, tarjetas) |
| `dashboard/js/model.js` | Modelo energético y financiero (funciones puras) |
| `dashboard/js/charts.js` | Gráficos SVG sin dependencias |
| `dashboard/js/app.js` | Filtros, estado y composición de las secciones |
| `dashboard/data/antioquia.json` | Climatología mes × hora por estación y contorno del departamento y límites municipales (142 kB, generado) |
| `pipeline/geo/antioquia.geojson` | Contorno simplificado de Antioquia (geoBoundaries / OpenStreetMap, licencia ODbL) |
| `pipeline/geo/antioquia_municipios.geojson` | Límites simplificados de los 125 municipios (geoBoundaries / DANE, licencia CC BY 4.0) |
| `tests/check_model.mjs` | Comprobaciones del modelo |
| `docs/PLAN.md` | Plan: preguntas de negocio, gráficos, supuestos y pendientes |
| `.github/workflows/deploy.yml` | Despliegue automático en GitHub Pages |

## Cómo fluyen los datos

1. `build_data.py` lee el parquet y se queda con `Departamento = 'Antioquia'`: 11 estaciones y unos 327.000 registros horarios entre 2005 y 2022 (horas 7 a 18).
2. Aplica el modelo a cada registro para obtener la radiación corregida.
3. Promedia por estación, mes y hora la radiación medida (IDEAM), la satelital (NASA), la corregida (ML) y la temperatura.
4. El navegador simula con esa climatología la generación, el autoconsumo, la factura, el Payback y el VPN cada vez que cambia un filtro. No hay servidor.

## Despliegue

El dashboard es un sitio estático: basta publicar la carpeta `dashboard/`.

**GitHub Pages (incluido).**

```bash
git init -b main
git add .
git commit -m "Dashboard Calcusol Antioquia"
git remote add origin <URL del repositorio>
git push -u origin main
```

Después, en el repositorio: *Settings → Pages → Source: GitHub Actions*. Cada push a `main` ejecuta las verificaciones y publica el sitio.

**Otras opciones.** Netlify, Vercel o Cloudflare Pages: directorio de publicación `dashboard`, sin comando de build.

Si cambian el parquet o el modelo, hay que ejecutar `make data` y subir el nuevo `antioquia.json`; el despliegue no lo regenera.

## Compartir un caso

El estado inicial se puede fijar en la URL:

```
?lat=6.15&lon=-75.37&consumo=250&estrato=3&tarifa=950&perfil=diurno&mes=7&escenario=2
```

`lat` y `lon` marcan la ubicación del hogar y seleccionan la estación más cercana; también se puede fijar la estación con `estacion=<código>`.

`perfil`: `tipico`, `diurno` o `nocturno`. `mes`: 1 a 12. `escenario`: 1 (pequeño), 2 (mediano) o 3 (máximo). Los códigos de estación están en `antioquia.json`.

## Qué es dato y qué es supuesto

| Elemento | Estado |
|---|---|
| Radiación IDEAM, NASA POWER y temperatura por estación | Dato del parquet |
| Radiación corregida | Predicción del modelo; su error se midió sobre los mismos registros históricos, no es una validación independiente |
| Tarifa, costos de instalación, comercialización, precio de bolsa, tasas | Valores de referencia editables en el dashboard; hay que validarlos antes de presentar cifras como definitivas |
| Perfiles horarios de consumo | Formas supuestas, no mediciones |

El detalle de fórmulas y limitaciones está en [docs/PLAN.md](docs/PLAN.md).
