"""
Silver — IDEAM limpio, con ubicación (reglas 1 a 7).

Entrada (bronze, copia de s3://calcusol-datos/bronze/):
    bronze/ideam/mediciones/<Departamento>/*.csv   CSV del DHIME
    bronze/ideam/catalogo/CNE_IDEAM.csv            catálogo de estaciones (el del manifest de R4)
Salida:
    silver/ideam/Anio=<año>/*.parquet              una fila por estación y hora
    silver/reportes/embudo_ideam/                  filas que pierde cada regla, por departamento (D9)
    silver/reportes/estaciones_sin_coordenadas/    estaciones sin ubicación en el catálogo (D11)

REGLAS (mismos números que silver.py, la versión de referencia en pandas)
   1. Solo columnas útiles: código, nombre, fecha, valor, nivel de aprobación.
      Si el CSV trae la columna Variable, solo se dejan las filas de "RAD SOLAR".
   2. CSV de Excel: separador ';', coma decimal, tildes en latin-1 (se detecta por archivo).
   3. Fechas en 2005-05-01 01:00 o 01/05/2005 01:00. Una fecha que no se entiende se quita y se cuenta.
   4. Hora repetida en la misma estación: se queda la de mayor aprobación
      (si empatan, la primera del archivo, como pandas).
   5. Latitud, longitud y altitud desde el catálogo.
   6. Se quitan los valores mayores a 1400 Wh/m² en una hora (los vacíos se quedan).
   7. Nombres de departamento unificados (QUINDÍO -> Quindío).

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.silver.ideam                    -> todos los departamentos
  python -m calcusol.silver.ideam Amazonas Caldas    -> solo esos
"""

from __future__ import annotations

import argparse
import unicodedata
from collections import defaultdict

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from calcusol.common import archivos, embudo
from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

MEDICIONES = settings.bronze_path("ideam", "mediciones")
CATALOGO = settings.bronze_path("ideam", "catalogo", "CNE_IDEAM.csv")
SALIDA = settings.silver_path("ideam")
REPORTES = settings.silver_path("reportes")

FORMATOS_FECHA = ["yyyy-MM-dd HH:mm", "dd/MM/yyyy HH:mm", "dd/MM/yyyy HH:mm:ss", "yyyy-MM-dd HH:mm:ss"]
COLUMNAS_SALIDA = ["CodigoEstacion", "NombreEstacion", "Departamento", "Fecha", "EsPreliminar",
                   "Valor", "Latitud", "Longitud", "Altitud"]


# ------------------------------------------------------------------ Departamentos (regla 7)
def clave(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


NOMBRE_OFICIAL = {clave(n): n for n in schema.OFICIALES}


def nombre_departamento(carpeta: str) -> str:
    nombre = NOMBRE_OFICIAL.get(clave(carpeta))
    if nombre is None:
        print(f"   ! '{carpeta}' no coincide con ningún departamento oficial, se deja igual")
        return carpeta
    return nombre


def carpetas_departamento(departamentos: list[str] | None = None) -> list[str]:
    """Carpetas de mediciones, opcionalmente solo las pedidas."""
    if not archivos.existe(MEDICIONES):
        raise SystemExit(f"No encuentro {MEDICIONES}. ¿Está bronze en {settings.data_root()}?")
    carpetas = archivos.listar_carpetas(MEDICIONES)
    if departamentos:
        pedidos = {clave(d) for d in departamentos}
        carpetas = [c for c in carpetas if clave(archivos.nombre(c)) in pedidos
                    or clave(nombre_departamento(archivos.nombre(c))) in pedidos]
    if not carpetas:
        raise SystemExit("Ningún departamento coincide con lo pedido")
    return carpetas


# ------------------------------------------------------------------ Lectura (reglas 1-3)
def detectar_formato(archivo: str):
    """Mira el inicio del CSV y devuelve (codificación, separador), o None si no sirve.
    Se revisan los primeros 64 KB (no solo la cabecera) para notar tildes en latin-1."""
    crudo = archivos.leer_inicio(archivo)
    fin_linea = crudo.rfind(b"\n")
    if fin_linea > 0:
        crudo = crudo[:fin_linea]                    # no cortar una letra a la mitad
    try:
        texto, codificacion = crudo.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        texto, codificacion = crudo.decode("latin-1"), "ISO-8859-1"
    lineas = texto.splitlines()
    if not lineas:
        return None
    encabezado = lineas[0].lstrip("﻿")
    for separador in (",", ";"):
        columnas = [c.strip().strip('"') for c in encabezado.split(separador)]
        if "CodigoEstacion" in columnas:
            return codificacion, separador
    return None


def leer_mediciones(spark: SparkSession, carpetas: list[str]) -> DataFrame:
    """Lee los CSV de cada departamento con su formato.
    Devuelve CodigoEstacion, NombreEstacion, Fecha, Valor, EsPreliminar, Departamento y columnas internas."""
    partes = []
    for carpeta in carpetas:
        depto = nombre_departamento(archivos.nombre(carpeta))
        grupos = defaultdict(list)
        for archivo in archivos.listar(carpeta, ".csv"):
            formato = detectar_formato(archivo)
            if formato is None:
                print(f"   ! {archivos.nombre(carpeta)}/{archivos.nombre(archivo)}: "
                      "no tiene la columna CodigoEstacion, no se lee")
            else:
                grupos[formato].append(archivo)
        for (codificacion, separador), rutas in grupos.items():
            df = spark.read.csv(rutas, header=True, sep=separador, encoding=codificacion)
            df = df.toDF(*[c.lstrip("﻿").strip().strip('"') for c in df.columns])
            fecha_texto = F.trim("Fecha")
            es_rad = (F.upper(F.trim("Variable")) == "RAD SOLAR") if "Variable" in df.columns else F.lit(True)
            partes.append(df.select(                                            # regla 1
                F.col("CodigoEstacion").cast("long").alias("CodigoEstacion"),
                F.col("NombreEstacion"),
                F.coalesce(*[F.to_timestamp(fecha_texto, f) for f in FORMATOS_FECHA]).alias("Fecha"),  # regla 3
                F.regexp_replace("Valor", ",", ".").cast("float").alias("Valor"),                     # regla 2
                F.coalesce(F.lower(F.trim("NivelAprobacion")) == "preliminar", F.lit(False)).alias("EsPreliminar"),
                F.lit(depto).alias("Departamento"),                                                  # regla 7
                F.input_file_name().alias("_archivo"),
                F.monotonically_increasing_id().alias("_orden"),
                F.coalesce(es_rad, F.lit(False)).alias("_es_rad"),
            ))
    if not partes:
        raise SystemExit("No se encontró ningún CSV del IDEAM para leer")
    ideam = partes[0]
    for p in partes[1:]:
        ideam = ideam.unionByName(p)
    return ideam


def leer_catalogo(spark: SparkSession, ruta: str = CATALOGO) -> DataFrame:
    """Catálogo -> CodigoEstacion, Latitud, Longitud, Altitud (regla 5).
    Sirve para CNE_IDEAM.csv (CODIGO, ..., con un campo de texto libre con saltos de línea)
    y para el catálogo anterior (Codigo, ..., Altitud)."""
    if not archivos.existe(ruta):
        raise SystemExit(f"No encuentro el catálogo {ruta}")
    cat = (spark.read.option("header", True).option("multiLine", True)
           .option("quote", '"').option("escape", '"').option("encoding", "UTF-8").csv(ruta))
    nombres = {c.lstrip("﻿").strip().upper(): c for c in cat.columns}
    faltan = [c for c in ("CODIGO", "LATITUD", "LONGITUD", "ALTITUD") if c not in nombres]
    if faltan:
        raise SystemExit(f"El catálogo {ruta} no tiene las columnas {faltan}. Tiene: {cat.columns}")
    col = lambda n: F.trim(F.col(f"`{nombres[n]}`"))
    cat = cat.select(
        col("CODIGO").cast("long").alias("CodigoEstacion"),
        F.regexp_replace(col("LATITUD"), ",", ".").cast("double").alias("Latitud"),
        F.regexp_replace(col("LONGITUD"), ",", ".").cast("double").alias("Longitud"),
        F.regexp_replace(col("ALTITUD"), r"\.", "").cast("double").alias("Altitud"),  # "1.492" -> 1492
    ).filter(F.col("CodigoEstacion").isNotNull())
    repetidos = cat.groupBy("CodigoEstacion").count().filter("count > 1").count()
    if repetidos:
        print(f"   ! El catálogo tiene {repetidos} códigos repetidos: se deja una fila por código")
        cat = cat.dropDuplicates(["CodigoEstacion"])
    return cat


# ------------------------------------------------------------------ Transformación
def transformar_ideam(spark: SparkSession, departamentos: list[str] | None = None,
                      catalogo: str = CATALOGO) -> tuple[DataFrame, DataFrame]:
    """Aplica las reglas 1 a 7. Devuelve (tabla limpia, conteos por departamento para el embudo)."""
    ideam = leer_mediciones(spark, carpetas_departamento(departamentos)).cache()
    conteos = embudo.contar(ideam, "leidas")

    ideam = ideam.filter(F.col("_es_rad"))                                      # regla 1: solo RAD SOLAR
    conteos = conteos.join(embudo.contar(ideam, "_r1"), "Departamento", "left")

    ejemplos = ideam.filter(F.col("Fecha").isNull()).select("_archivo").distinct().limit(5).collect()
    if ejemplos:                                                                # regla 3
        print("   ! Hay fechas que no se pudieron leer, por ejemplo en:")
        for r in ejemplos:
            print(f"       {r['_archivo']}")
    ideam = ideam.filter(F.col("Fecha").isNotNull())
    conteos = conteos.join(embudo.contar(ideam, "_r3"), "Departamento", "left")

    # Regla 4: hora repetida -> mejor aprobación; si empatan, la primera como en pandas
    # (pandas quita repetidas dentro de cada departamento, por eso Departamento va en la partición)
    orden = (Window.partitionBy("Departamento", "CodigoEstacion", "Fecha")
             .orderBy("EsPreliminar", "_archivo", "_orden"))
    ideam = (ideam.withColumn("_n", F.row_number().over(orden))
                  .withColumn("_p1", F.first("EsPreliminar").over(orden))
                  .withColumn("_v1", F.first("Valor").over(orden)))
    empates = ideam.filter((F.col("_n") == 2) & (F.col("EsPreliminar") == F.col("_p1"))
                           & ~F.col("Valor").eqNullSafe(F.col("_v1"))).count()
    if empates:
        print(f"   ! Regla 4: {empates:,} horas repetidas con la misma aprobación y valor distinto "
              "(se queda la primera del archivo, como pandas)")
    ideam = ideam.filter("_n = 1")
    conteos = conteos.join(embudo.contar(ideam, "_r4"), "Departamento", "left")

    ideam = ideam.filter(~F.coalesce(F.col("Valor") > schema.VALOR_MAXIMO, F.lit(False)))   # regla 6
    conteos = conteos.join(embudo.contar(ideam, "_r6"), "Departamento", "left")

    ideam = ideam.join(leer_catalogo(spark, catalogo), "CodigoEstacion", "left")             # regla 5
    return ideam.select(*COLUMNAS_SALIDA), conteos


def estaciones_sin_coordenadas(spark: SparkSession, ideam: DataFrame) -> DataFrame:
    """D11: estaciones que no tienen ubicación en el catálogo (sin ellas no hay dato de la NASA)."""
    return (ideam.filter(F.col("Latitud").isNull() | F.col("Longitud").isNull())
            .groupBy("CodigoEstacion").agg(F.first("NombreEstacion").alias("NombreEstacion"),
                                           F.first("Departamento").alias("Departamento"))
            .orderBy("CodigoEstacion"))


def main(spark: SparkSession | None = None, departamentos: list[str] | None = None) -> None:
    propia = spark is None
    spark = get_spark("Calcusol-Silver-IDEAM", existing=spark)
    try:
        print(f"Datos: {settings.data_root()}")
        ideam, conteos = transformar_ideam(spark, departamentos)
        (ideam.withColumn(schema.COLUMNA_PARTICION, F.year("Fecha"))
              .repartition(schema.COLUMNA_PARTICION)
              .sortWithinPartitions("Departamento", "CodigoEstacion", "Fecha")
              .write.mode("overwrite").partitionBy(schema.COLUMNA_PARTICION).parquet(SALIDA))

        escrito = spark.read.parquet(SALIDA)
        final = embudo.contar(escrito, "final")
        filas = [r.asDict() for r in conteos.join(final, "Departamento", "left").fillna(0).collect()]
        for r in filas:
            r.update(r1_otra_variable=r["leidas"] - r["_r1"], r3_fecha_ilegible=r["_r1"] - r["_r3"],
                     r4_hora_repetida=r["_r3"] - r["_r4"], r6_mayor_1400=r["_r4"] - r["_r6"])
        embudo.guardar_e_imprimir(
            spark, filas,
            [("leidas", "Leídas"), ("r1_otra_variable", "R1 otra var"), ("r3_fecha_ilegible", "R3 fecha"),
             ("r4_hora_repetida", "R4 repet."), ("r6_mayor_1400", "R6 >1400"), ("final", "Silver IDEAM")],
            archivos.unir(REPORTES, "embudo_ideam"), "Embudo silver IDEAM (D9)")

        sin = estaciones_sin_coordenadas(spark, escrito)
        sin.coalesce(1).write.mode("overwrite").option("header", True).csv(
            archivos.unir(REPORTES, "estaciones_sin_coordenadas"))
        codigos = [r["CodigoEstacion"] for r in sin.collect()]
        if codigos:
            print(f"   ! {len(codigos)} estaciones sin coordenadas (sin NASA): {codigos}")
        print(f"\nEstaciones: {escrito.select('CodigoEstacion').distinct().count()} | Guardado: {SALIDA}")
    finally:
        if propia:
            spark.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Silver IDEAM (reglas 1-7)")
    parser.add_argument("departamentos", nargs="*", help="solo estos departamentos (vacío = todos)")
    args, _ = parser.parse_known_args()
    main(departamentos=args.departamentos or None)
