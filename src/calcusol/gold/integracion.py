"""
Gold — Integración IDEAM + NASA, Sitio y copias (reglas 11 a 13, 12b).

Entrada:
    silver/ideam/        (silver/ideam.py)
    silver/nasa/         (silver/nasa.py)
Salida:
    gold/integrado/Anio=<año>/*.parquet     las 17 columnas del contrato, todavía con noches,
                                            vacíos y años antes de 2005 (eso lo filtra features.py)
    gold/reportes/embudo_integracion/       filas que quitan las reglas 12 y 12b, por departamento

REGLAS
  11. Se conservan todas las filas del IDEAM; la NASA se pega por estación y hora.
  12. Sitio = estaciones a menos de 1 km (se calcula en orden de código, siempre igual).
      Un dato "Preliminar" se quita solo si en el mismo Sitio y a la misma hora hay un dato
      con mayor aprobación. Sitio se CONSERVA como columna.
  12b. Copias exactas: si en el mismo Sitio y a la misma hora dos estaciones tienen la misma
      aprobación y el mismo Valor, se queda solo la de código mayor (la más nueva).
      Caso conocido: Hacienda Santa Ana 21205970 (copia de 21206790, ambas preliminares).
  13. Aviso si una estación aparece en dos departamentos.
  Al final se agregan Hora, Mes y DiaDelAnio, y se revisan columnas y tipos (contrato C1-C2).

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.gold.integracion
"""

from __future__ import annotations

import math
from collections import defaultdict

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from calcusol.common import archivos, embudo
from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

SILVER_IDEAM = settings.silver_path("ideam")
SILVER_NASA = settings.silver_path("nasa")
SALIDA = settings.gold_path("integrado")
REPORTES = settings.gold_path("reportes")

VARIABLES_NASA = schema.X_NASA
DISTANCIA_SITIO_KM = 1.0


# ------------------------------------------------------------------ Sitio
def _distancia_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def asignar_sitios(estaciones: list[tuple[int, float, float]]) -> dict[int, int]:
    """(código, lat, lon) -> {código: Sitio}. Mismo algoritmo y mismo orden (por código) que silver.py."""
    sitios, sitio_de = [], {}
    for codigo, lat, lon in sorted(estaciones):
        for i, (lat_s, lon_s) in enumerate(sitios):
            if _distancia_km(lat, lon, lat_s, lon_s) <= DISTANCIA_SITIO_KM:
                sitio_de[codigo] = i
                break
        else:
            sitios.append((lat, lon))
            sitio_de[codigo] = len(sitios) - 1
    return sitio_de


def construir_sitio(spark: SparkSession, ideam: DataFrame) -> tuple[DataFrame, dict[int, int]]:
    filas = (ideam.groupBy("CodigoEstacion")
             .agg(F.first("Latitud").alias("lat"), F.first("Longitud").alias("lon")).collect())
    sitio_de = asignar_sitios([(r["CodigoEstacion"], r["lat"], r["lon"]) for r in filas
                               if r["lat"] is not None and r["lon"] is not None])
    tabla = spark.createDataFrame(list(sitio_de.items()), "CodigoEstacion long, Sitio int")
    return tabla, sitio_de


# ------------------------------------------------------------------ Integración
def integrar(spark: SparkSession) -> tuple[DataFrame, list[dict]]:
    """Reglas 11, 12, 12b y 13. Devuelve (tabla integrada, conteos por departamento)."""
    ideam = spark.read.parquet(SILVER_IDEAM).drop(schema.COLUMNA_PARTICION)
    nasa = spark.read.parquet(SILVER_NASA).drop(schema.COLUMNA_PARTICION)
    df = ideam.join(nasa, ["CodigoEstacion", "Fecha"], "left")                  # regla 11

    tabla_sitios, sitio_de = construir_sitio(spark, ideam)
    df = df.join(F.broadcast(tabla_sitios), "CodigoEstacion", "left")

    # Regla 12: preliminares con dato mejor en el mismo Sitio y hora
    mejor = (df.filter(~F.col("EsPreliminar") & F.col("Sitio").isNotNull())
             .select("Sitio", "Fecha").distinct().withColumn("HayMejor", F.lit(True)))
    df = df.join(mejor, ["Sitio", "Fecha"], "left")
    df = df.withColumn("_quitar12", F.col("EsPreliminar") & F.coalesce(F.col("HayMejor"), F.lit(False))).cache()
    por_depto = (df.groupBy("Departamento")
                 .agg(F.count(F.lit(1)).alias("entrada"),
                      F.sum(F.col("EsPreliminar").cast("int")).alias("_prelim"),
                      F.sum(F.col("_quitar12").cast("int")).alias("r12_preliminar_duplicado")).collect())
    total_prelim = sum(r["_prelim"] for r in por_depto)
    total_q12 = sum(r["r12_preliminar_duplicado"] for r in por_depto)
    df = df.filter(~F.col("_quitar12")).drop("HayMejor", "_quitar12")
    print(f"\nPreliminares: {total_prelim:,} | quitadas (ya había dato mejor): {total_q12:,} | "
          f"conservadas: {total_prelim - total_q12:,}")
    grupos = defaultdict(list)
    for codigo, sitio in sitio_de.items():
        grupos[sitio].append(codigo)
    parejas = sorted(sorted(g) for g in grupos.values() if len(g) > 1)
    if parejas:
        print(f"Estaciones en el mismo sitio: {parejas}")

    # Regla 12b: copias exactas dentro del mismo Sitio -> se queda la de código mayor
    copia = Window.partitionBy("Sitio", "Fecha", "EsPreliminar", "Valor").orderBy(F.desc("CodigoEstacion"))
    df = df.withColumn("_c", F.when(F.col("Sitio").isNull(), 1).otherwise(F.row_number().over(copia)))
    copias = (df.filter(F.col("_c") > 1).groupBy("CodigoEstacion", "Departamento").count()
              .orderBy("CodigoEstacion").collect())
    if copias:
        print("Regla 12b — horas quitadas por ser copia exacta de otra estación del mismo Sitio:")
        for r in copias:
            print(f"   {r['CodigoEstacion']} ({r['Departamento']}): {r['count']:,} horas")
    else:
        print("Regla 12b — no hay copias exactas dentro de un mismo Sitio")
    df = df.filter(F.col("_c") == 1).drop("_c")

    # Regla 13
    dos = (df.groupBy("CodigoEstacion").agg(F.countDistinct("Departamento").alias("n"))
           .filter("n > 1").collect())
    if dos:
        print(f"! Estaciones en más de un departamento: {sorted(r['CodigoEstacion'] for r in dos)}")

    df = (df.withColumn("Hora", F.hour("Fecha"))
            .withColumn("Mes", F.month("Fecha"))
            .withColumn("DiaDelAnio", F.dayofyear("Fecha"))
            .withColumn(schema.COLUMNA_PARTICION, F.year("Fecha")))
    df = schema.validar_columnas(df.select(*schema.COLUMNAS_GOLD, schema.COLUMNA_PARTICION), "integrado")
    return df, [r.asDict() for r in por_depto]


def main(spark: SparkSession | None = None) -> None:
    propia = spark is None
    spark = get_spark("Calcusol-Gold-Integracion", existing=spark)
    try:
        df, por_depto = integrar(spark)
        (df.repartition(schema.COLUMNA_PARTICION)
           .sortWithinPartitions("Departamento", "CodigoEstacion", "Fecha")
           .write.mode("overwrite").partitionBy(schema.COLUMNA_PARTICION).parquet(SALIDA))

        escrito = spark.read.parquet(SALIDA)
        finales = {r["Departamento"]: r["final"] for r in embudo.contar(escrito, "final").collect()}
        for r in por_depto:
            r["final"] = finales.get(r["Departamento"], 0)
            r["r12b_copia_exacta"] = r["entrada"] - r["r12_preliminar_duplicado"] - r["final"]
        embudo.guardar_e_imprimir(
            spark, por_depto,
            [("entrada", "Silver IDEAM"), ("r12_preliminar_duplicado", "R12 prelim"),
             ("r12b_copia_exacta", "R12b copia"), ("final", "Integrado")],
            archivos.unir(REPORTES, "embudo_integracion"), "Embudo integración (D9)")
        r = escrito.agg(F.countDistinct("CodigoEstacion").alias("est"),
                        F.date_format(F.min("Fecha"), "yyyy-MM-dd").alias("ini"),
                        F.date_format(F.max("Fecha"), "yyyy-MM-dd").alias("fin"),
                        F.avg(F.col("ALLSKY_SFC_SW_DWN").isNotNull().cast("double")).alias("nasa")).first()
        print(f"\nEstaciones: {r['est']} | Fechas: {r['ini']} a {r['fin']} | "
              f"Con dato NASA: {r['nasa'] * 100:.1f} % | Guardado: {SALIDA}")
    finally:
        if propia:
            spark.stop()


if __name__ == "__main__":
    main()
