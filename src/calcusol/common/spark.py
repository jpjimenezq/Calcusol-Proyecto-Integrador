"""
Fábrica de SparkSession.

Portabilidad: en local se crea con master local[*]; en Glue, Glue inyecta su
propia sesión y este módulo la reutiliza sin forzar master(). Por eso get_spark
acepta una sesión existente.
"""

from __future__ import annotations

from pyspark.sql import SparkSession


def get_spark(app_name: str = "Calcusol", existing: SparkSession | None = None) -> SparkSession:
    """
    Devuelve una SparkSession.

    - En local: la crea con master local[*] si no hay una activa.
    - En Glue: pasar la sesión que Glue ya creó mediante `existing`.
    """
    if existing is not None:
        return existing

    active = SparkSession.getActiveSession()
    if active is not None:
        return active

    return (
        SparkSession.builder
        .appName(app_name)
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )
