"""
Silver — NASA POWER por estación y hora (reglas 8 a 10).

No descarga nada: lee los JSON que dejó R4 en bronze. Para cada estación de silver/ideam
se buscan los archivos de sus coordenadas y de los años que cubre (como silver.py en pandas).

Entrada:
    silver/ideam/                                  (salida de silver/ideam.py: estaciones y años)
    bronze/nasa/<lat>_<lon>_<año>_UTC.json         respuestas crudas de la NASA
Salida:
    silver/nasa/Anio=<año>/*.parquet               CodigoEstacion, Fecha y las 4 variables

REGLAS
   8. La NASA viene en UTC -> hora de Colombia (UTC-5).
   9. +1 hora: la NASA marca el INICIO del periodo y el IDEAM el FINAL.  (8 + 9 = -4 horas)
  10. Los -999 (dato faltante) pasan a vacío.
  Solo se usan los años de la NASA desde 2001 que cubren el periodo de cada estación.

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.silver.nasa
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

from calcusol.common import archivos
from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

SILVER_IDEAM = settings.silver_path("ideam")
BRONZE_NASA = settings.bronze_path("nasa")
SALIDA = settings.silver_path("nasa")

VARIABLES = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]
PRIMER_ANIO_NASA = 2001
HORAS_COLOMBIA = -5
AJUSTE_ETIQUETA = 1          # NASA marca el inicio del periodo, IDEAM el final
SUFIJO = "_UTC"              # los JSON de R4 terminan en _UTC.json
FALTANTE = -999

ESQUEMA_JSON = T.StructType([T.StructField("properties", T.StructType([
    T.StructField("parameter", T.MapType(T.StringType(), T.MapType(T.StringType(), T.DoubleType())))
]))])


def nombre_json(lat: float, lon: float, anio: int) -> str:
    """Nombre del JSON de la NASA, igual que lo guarda R4 (coordenadas a 4 decimales)."""
    return f"{lat:.4f}_{lon:.4f}_{anio}{SUFIJO}.json"


def estaciones(ideam: DataFrame) -> list[dict]:
    """Estaciones con coordenadas, con el primer y último año de datos (tabla pequeña)."""
    filas = ideam.groupBy("CodigoEstacion").agg(
        F.first("Latitud").alias("Latitud"), F.first("Longitud").alias("Longitud"),
        F.year(F.min("Fecha")).alias("ini"), F.year(F.max("Fecha")).alias("fin"),
    ).collect()
    return [r.asDict() for r in filas if r["Latitud"] is not None and r["Longitud"] is not None]


def transformar_nasa(spark: SparkSession, ideam: DataFrame) -> DataFrame | None:
    """Devuelve CodigoEstacion, Fecha y las 4 variables de la NASA (reglas 8-10)."""
    existentes = {archivos.nombre(a) for a in archivos.listar(BRONZE_NASA, ".json")}
    cruce, rutas, faltan = [], [], []
    for e in estaciones(ideam):
        llave = f"{e['Latitud']:.4f}_{e['Longitud']:.4f}"
        for anio in range(max(e["ini"], PRIMER_ANIO_NASA), e["fin"] + 1):
            nombre = nombre_json(e["Latitud"], e["Longitud"], anio)
            if nombre in existentes:
                rutas.append(archivos.unir(BRONZE_NASA, nombre))
                cruce.append((e["CodigoEstacion"], llave, anio))
            else:
                faltan.append((e["CodigoEstacion"], anio))
    if faltan:
        antes = sum(1 for _, a in faltan if a < schema.ANIO_MINIMO)
        print(f"   ! Faltan {len(faltan)} archivos de la NASA (estación-año): {antes} son de antes de "
              f"{schema.ANIO_MINIMO} (no afectan gold, D1) y {len(faltan) - antes} de {schema.ANIO_MINIMO} en adelante")
        recientes = [f for f in faltan if f[1] >= schema.ANIO_MINIMO]
        if recientes:
            print(f"     Ejemplos desde {schema.ANIO_MINIMO}: {recientes[:10]}")
    if not rutas:
        return None

    crudo = (spark.read.schema(ESQUEMA_JSON).option("multiLine", True).json(sorted(set(rutas)))
             .withColumn("_archivo", F.input_file_name()))
    patron = r"([-0-9.]+_[-0-9.]+)_(\d{4})(?:_UTC)?\.json$"
    p = F.col("properties.parameter")
    nasa = (crudo.select(F.regexp_extract("_archivo", patron, 1).alias("llave"),
                         F.regexp_extract("_archivo", patron, 2).cast("int").alias("anio"),
                         F.explode(p.getItem(VARIABLES[0])).alias("hora_utc", "_v0"),
                         p.alias("_p"))
            .select("llave", "anio",
                    (F.to_timestamp("hora_utc", "yyyyMMddHH")                   # reglas 8 y 9
                     + F.expr(f"INTERVAL {HORAS_COLOMBIA + AJUSTE_ETIQUETA} HOURS")).alias("Fecha"),
                    *[F.element_at(F.col("_p").getItem(v), F.col("hora_utc")).alias(v) for v in VARIABLES]))
    for v in VARIABLES:                                                         # regla 10
        nasa = nasa.withColumn(v, F.when(F.col(v) == FALTANTE, None).otherwise(F.col(v)).cast("float"))

    tabla = spark.createDataFrame(cruce, "CodigoEstacion long, llave string, anio int")
    return (nasa.join(F.broadcast(tabla), ["llave", "anio"])
            .select("CodigoEstacion", "Fecha", *VARIABLES)
            .dropDuplicates(["CodigoEstacion", "Fecha"]))


def main(spark: SparkSession | None = None) -> None:
    propia = spark is None
    spark = get_spark("Calcusol-Silver-NASA", existing=spark)
    try:
        ideam = spark.read.parquet(SILVER_IDEAM)
        nasa = transformar_nasa(spark, ideam)
        if nasa is None:
            raise SystemExit(f"No encontré archivos de la NASA en {BRONZE_NASA}")
        (nasa.withColumn(schema.COLUMNA_PARTICION, F.year("Fecha"))
             .repartition(schema.COLUMNA_PARTICION)
             .sortWithinPartitions("CodigoEstacion", "Fecha")
             .write.mode("overwrite").partitionBy(schema.COLUMNA_PARTICION).parquet(SALIDA))
        escrito = spark.read.parquet(SALIDA)
        r = escrito.agg(F.count(F.lit(1)).alias("filas"),
                        F.countDistinct("CodigoEstacion").alias("estaciones")).first()
        print(f"\nSilver NASA: {r['filas']:,} filas | {r['estaciones']} estaciones | Guardado: {SALIDA}")
    finally:
        if propia:
            spark.stop()


if __name__ == "__main__":
    main()
