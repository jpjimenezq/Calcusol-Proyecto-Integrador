"""
Embudo de filas (decisión D9): cuántas filas pierde cada regla en cada departamento.

Cada etapa (silver IDEAM, integración, gold) guarda su parte en <zona>/reportes/embudo_<etapa>/
y gold/validacion.py las junta en un solo embudo.
"""

from __future__ import annotations

from collections import defaultdict

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F


def contar(df: DataFrame, nombre: str) -> DataFrame:
    """Filas por departamento con el nombre de columna `nombre`."""
    return df.groupBy("Departamento").agg(F.count(F.lit(1)).alias(nombre))


def guardar_e_imprimir(spark: SparkSession, filas: list[dict], columnas: list[tuple[str, str]],
                       ruta: str, titulo: str) -> None:
    """
    filas:    una por departamento, con 'Departamento' y una clave por cada columna.
    columnas: (clave, título corto) en el orden de la tabla; la primera es la entrada, la última la salida.
    """
    filas = sorted(filas, key=lambda r: r["Departamento"])
    claves = [c for c, _ in columnas]
    tabla = spark.createDataFrame([[r["Departamento"], *(int(r[c]) for c in claves)] for r in filas],
                                  ["Departamento", *claves])
    tabla.coalesce(1).write.mode("overwrite").option("header", True).csv(ruta)

    print(f"\n--- {titulo} ---")
    print(f"{'Departamento':<22}" + "".join(f"{t:>13}" for _, t in columnas))
    total = defaultdict(int)
    for r in filas:
        print(f"{r['Departamento']:<22}" + "".join(f"{int(r[c]):>13,}" for c in claves))
        for c in claves:
            total[c] += int(r[c])
    print(f"{'TOTAL':<22}" + "".join(f"{total[c]:>13,}" for c in claves))
