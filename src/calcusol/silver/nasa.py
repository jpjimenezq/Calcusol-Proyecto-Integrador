"""
Silver — NASA POWER estandarizado (PySpark).

Lee los JSON crudos de bronze/nasa/, los convierte a una tabla horaria estándar
y aplica las decisiones cerradas (docs/contrato_datos.md):

  - Regla temporal D-TEMPORAL: el timestamp UTC de NASA se corrige a hora local
    del IDEAM restando 4 h (UTC-5 por zona horaria, +1 h por la convención
    inicio-de-intervalo de NASA frente a fin-de-intervalo del IDEAM). Validado:
    el pico de radiación queda centrado en la hora 12 local.
  - Faltantes: -999 -> NULL.
  - Cada JSON se nombra por coordenadas; se mapea coordenada -> CodigoEstacion
    reusando el catálogo cruzado con las estaciones de radiación (bronze.nasa).

Salida: data/silver/nasa/  (Parquet, columnas: CodigoEstacion, Fecha + 4 vars).

Diseño distribuido: la lista de archivos se paraleliza y cada ejecutor parsea
su JSON a filas; no se traen todos los datos al driver.

EJECUCIÓN (tras terminar la descarga Bronze NASA):
  docker compose run --rm calcusol uv run python -m calcusol.silver.nasa
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from calcusol.bronze import nasa as bronze_nasa
from calcusol.common.spark import get_spark
from calcusol.config import settings

BRONZE_NASA = settings.bronze_path("nasa")
SALIDA = settings.silver_path("nasa")

VARIABLES = bronze_nasa.VARIABLES          # 4 variables NASA
AJUSTE_HORAS = -4                          # UTC-5 + 1 (D-TEMPORAL)
FALTANTE = -999.0

# Esquema de salida del parseo de cada JSON (filas largas por hora).
ESQUEMA = T.StructType([
    T.StructField("CodigoEstacion", T.LongType()),
    T.StructField("Fecha", T.TimestampType()),
    *[T.StructField(v, T.FloatType()) for v in VARIABLES],
])


# ================================================================== Lógica pura
def mapa_archivo_a_codigos() -> dict[str, list[int]]:
    """
    Construye {nombre_archivo_json -> [CodigoEstacion, ...]} a partir del cruce
    coordenada<->estación de la ingesta Bronze.

    OJO: varias estaciones casi colocalizadas (< ~11 m) comparten el mismo
    nombre de archivo al redondear a 4 decimales, y por tanto el MISMO JSON de
    NASA (coherente: NASA tiene resolución ~50 km, ver plan D10 y auditoría 1.6).
    Por eso el valor es una LISTA de códigos: el JSON se asigna a todas las
    estaciones de esa coordenada, no a una sola.
    """
    coords = bronze_nasa.leer_coordenadas()   # codigo(str) -> (lat, lon)
    mapa: dict[str, list[int]] = {}
    carpeta = Path(BRONZE_NASA)
    nombres = {p.name for p in carpeta.glob("*_UTC.json")}
    for codigo, (lat, lon) in coords.items():
        for anio in range(bronze_nasa.PISO_NASA, bronze_nasa.ANIO_FIN_DEFECTO + 1):
            nombre = bronze_nasa.nombre_archivo(lat, lon, anio)
            if nombre in nombres:
                mapa.setdefault(nombre, [])
                if int(codigo) not in mapa[nombre]:
                    mapa[nombre].append(int(codigo))
    return mapa


def parsear_json(texto: str, codigos: list[int]) -> list[tuple]:
    """
    Convierte un JSON crudo de NASA en filas (codigo, fecha_local, *variables),
    emitiendo las filas para CADA estación que comparte esa coordenada.
    Aplica el ajuste temporal (-4 h) y -999 -> None. Lógica pura y testeable.
    """
    datos = json.loads(texto)
    parametros = datos["properties"]["parameter"]
    claves = parametros[VARIABLES[0]].keys()
    filas = []
    for clave in claves:
        utc = datetime.strptime(clave, "%Y%m%d%H")
        local = utc + timedelta(hours=AJUSTE_HORAS)
        valores = []
        for v in VARIABLES:
            x = parametros[v].get(clave)
            valores.append(None if x is None or x == FALTANTE else float(x))
        for codigo in codigos:
            filas.append((codigo, local, *valores))
    return filas


# ================================================================== Pipeline
def transformar_nasa(spark: SparkSession) -> DataFrame:
    mapa = mapa_archivo_a_codigos()
    if not mapa:
        raise SystemExit(f"No hay JSON de NASA en {BRONZE_NASA} para las estaciones.")
    n_est = len({c for cods in mapa.values() for c in cods})
    print(f"Archivos NASA: {len(mapa)} | estaciones mapeadas: {n_est}")

    carpeta = Path(BRONZE_NASA)
    items = [(str(carpeta / nombre), codigos) for nombre, codigos in mapa.items()]

    # Distribuido: cada ejecutor lee y parsea su propio archivo.
    def _parsear_item(item):
        ruta, codigos = item
        with open(ruta, encoding="utf-8") as f:
            return parsear_json(f.read(), codigos)

    rdd = spark.sparkContext.parallelize(items, numSlices=min(len(items), 64))
    filas_rdd = rdd.flatMap(_parsear_item)
    df = spark.createDataFrame(filas_rdd, schema=ESQUEMA)

    # Una estación-hora única por si hubiera años solapados.
    df = df.dropDuplicates(["CodigoEstacion", "Fecha"])
    return df


def main() -> None:
    spark = get_spark("Calcusol-Silver-NASA")
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = transformar_nasa(spark)
        total = df.count()
        estaciones = df.select("CodigoEstacion").distinct().count()
        print(f"Filas NASA: {total:,} | estaciones: {estaciones}")
        print("Rango de fechas (local):")
        df.select(F.min("Fecha"), F.max("Fecha")).show(truncate=False)

        (df.write.mode("overwrite").parquet(SALIDA))
        print(f"\nSilver NASA escrito en: {SALIDA}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
