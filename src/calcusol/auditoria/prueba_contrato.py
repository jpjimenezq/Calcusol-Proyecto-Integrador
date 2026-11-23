"""
Auditoría — Prueba del contrato de gold (config/schema.py).

Crea una tabla de ejemplo que SÍ cumple y 10 tablas con un error a propósito cada una.
El contrato debe aceptar la primera y rechazar las 10.

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.auditoria.prueba_contrato
"""

from __future__ import annotations

import sys

from pyspark.sql import functions as F
from pyspark.sql import types as T

from calcusol.common.spark import get_spark
from calcusol.config import schema


def tabla_de_ejemplo(spark):
    """Dos horas de una estación inventada que SÍ cumplen el contrato."""
    esquema_texto = T.StructType([
        T.StructField(c, T.StringType() if c == "Fecha" else schema.TIPOS_GOLD[c]) for c in schema.COLUMNAS_GOLD
    ])
    filas = [
        (1, "ESTACION PRUEBA", "Antioquia", "2010-03-15 12:00:00", False,
         650.0, 900.0, 0.60, 24.5, 6.22, -75.59, 1490.0, 12, 3, 74, 0, 610.0),
        (1, "ESTACION PRUEBA", "Antioquia", "2010-03-15 13:00:00", True,
         580.0, 850.0, 0.55, 25.1, 6.22, -75.59, 1490.0, 13, 3, 74, 0, 540.0),
    ]
    df = spark.createDataFrame(filas, esquema_texto)
    return df.withColumn("Fecha", F.to_timestamp("Fecha")).select(*schema.COLUMNAS_GOLD)


def main() -> None:
    spark = get_spark("Calcusol-Prueba-Contrato")
    try:
        buena = tabla_de_ejemplo(spark)
        print("1) Tabla buena (debe pasar):")
        schema.validar_gold(buena, "tabla buena")

        malas = {
            "falta Sitio": buena.drop("Sitio"),
            "columna de más": buena.withColumn("NivelAprobacion", F.lit("Definitivo")),
            "Valor como double": buena.withColumn("Valor", F.col("Valor").cast("double")),
            "T2M vacío": buena.withColumn("T2M", F.lit(None).cast("float")),
            "Valor de 1500": buena.withColumn(
                "Valor", F.when(F.col("Hora") == 12, F.lit(1500.0)).otherwise(F.col("Valor")).cast("float")),
            "año 2003": buena.withColumn("Fecha", F.col("Fecha") - F.expr("INTERVAL 7 YEARS")),
            "Hora corrida": buena.withColumn("Hora", F.col("Hora") + 1),
            "departamento mal escrito": buena.withColumn("Departamento", F.lit("ANTIOQUIA")),
            "fila repetida": buena.unionByName(buena.limit(1)),
            "estación en dos Sitios": buena.withColumn(
                "Sitio", F.when(F.col("Hora") == 13, F.lit(1)).otherwise(F.col("Sitio")).cast("int")),
        }
        print("\n2) Tablas malas (todas deben ser rechazadas):")
        no_rechazadas = 0
        for nombre, mala in malas.items():
            try:
                schema.validar_gold(mala, nombre)
                print(f"   X NO la rechazó: {nombre}")
                no_rechazadas += 1
            except schema.ContratoIncumplido as e:
                print(f"   OK rechazada '{nombre}' -> {e.problemas[0]}")
        print()
        if no_rechazadas:
            print(f"ATENCIÓN: {no_rechazadas} tablas malas pasaron. El contrato no está completo.")
            sys.exit(1)
        print("El contrato funciona: acepta la tabla buena y rechaza las 10 malas.")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
