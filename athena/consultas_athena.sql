-- =====================================================================
-- Calcusol · R5-4 · Catálogo de Glue y consultas en Athena sobre gold v1
-- Bucket: s3://calcusol-datos-eafit (us-east-1)
-- Ubicación de resultados de Athena: s3://calcusol-datos-eafit/athena/
--
-- Cómo correrlo: en el editor de Athena, ejecutar cada bloque por separado,
-- en orden. Los pasos 1 y 2 se corren una sola vez; las consultas, cuando se
-- quiera.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. Base de datos en el catálogo de Glue
-- ---------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS calcusol;


-- ---------------------------------------------------------------------
-- 2. Tabla gold v1 (17 columnas del contrato, partida por año, D17)
--    La proyección de particiones le indica a Athena que los años están en
--    carpetas Anio=2005 ... Anio=2022, sin tener que cargarlas a mano.
-- ---------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS calcusol.gold_v1 (
  CodigoEstacion     bigint,
  NombreEstacion     string,
  Departamento       string,
  Fecha              timestamp,
  EsPreliminar       boolean,
  ALLSKY_SFC_SW_DWN  float,
  CLRSKY_SFC_SW_DWN  float,
  ALLSKY_KT          float,
  T2M                float,
  Latitud            double,
  Longitud           double,
  Altitud            double,
  Hora               int,
  Mes                int,
  DiaDelAnio         int,
  Sitio              int,
  Valor              float
)
PARTITIONED BY (anio int)
STORED AS PARQUET
LOCATION 's3://calcusol-datos-eafit/gold/dataset/'
TBLPROPERTIES (
  'projection.enabled' = 'true',
  'projection.anio.type' = 'integer',
  'projection.anio.range' = '2005,2022',
  'storage.location.template' = 's3://calcusol-datos-eafit/gold/dataset/Anio=${anio}/'
);


-- ---------------------------------------------------------------------
-- Comprobación: la tabla lee gold v1 completo
-- Esperado: 3,573,402 filas · 123 estaciones · 28 departamentos
-- ---------------------------------------------------------------------
SELECT COUNT(*)                       AS filas,
       COUNT(DISTINCT CodigoEstacion) AS estaciones,
       COUNT(DISTINCT Departamento)   AS departamentos
FROM calcusol.gold_v1;


-- ---------------------------------------------------------------------
-- Consulta 1 · Filas por año                    (guardada: q1_filas_por_anio)
-- Esperado: 18 años (2005-2022); suma 3,573,402
--           2005-2019 = 3,193,754 (entrenamiento) · 2020-2022 = 379,648 (prueba)
-- ---------------------------------------------------------------------
SELECT anio,
       COUNT(*)                       AS filas,
       COUNT(DISTINCT CodigoEstacion) AS estaciones,
       ROUND(100.0 * AVG(CASE WHEN EsPreliminar THEN 1 ELSE 0 END), 1) AS pct_preliminar
FROM calcusol.gold_v1
GROUP BY anio
ORDER BY anio;


-- ---------------------------------------------------------------------
-- Consulta 2 · Estaciones por Sitio             (guardada: q2_estaciones_por_sitio)
-- Esperado: 123 Sitios, cada uno con 1 estación (las copias del mismo Sitio
--           se quitan en integración con las reglas 12 y 12b)
-- ---------------------------------------------------------------------
SELECT Sitio,
       COUNT(DISTINCT CodigoEstacion) AS estaciones,
       ARBITRARY(NombreEstacion)      AS nombre,
       ARBITRARY(Departamento)        AS departamento,
       COUNT(*)                       AS filas,
       MIN(anio)                      AS desde,
       MAX(anio)                      AS hasta
FROM calcusol.gold_v1
GROUP BY Sitio
ORDER BY estaciones DESC, filas DESC;


-- ---------------------------------------------------------------------
-- Consulta 3 · Error de la NASA por departamento (guardada: q3_error_nasa_por_departamento)
--   mae_todo   : error absoluto medio NASA vs IDEAM, Wh/m², todos los años
--   mae_prueba : el mismo error solo en 2020-2022 (vacío si no hay datos)
--   sesgo      : promedio de (NASA - IDEAM); positivo = la NASA sobreestima
-- Esperado: 28 departamentos
-- ---------------------------------------------------------------------
SELECT Departamento,
       COUNT(DISTINCT CodigoEstacion) AS estaciones,
       COUNT(*)                       AS filas,
       ROUND(AVG(ABS(ALLSKY_SFC_SW_DWN - Valor)), 2) AS mae_todo,
       ROUND(AVG(CASE WHEN anio >= 2020 THEN ABS(ALLSKY_SFC_SW_DWN - Valor) END), 2) AS mae_prueba,
       ROUND(AVG(ALLSKY_SFC_SW_DWN - Valor), 2)      AS sesgo
FROM calcusol.gold_v1
GROUP BY Departamento
ORDER BY mae_todo DESC;
