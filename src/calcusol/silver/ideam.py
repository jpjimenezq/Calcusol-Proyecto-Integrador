"""
Silver — IDEAM limpio y estandarizado (PySpark).

Porta a PySpark las reglas de limpieza IDEAM del código de referencia
(codigo_propuesto/silver.py), aplicando las decisiones cerradas en
docs/contrato_datos.md. NO cruza con NASA (eso es silver/nasa.py + gold).

REGLAS (lógica pura, cada una (df) -> df):
  1. Columnas útiles.
  2. Lectura UTF-8 (confirmado: los CSV son UTF-8, no latin-1).
  3. Fechas en formatos mixtos (ISO y D/M/Y) con try_to_timestamp (Spark 4 ANSI).
  4. Dedup por (estación, hora): se queda el de mayor aprobación (Definitivo).
  5. Coordenadas desde el catálogo (coma decimal -> punto; altitud sin separador
     de miles).
  6. Descarta Valor > 1400 Wh/m² (imposible; máx real observado 1181.8).
  7. Nombre de departamento oficial, desde la carpeta de origen.
  +  Filtro de año: desde 2005 (D-AÑO).
  +  Embudo de filas: cuántas entran/salen por regla (D9).

Salida: data/silver/ideam/  (Parquet particionado por Departamento).

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.silver.ideam
"""

from __future__ import annotations

import unicodedata

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from calcusol.common.spark import get_spark
from calcusol.config import settings

MEDICIONES = settings.bronze_path("ideam", "mediciones")
CATALOGO = settings.bronze_path("ideam", "catalogo", "CNE_IDEAM.csv")
SALIDA = settings.silver_path("ideam")

COLUMNAS_MEDICION = ["CodigoEstacion", "NombreEstacion", "Fecha", "Valor", "NivelAprobacion"]
VALOR_MAXIMO = 1400
ANIO_CORTE = 2005

OFICIALES = [
    "Amazonas", "Antioquia", "Arauca", "Atlántico", "Bogotá", "Bolívar", "Boyacá",
    "Caldas", "Caquetá", "Casanare", "Cauca", "Cesar", "Chocó", "Córdoba",
    "Cundinamarca", "Guainía", "Guaviare", "Huila", "La Guajira", "Magdalena",
    "Meta", "Nariño", "Norte de Santander", "Putumayo", "Quindío", "Risaralda",
    "San Andrés", "Santander", "Sucre", "Tolima", "Valle del Cauca", "Vaupés", "Vichada",
]


def _clave(texto: str) -> str:
    sin = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin.lower().split())


NOMBRE_OFICIAL = {_clave(n): n for n in OFICIALES}


def nombre_departamento(carpeta: str) -> str:
    return NOMBRE_OFICIAL.get(_clave(carpeta), carpeta)


# ================================================================== Catálogo
def cargar_catalogo(spark: SparkSession) -> DataFrame:
    """
    Coordenadas por estación, normalizadas (regla 5).

    El CNE tiene un campo OBSERVACION con texto libre, saltos de línea y comillas;
    se activa multiLine + quote/escape para que no desplace columnas, y se usa
    try_cast para que cualquier fila mal parseada caiga a NULL en vez de abortar
    (parser ANSI de Spark 4). Esas filas se descartan con el filtro final.
    """
    cat = (
        spark.read
        .option("header", True)
        .option("multiLine", True)
        .option("quote", '"')
        .option("escape", '"')
        .csv(CATALOGO)
        .select(
            F.trim(F.col("CODIGO")).try_cast("long").alias("CodigoEstacion"),
            F.regexp_replace(F.trim("LATITUD"), ",", ".")
            .try_cast("double").alias("Latitud"),
            F.regexp_replace(F.trim("LONGITUD"), ",", ".")
            .try_cast("double").alias("Longitud"),
            # altitud: quitar separador de miles (punto) antes de castear
            F.regexp_replace(F.trim("ALTITUD"), r"\.", "")
            .try_cast("double").alias("Altitud"),
        )
        .filter(F.col("CodigoEstacion").isNotNull())
        .dropDuplicates(["CodigoEstacion"])
    )
    return cat


# ================================================================== Lógica pura
def leer_mediciones(spark: SparkSession) -> DataFrame:
    """
    Lee los CSV de mediciones RAD SOLAR (regla 1, 2). Añade Departamento desde
    la ruta del archivo (regla 7) usando el nombre de carpeta.
    """
    df = (
        spark.read
        .option("header", True)
        .option("recursiveFileLookup", True)
        .option("encoding", "UTF-8")
        .csv(MEDICIONES)
        .filter(F.col("Variable") == "RAD SOLAR")
    )
    # Departamento desde la ruta: .../mediciones/<Depto>/archivo.csv
    ruta = F.input_file_name()
    carpeta = F.regexp_extract(ruta, r"mediciones/([^/]+)/[^/]+\.csv$", 1)
    df = df.withColumn("_CarpetaDepto", carpeta)
    return df.select(*COLUMNAS_MEDICION, "_CarpetaDepto")


def parsear(df: DataFrame) -> DataFrame:
    """Tipos: código a long, valor a float (coma->punto), fecha mixta (regla 3)."""
    fecha = F.coalesce(
        F.try_to_timestamp("Fecha", F.lit("yyyy-MM-dd HH:mm")),
        F.try_to_timestamp("Fecha", F.lit("dd/MM/yyyy HH:mm")),
        F.try_to_timestamp("Fecha", F.lit("yyyy-MM-dd HH:mm:ss")),
        F.try_to_timestamp("Fecha", F.lit("dd/MM/yyyy HH:mm:ss")),
    )
    return (
        df.withColumn("CodigoEstacion", F.col("CodigoEstacion").cast("long"))
        .withColumn("Valor",
                    F.regexp_replace(F.col("Valor"), ",", ".").cast("float"))
        .withColumn("Fecha", fecha)
        .withColumn("EsPreliminar",
                    F.lower(F.trim(F.col("NivelAprobacion"))) == F.lit("preliminar"))
    )


def quitar_duplicados_exactos(df: DataFrame) -> DataFrame:
    """
    Elimina filas idénticas en TODOS los campos relevantes (tu auditoría 1.5:
    los 18 duplicados exactos de la estación 52025090). Operación segura e
    incuestionable: una fila idéntica no aporta información.
    """
    campos = ["CodigoEstacion", "Fecha", "Valor", "NivelAprobacion",
              "NombreEstacion", "EsPreliminar"]
    return df.dropDuplicates(campos)


def contar_conflictos_hora(df: DataFrame) -> int:
    """
    Cuenta claves (estación, hora) que tras la dedup exacta aún tienen más de un
    registro, es decir, mismo timestamp con distinto valor o aprobación.
    Es la evidencia que decide si colapsar importa o es marginal.
    """
    return (
        df.groupBy("CodigoEstacion", "Fecha")
        .count()
        .filter(F.col("count") > 1)
        .count()
    )


def colapsar_conflictos_hora(df: DataFrame) -> DataFrame:
    """
    Para claves (estación, hora) con varios registros, conserva el de mayor
    aprobación (Definitivo antes que Preliminar). Justificación física: un
    piranómetro no mide dos radiaciones distintas para la misma hora. Requisito
    técnico para el cruce con NASA (único por estación-hora).
    """
    w = Window.partitionBy("CodigoEstacion", "Fecha").orderBy(
        F.col("EsPreliminar").asc())  # Definitivo (False) primero -> se conserva
    return (
        df.withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )


def unir_catalogo(df: DataFrame, catalogo: DataFrame) -> DataFrame:
    """Regla 5: adjunta coordenadas por estación."""
    return df.join(F.broadcast(catalogo), on="CodigoEstacion", how="left")


# ================================================================== Pipeline
def transformar_ideam(spark: SparkSession, con_embudo: bool = True) -> DataFrame:
    """
    Aplica todas las reglas y devuelve el IDEAM limpio. Si con_embudo, imprime
    el embudo de filas por regla (D9).
    """
    catalogo = cargar_catalogo(spark)
    crudo = leer_mediciones(spark)
    df = parsear(crudo)

    embudo = []

    def paso(nombre: str, d: DataFrame) -> DataFrame:
        if con_embudo:
            n = d.count()
            embudo.append((nombre, n))
            print(f"  embudo · {nombre}: {n:,}")
        return d

    df = paso("0_crudo_rad_solar", df)

    # fecha inválida fuera
    df = df.filter(F.col("Fecha").isNotNull())
    df = paso("1_fecha_valida", df)

    # 1.5: duplicados EXACTOS (filas idénticas) — eliminación segura
    df = quitar_duplicados_exactos(df)
    df = paso("2_sin_duplicados_exactos", df)

    # Evidencia: ¿cuántos conflictos estación-hora quedan? (decide si colapsar importa)
    if con_embudo:
        conflictos = contar_conflictos_hora(df)
        print(f"  conflictos estación-hora (mismo timestamp, distinto valor/aprob): {conflictos:,}")

    # Colapso estación-hora por mayor aprobación (unicidad para el cruce NASA)
    df = colapsar_conflictos_hora(df)
    df = paso("3_unico_por_estacion_hora", df)

    # regla 6: valores imposibles (> 1400). NO se tocan negativos ni ceros (1.4).
    df = df.filter((F.col("Valor").isNull()) | (F.col("Valor") <= VALOR_MAXIMO))
    df = paso("4_sin_imposibles", df)

    # filtro de año (D-AÑO)
    df = df.filter(F.year("Fecha") >= ANIO_CORTE)
    df = paso(f"5_desde_{ANIO_CORTE}", df)

    # regla 7: departamento oficial, normalizando el nombre de carpeta igual que
    # _clave() en Python (sin tildes, minúsculas, espacios colapsados) y buscando
    # en un mapa clave->nombre oficial.
    norm = F.lower(
        F.regexp_replace(
            F.translate(F.col("_CarpetaDepto"), "áéíóúÁÉÍÓÚñÑ", "aeiouAEIOUnN"),
            r"\s+", " ",
        )
    )
    norm = F.trim(norm)
    pares: list = []
    for clave, oficial in NOMBRE_OFICIAL.items():
        pares += [F.lit(clave), F.lit(oficial)]
    mapping = F.create_map(*pares)
    df = df.withColumn(
        "Departamento",
        F.coalesce(mapping[norm], F.col("_CarpetaDepto")),
    )

    # regla 5: coordenadas
    df = unir_catalogo(df, catalogo)

    df = df.select(
        "CodigoEstacion", "NombreEstacion", "Departamento", "Fecha",
        "EsPreliminar", "Valor", "NivelAprobacion",
        "Latitud", "Longitud", "Altitud",
    )

    if con_embudo:
        sin_coord = df.filter(F.col("Latitud").isNull()).select(
            "CodigoEstacion").distinct().count()
        print(f"  estaciones sin coordenadas: {sin_coord}")
        print(f"  estaciones totales: {df.select('CodigoEstacion').distinct().count()}")

    return df


def main() -> None:
    spark = get_spark("Calcusol-Silver-IDEAM")
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = transformar_ideam(spark)
        (df.write.mode("overwrite")
           .partitionBy("Departamento")
           .parquet(SALIDA))
        print(f"\nSilver IDEAM escrito en: {SALIDA}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
