"""
Gold — Dataset listo para el modelo (filtros G1-G3 y D1).

Entrada:
    gold/integrado/          (gold/integracion.py: las 17 columnas, con noches y vacíos)
Salida:
    gold/dataset/Anio=<año>/*.parquet     gold v1, partido por año (D17)
    gold/reportes/embudo_gold/            filas que pierde cada regla, por departamento (D9)

REGLAS (en el orden de gold.py; D1 va al final)
  G1. Solo horas de sol: CLRSKY_SFC_SW_DWN > 0.
  G2. Sin vacíos en Valor, variables de la NASA ni ubicación.
  G3. Sin valores negativos del IDEAM.
  D1. Solo años 2005 a 2022.
  Al final se revisa todo el contrato (config/schema.py: validar_gold).

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.gold.features
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from calcusol.common import archivos, embudo
from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

ENTRADA = settings.gold_path("integrado")
SALIDA = settings.gold_path("dataset")
REPORTES = settings.gold_path("reportes")

X_NASA = schema.X_NASA
X_LUGAR = schema.X_LUGAR
ULTIMO_ANIO_ENTRENAMIENTO = 2019        # D0: entrenar hasta 2019, probar 2020-2022
REGLAS = ["G1_noche", "G2_vacios", "G3_negativos", "D1_antes_2005"]


def marcar(df: DataFrame) -> DataFrame:
    """Cada fila queda marcada con la PRIMERA regla que no cumple (o "ok")."""
    g1 = F.coalesce(F.col("CLRSKY_SFC_SW_DWN") > 0, F.lit(False))
    g2 = F.lit(True)
    for c in [schema.OBJETIVO] + X_NASA + X_LUGAR:
        g2 = g2 & F.col(c).isNotNull()
    g3 = F.col(schema.OBJETIVO) >= 0
    d1 = F.year("Fecha").between(schema.ANIO_MINIMO, schema.ANIO_MAXIMO)
    motivo = (F.when(~g1, "G1_noche").when(~g2, "G2_vacios")
              .when(~g3, "G3_negativos").when(~d1, "D1_antes_2005").otherwise("ok"))
    return df.withColumn("_motivo", motivo)


def construir(spark: SparkSession) -> tuple[DataFrame, list[dict]]:
    """Aplica G1-G3 y D1 y valida el contrato. Devuelve (gold, conteos por departamento)."""
    marcado = marcar(spark.read.parquet(ENTRADA))
    conteo = (marcado.groupBy("Departamento").pivot("_motivo", REGLAS + ["ok"]).count()
              .fillna(0).collect())
    filas = []
    for r in conteo:
        fila = {"Departamento": r["Departamento"], **{k: int(r[k]) for k in REGLAS}, "final": int(r["ok"])}
        fila["entrada"] = sum(fila[k] for k in REGLAS) + fila["final"]
        filas.append(fila)
    gold = marcado.filter(F.col("_motivo") == "ok").drop("_motivo")
    gold = schema.validar_gold(gold.withColumn(schema.COLUMNA_PARTICION, F.year("Fecha")), "gold v1")
    return gold, filas


def main(spark: SparkSession | None = None) -> None:
    propia = spark is None
    spark = get_spark("Calcusol-Gold-Features", existing=spark)
    try:
        gold, filas = construir(spark)
        (gold.repartition(schema.COLUMNA_PARTICION)
             .sortWithinPartitions("Departamento", "CodigoEstacion", "Fecha")
             .write.mode("overwrite").partitionBy(schema.COLUMNA_PARTICION).parquet(SALIDA))
        embudo.guardar_e_imprimir(
            spark, filas,
            [("entrada", "Integrado"), ("G1_noche", "G1 noche"), ("G2_vacios", "G2 vacíos"),
             ("G3_negativos", "G3 neg."), ("D1_antes_2005", "D1 <2005"), ("final", "Gold")],
            archivos.unir(REPORTES, "embudo_gold"), "Embudo gold (D9)")
        print(f"\nGuardado: {SALIDA}")
    finally:
        if propia:
            spark.stop()


if __name__ == "__main__":
    main()
