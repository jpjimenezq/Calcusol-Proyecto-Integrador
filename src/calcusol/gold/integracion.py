"""
Gold — Integración IDEAM + NASA (PySpark).

Cruza el IDEAM limpio (silver/ideam) con el NASA estandarizado (silver/nasa) por
(CodigoEstacion, Fecha) y construye la columna Sitio (estaciones a < 1 km = un
sitio, para GroupKFold en validación espacial). Mide el % de filas IDEAM que
consiguen dato NASA, para detectar emparejamientos pobres.

Decisiones (docs/contrato_datos.md):
  - Se conservan TODAS las filas IDEAM (left join); NASA se adjunta donde cuadra.
  - Sitio: < 1 km. No fusiona estaciones (auditoría 1.6), solo las agrupa para
    validación.

Salida: data/gold/integrado/  (Parquet; insumo de gold/features.py).

EJECUCIÓN (tras silver/ideam y silver/nasa):
  docker compose run --rm calcusol uv run python -m calcusol.gold.integracion
"""

from __future__ import annotations

import math

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from calcusol.common.spark import get_spark
from calcusol.config import settings

SILVER_IDEAM = settings.silver_path("ideam")
SILVER_NASA = settings.silver_path("nasa")
SALIDA = settings.gold_path("integrado")

VARIABLES_NASA = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]
DISTANCIA_SITIO_KM = 1.0


# ================================================================== Sitio
def _distancia_km(lat1, lon1, lat2, lon2) -> float:
    """Haversine en km."""
    r1, o1, r2, o2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((r2 - r1) / 2) ** 2
         + math.cos(r1) * math.cos(r2) * math.sin((o2 - o1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def asignar_sitios(estaciones: list[tuple[int, float, float]]) -> dict[int, int]:
    """
    Agrupa estaciones a < DISTANCIA_SITIO_KM en el mismo Sitio. Secuencial sobre
    la tabla pequeña de estaciones (132), como anticipa el plan (R5-2).
    `estaciones`: lista de (codigo, lat, lon). Devuelve {codigo -> id_sitio}.
    """
    centros: list[tuple[float, float]] = []
    sitio_de: dict[int, int] = {}
    for codigo, lat, lon in estaciones:
        asignado = None
        for i, (clat, clon) in enumerate(centros):
            if _distancia_km(lat, lon, clat, clon) <= DISTANCIA_SITIO_KM:
                asignado = i
                break
        if asignado is None:
            centros.append((lat, lon))
            asignado = len(centros) - 1
        sitio_de[codigo] = asignado
    return sitio_de


def construir_sitio(spark: SparkSession, ideam: DataFrame) -> DataFrame:
    """Devuelve ideam con una columna Sitio. Calcula sitios en el driver y une."""
    est_rows = (
        ideam.select("CodigoEstacion", "Latitud", "Longitud")
        .filter(F.col("Latitud").isNotNull())
        .dropDuplicates(["CodigoEstacion"])
        .collect()
    )
    estaciones = [(int(r["CodigoEstacion"]), float(r["Latitud"]), float(r["Longitud"]))
                  for r in est_rows]
    sitio_de = asignar_sitios(estaciones)

    # mapa codigo -> sitio como DataFrame pequeño para broadcast join
    mapa_df = spark.createDataFrame(
        [(c, s) for c, s in sitio_de.items()], ["CodigoEstacion", "Sitio"]
    )
    n_sitios = len(set(sitio_de.values()))
    print(f"Estaciones: {len(estaciones)} | Sitios (<{DISTANCIA_SITIO_KM} km): {n_sitios}")
    return ideam.join(F.broadcast(mapa_df), on="CodigoEstacion", how="left")


# ================================================================== Cruce
def integrar(spark: SparkSession) -> DataFrame:
    ideam = spark.read.parquet(SILVER_IDEAM)
    nasa = spark.read.parquet(SILVER_NASA)

    ideam = construir_sitio(spark, ideam)

    # NASA solo aporta sus 4 variables; la clave es (estación, Fecha).
    nasa_sel = nasa.select("CodigoEstacion", "Fecha", *VARIABLES_NASA)
    df = ideam.join(nasa_sel, on=["CodigoEstacion", "Fecha"], how="left")

    total = df.count()
    con_nasa = df.filter(F.col("ALLSKY_SFC_SW_DWN").isNotNull()).count()
    pct = 100 * con_nasa / total if total else 0
    print(f"Filas IDEAM: {total:,} | con dato NASA: {con_nasa:,} ({pct:.1f} %)")

    return df


def main() -> None:
    spark = get_spark("Calcusol-Gold-Integracion")
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = integrar(spark)
        df.write.mode("overwrite").parquet(SALIDA)
        print(f"\nGold integrado escrito en: {SALIDA}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
