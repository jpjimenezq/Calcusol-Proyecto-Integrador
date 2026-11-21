"""
Gold — Dataset supervisado final (PySpark).

Toma la tabla integrada (gold/integracion) y produce el dataset de 17 columnas
listo para ML (docs/contrato_datos.md, schema.py). Porta las reglas Gold del
código de referencia:

  G1. Solo horas de sol: CLRSKY_SFC_SW_DWN > 0.
  G2. Sin vacíos en las predictoras NASA/lugar ni en el objetivo.
  G3. Sin valores negativos del objetivo (ruido de sensor al amanecer/atardecer).
  +   Variables de tiempo: Hora, Mes, DiaDelAnio.
  +   Embudo de filas y validación del contrato de 17 columnas.

Salida: data/gold/dataset/  (Parquet, particionado por año de Fecha).

EJECUCIÓN (tras gold/integracion):
  docker compose run --rm calcusol uv run python -m calcusol.gold.features
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

ENTRADA = settings.gold_path("integrado")
SALIDA = settings.gold_path("dataset")

X_NASA = schema.X_NASA
X_LUGAR = schema.X_LUGAR


def agregar_tiempo(df: DataFrame) -> DataFrame:
    return (
        df.withColumn("Hora", F.hour("Fecha"))
        .withColumn("Mes", F.month("Fecha"))
        .withColumn("DiaDelAnio", F.dayofyear("Fecha"))
    )


def construir(spark: SparkSession, con_embudo: bool = True) -> DataFrame:
    df = spark.read.parquet(ENTRADA)

    embudo = []

    def paso(nombre: str, d: DataFrame) -> DataFrame:
        if con_embudo:
            n = d.count()
            embudo.append((nombre, n))
            print(f"  embudo · {nombre}: {n:,}")
        return d

    df = paso("0_integrado", df)

    # G1: solo horas de sol (según NASA el sol está sobre el horizonte)
    df = df.filter(F.col("CLRSKY_SFC_SW_DWN") > 0)
    df = paso("1_horas_de_sol", df)

    # G2: sin vacíos en X (NASA + lugar) ni en Y
    requeridas = X_NASA + X_LUGAR + ["Valor"]
    cond = None
    for c in requeridas:
        term = F.col(c).isNotNull()
        cond = term if cond is None else (cond & term)
    df = df.filter(cond)
    df = paso("2_sin_vacios", df)

    # G3: sin negativos en el objetivo
    df = df.filter(F.col("Valor") >= 0)
    df = paso("3_sin_negativos", df)

    df = agregar_tiempo(df)

    # Proyecta exactamente las 17 columnas del contrato
    df = df.select(*schema.COLUMNAS_GOLD)
    schema.validar_gold(df)

    if con_embudo:
        est = df.select("CodigoEstacion").distinct().count()
        sitios = df.select("Sitio").distinct().count()
        print(f"  estaciones: {est} | sitios: {sitios}")

    return df


def main() -> None:
    spark = get_spark("Calcusol-Gold-Features")
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = construir(spark)
        # Partición por año (plan D17) acelera el filtrado train/test. Se llama
        # 'anio' SIN guión bajo: pyarrow ignora las carpetas que empiezan con '_',
        # y así pandas (ML) también lee las particiones. Al leer, 'anio' aparece
        # como columna extra; ml/ y splits/ la usan o la ignoran, y la validación
        # del contrato se hizo antes de particionar.
        df = df.withColumn("anio", F.year("Fecha"))
        (df.write.mode("overwrite")
           .partitionBy("anio")
           .parquet(SALIDA))
        print(f"\nGold dataset escrito en: {SALIDA}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
