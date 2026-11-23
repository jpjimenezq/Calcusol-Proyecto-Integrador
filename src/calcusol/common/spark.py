"""
Fábrica de SparkSession.

Portabilidad: en local se crea con master local[*]; en Glue, Glue inyecta su
propia sesión y este módulo la reutiliza sin forzar master(). Por eso get_spark
acepta una sesión existente.

Configuración común (igual en el PC, en Docker con Spark 4 y en Glue 5.0 con Spark 3.5):
  - Zona horaria UTC: las fechas ya vienen en hora de Colombia; así Spark no las mueve.
  - Modo NO ANSI: en Spark 4 viene activado y una conversión fallida detiene el programa;
    en Spark 3.5 (Glue) devuelve vacío. Se fija el comportamiento de 3.5 en todos lados.
  - Fechas que no encajan con un formato -> vacío (timeParserPolicy CORRECTED).
"""

from __future__ import annotations

import os
import sys

from pyspark.sql import SparkSession

ZONA_HORARIA = "UTC"
HADOOP_WINDOWS = r"C:\hadoop"          # carpeta con bin\winutils.exe y bin\hadoop.dll (solo en Windows)

CONFIGURACION = {
    "spark.sql.session.timeZone": ZONA_HORARIA,
    "spark.sql.ansi.enabled": "false",
    "spark.sql.legacy.timeParserPolicy": "CORRECTED",
    "spark.sql.shuffle.partitions": "64",
}


def en_glue() -> bool:
    """True si el código corre como job de AWS Glue (Glue siempre pasa --JOB_NAME)."""
    return "--JOB_NAME" in sys.argv


def _configurar(spark: SparkSession) -> SparkSession:
    for clave, valor in CONFIGURACION.items():
        spark.conf.set(clave, valor)
    return spark


def _preparar_windows() -> None:
    """Ajustes del PC con Windows: mismo Python para Spark, sin el límite _JAVA_OPTIONS y winutils."""
    import pyspark
    os.environ.setdefault("SPARK_HOME", os.path.dirname(pyspark.__file__))
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    os.environ.pop("_JAVA_OPTIONS", None)          # le ganaría a la memoria que se pide
    if os.name == "nt" and os.path.exists(os.path.join(HADOOP_WINDOWS, "bin", "winutils.exe")):
        os.environ.setdefault("HADOOP_HOME", HADOOP_WINDOWS)
        os.environ["PATH"] = os.path.join(HADOOP_WINDOWS, "bin") + os.pathsep + os.environ["PATH"]


def get_spark(app_name: str = "Calcusol", existing: SparkSession | None = None,
              memoria: str | None = None, nucleos: str | None = None) -> SparkSession:
    """
    Devuelve una SparkSession configurada.

    - Si se pasa `existing` o ya hay una sesión activa, se reutiliza.
    - En Glue: usa la sesión que prepara Glue (sin fijar master ni memoria).
    - En el PC: la crea con master local[nucleos] y `memoria` de RAM para Spark.
      Por defecto 8g y todos los núcleos; se cambian con las variables de entorno
      CALCUSOL_SPARK_MEMORIA (ej. 4g) y CALCUSOL_SPARK_NUCLEOS (ej. 2).
    """
    memoria = memoria or os.environ.get("CALCUSOL_SPARK_MEMORIA", "8g")
    nucleos = nucleos or os.environ.get("CALCUSOL_SPARK_NUCLEOS", "*")
    if existing is not None:
        return _configurar(existing)
    activa = SparkSession.getActiveSession()
    if activa is not None:
        return _configurar(activa)
    if en_glue():
        return _configurar(SparkSession.builder.appName(app_name).getOrCreate())

    _preparar_windows()
    constructor = (SparkSession.builder.appName(app_name).master(f"local[{nucleos}]")
                   .config("spark.driver.memory", memoria)
                   .config("spark.ui.showConsoleProgress", "false"))
    for clave, valor in CONFIGURACION.items():
        constructor = constructor.config(clave, valor)
    spark = constructor.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark
