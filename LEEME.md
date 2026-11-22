# Calcusol — limpieza (R5) en la estructura por carpetas

La lógica de limpieza validada (paridad con pandas, reglas 12 y 12b) organizada en la estructura del equipo.
`bronze/`, `ml/`, `gold/splits.py`, `gold/consolidar.py` y `config/settings.py` son los del equipo, sin cambios.

```text
src/calcusol/
├── config/settings.py      rutas por zona (variable CALCUSOL_DATA_ROOT)            [equipo]
├── config/schema.py        contrato de 17 columnas: nombres, tipos, rangos, vacíos, duplicados
├── common/spark.py         sesión de Spark igual en PC, Docker (Spark 4) y Glue (Spark 3.5)
├── common/archivos.py      listar y leer archivos en el PC o en S3
├── common/embudo.py        embudo de filas por regla y departamento (D9)
├── bronze/                 ingesta                                                [R4]
├── silver/ideam.py         reglas 1-7: CSV, RAD SOLAR, fechas, horas repetidas, >1400, catálogo, departamentos
├── silver/nasa.py          reglas 8-10: JSON _UTC, -4 h, -999 a vacío
├── gold/integracion.py     reglas 11-13 y 12b: unir IDEAM+NASA, Sitio, quitar copias, Hora/Mes/Día
├── gold/features.py        G1 noche, G2 vacíos, G3 negativos, D1 desde 2005, contrato -> gold/dataset
├── gold/validacion.py      contrato completo, resumen de gold v1 y embudo bronze -> gold
├── gold/consolidar.py      gold/dataset -> un solo archivo dataset_modelo.parquet  [equipo]
├── gold/splits.py          entrenamiento/prueba y validación                     [R2]
├── ml/                     modelos                                                [R1]
├── pipeline.py             corre todo en orden en el PC
└── auditoria/              paridad, prueba del contrato, estaciones (nota D19), gemelas, comparar gold
glue/                       entradas de los 3 jobs de Glue
aws/                        configuración de los 3 jobs y del rol de Glue
```

## Zonas y salidas

| Etapa | Lee | Escribe |
|---|---|---|
| silver/ideam | bronze/ideam/mediciones, bronze/ideam/catalogo/CNE_IDEAM.csv | silver/ideam/Anio=... |
| silver/nasa | silver/ideam, bronze/nasa/*_UTC.json | silver/nasa/Anio=... |
| gold/integracion | silver/ideam, silver/nasa | gold/integrado/Anio=... |
| gold/features | gold/integrado | **gold/dataset/Anio=... (gold v1)** |
| gold/consolidar | gold/dataset | gold/dataset_modelo.parquet |

Los embudos quedan en `silver/reportes/` y `gold/reportes/`.

## Correr en el PC (PowerShell, desde esta carpeta)

```powershell
$env:PYTHONPATH = "src"
$env:CALCUSOL_DATA_ROOT = "A:\Archivos organizados\Documentos\reps\calcusol\datos"
$py = "..\Proyecto integrador\.venv-spark\Scripts\python.exe"
& $py -m calcusol.pipeline
& $py -m calcusol.gold.consolidar
```

Prueba rápida con dos departamentos: `& $py -m calcusol.pipeline Amazonas Caldas`

## Jobs de Glue

| Job | Corre |
|---|---|
| calcusol-silver | silver/ideam + silver/nasa |
| calcusol-gold | gold/integracion + gold/features |
| calcusol-validacion | gold/validacion (si gold no cumple el contrato, el job falla) |

El paquete se sube comprimido (`calcusol.zip`, con la carpeta `calcusol` en la raíz) a
`s3://calcusol-datos-eafit/scripts/`. Los scripts de entrada (`glue/glue_*.py`) se cargaron con
*Upload script* al crear cada job, así que Glue los guarda en
`s3://aws-glue-assets-607581913067-us-east-1/scripts/<nombre del job>.py`.
Cada job recibe `--extra-py-files s3://calcusol-datos-eafit/scripts/calcusol.zip` y `--data-root s3://calcusol-datos-eafit`.
`calcusol-silver` corre con 5 workers G.1X; `calcusol-gold` y `calcusol-validacion`, con 2.

## Auditoría

```powershell
& $py -m calcusol.auditoria.paridad --referencia "datos\gold_spark"     # nuevo vs. gold v1 anterior
& $py -m calcusol.auditoria.prueba_contrato
& $py -m calcusol.auditoria.estaciones                                  # de 136 a 123 estaciones
& $py -m calcusol.auditoria.gemelas                                     # las 8 copias (regla 12)
& $py -m calcusol.auditoria.gemelas 21205970:21206790                   # la copia de la regla 12b
```
