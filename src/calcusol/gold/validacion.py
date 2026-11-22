"""
Gold — Validación final (job calcusol-validacion).

Lee gold/dataset y revisa el contrato completo (config/schema.py). Si algo no cumple,
termina con error para que la orquestación (Step Functions) lo note y avise.
Además imprime el resumen de gold v1 y el embudo completo, de bronze a gold.

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.gold.validacion
"""

from __future__ import annotations

import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from calcusol.common import archivos
from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

DATASET = settings.gold_path("dataset")
EMBUDOS = [
    (settings.silver_path("reportes", "embudo_ideam"),
     [("leidas", "Bronze"), ("r1_otra_variable", "R1 otra variable"), ("r3_fecha_ilegible", "R3 fecha ilegible"),
      ("r4_hora_repetida", "R4 hora repetida"), ("r6_mayor_1400", "R6 mayor a 1400")]),
    (settings.gold_path("reportes", "embudo_integracion"),
     [("r12_preliminar_duplicado", "R12 preliminar duplicado"), ("r12b_copia_exacta", "R12b copia exacta")]),
    (settings.gold_path("reportes", "embudo_gold"),
     [("G1_noche", "G1 noche"), ("G2_vacios", "G2 vacíos"), ("G3_negativos", "G3 negativos"),
      ("D1_antes_2005", "D1 antes de 2005"), ("final", "Gold v1")]),
]
ULTIMO_ANIO_ENTRENAMIENTO = 2019


def embudo_completo(spark: SparkSession) -> None:
    print("\n--- Embudo completo, bronze -> gold (D9) ---")
    for ruta, columnas in EMBUDOS:
        if not archivos.existe(ruta):
            print(f"   (no encontré {ruta})")
            continue
        totales = spark.read.option("header", True).option("inferSchema", True).csv(ruta).agg(
            *[F.sum(c).alias(c) for c, _ in columnas]).first()
        for c, titulo in columnas:
            signo = "" if c in ("leidas", "final") else "−"
            print(f"   {titulo:<28}{signo}{int(totales[c] or 0):>12,}")


def main(spark: SparkSession | None = None) -> None:
    propia = spark is None
    spark = get_spark("Calcusol-Gold-Validacion", existing=spark)
    try:
        df = spark.read.parquet(DATASET)
        try:
            df = schema.validar_gold(df, "gold v1")
        except schema.ContratoIncumplido as e:
            print(e)
            sys.exit(1)

        anio = F.year("Fecha")
        r = df.agg(
            F.count(F.lit(1)).alias("filas"),
            F.sum((anio <= ULTIMO_ANIO_ENTRENAMIENTO).cast("int")).alias("entrenamiento"),
            F.sum((anio > ULTIMO_ANIO_ENTRENAMIENTO).cast("int")).alias("prueba"),
            F.avg(F.when(anio > ULTIMO_ANIO_ENTRENAMIENTO,
                         F.abs(F.col("ALLSKY_SFC_SW_DWN") - F.col(schema.OBJETIVO)))).alias("mae"),
            F.countDistinct("CodigoEstacion").alias("estaciones"),
            F.countDistinct("Sitio").alias("sitios"),
            F.countDistinct("Departamento").alias("deptos"),
            F.date_format(F.min("Fecha"), "yyyy-MM-dd HH:mm").alias("ini"),
            F.date_format(F.max("Fecha"), "yyyy-MM-dd HH:mm").alias("fin"),
            F.avg(F.col("EsPreliminar").cast("double")).alias("prelim"),
        ).first()
        print("\n--- Gold v1 ---")
        print(f"Filas: {r['filas']:,} (entrenamiento hasta {ULTIMO_ANIO_ENTRENAMIENTO}: "
              f"{r['entrenamiento']:,} | prueba: {r['prueba']:,})")
        print(f"MAE de la NASA sin corregir en prueba: {r['mae']:.4f} Wh/m²" if r["mae"] is not None
              else "MAE de la NASA sin corregir en prueba: sin filas de prueba")
        print(f"Estaciones: {r['estaciones']} | Sitios: {r['sitios']} | Departamentos: {r['deptos']}")
        print(f"Fechas: {r['ini']} a {r['fin']} | Preliminares: {r['prelim'] * 100:.1f} %")
        embudo_completo(spark)
        print("\nRESULTADO: gold v1 cumple el contrato")
    finally:
        if propia:
            spark.stop()


if __name__ == "__main__":
    main()
