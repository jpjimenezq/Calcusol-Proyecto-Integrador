"""
Auditoría — ¿Cuántas estaciones llegan a gold y por qué se pierden las demás? (nota D19)

1. Cuenta las estaciones en bronze (por departamento) y en gold.
2. Para cada estación de bronze que no llega a la tabla integrada: filas, % preliminares y
   estación más cercana según el catálogo (si está a < 1 km, la quitó la regla 12 o la 12b).
3. Para cada estación de la tabla integrada que no llega a gold: su periodo (la quitó D1 o G1).

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.auditoria.estaciones
"""

from __future__ import annotations

import math
from collections import defaultdict

from pyspark.sql import functions as F

from calcusol.common import archivos
from calcusol.common.spark import get_spark
from calcusol.config import settings
from calcusol.silver.ideam import (MEDICIONES, detectar_formato, leer_catalogo, nombre_departamento)

INTEGRADO = settings.gold_path("integrado")
DATASET = settings.gold_path("dataset")


def distancia_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def estaciones_bronze(spark):
    """{departamento: {codigo: (nombre, filas, preliminares)}} leyendo los CSV crudos."""
    por_depto = {}
    for carpeta in archivos.listar_carpetas(MEDICIONES):
        grupos = defaultdict(list)
        for a in archivos.listar(carpeta, ".csv"):
            formato = detectar_formato(a)
            if formato:
                grupos[formato].append(a)
        est = {}
        for (cod, sep), rutas in grupos.items():
            df = spark.read.csv(rutas, header=True, sep=sep, encoding=cod)
            df = df.toDF(*[c.lstrip("﻿").strip().strip('"') for c in df.columns])
            prelim = F.lower(F.trim("NivelAprobacion")) == "preliminar"
            for r in (df.groupBy(F.col("CodigoEstacion").cast("long").alias("c"))
                      .agg(F.first("NombreEstacion").alias("n"), F.count(F.lit(1)).alias("f"),
                           F.sum(F.when(prelim, 1).otherwise(0)).alias("p")).collect()):
                nombre, f0, p0 = est.get(r["c"], (r["n"], 0, 0))
                est[r["c"]] = (nombre, f0 + r["f"], p0 + r["p"])
        por_depto[nombre_departamento(archivos.nombre(carpeta))] = est
    return por_depto


def main() -> None:
    spark = get_spark("Calcusol-Auditoria-Estaciones")
    try:
        por_depto = estaciones_bronze(spark)
        info = {c: v for est in por_depto.values() for c, v in est.items()}
        suma = sum(len(e) for e in por_depto.values())
        integrado = {r[0] for r in spark.read.parquet(INTEGRADO).select("CodigoEstacion").distinct().collect()}
        gold = {r[0] for r in spark.read.parquet(DATASET).select("CodigoEstacion").distinct().collect()}
        catalogo = {r["CodigoEstacion"]: (r["Latitud"], r["Longitud"]) for r in leer_catalogo(spark).collect()
                    if r["Latitud"] is not None and r["Longitud"] is not None}

        print(f"\nBronze (suma por departamento): {suma} | códigos distintos: {len(info)}")
        print(f"Tabla integrada: {len(integrado)} | Gold: {len(gold)}")

        perdidas = sorted(set(info) - integrado)
        print(f"\nEstaciones de bronze que no llegan a la tabla integrada ({len(perdidas)}):")
        for c in perdidas:
            nombre, filas, prelim = info[c]
            linea = f"   {c}  {nombre or '':<40} filas {filas:>8,} | preliminares {prelim / filas * 100:5.1f} %"
            if c in catalogo:
                d, otra = min((distancia_km(*catalogo[c], *catalogo[o]), o) for o in info if o != c and o in catalogo)
                linea += f" | más cercana {otra} a {d:.2f} km" + (" (mismo Sitio)" if d <= 1 else "")
            else:
                linea += " | sin coordenadas en el catálogo"
            print(linea)

        sin_gold = sorted(integrado - gold)
        if sin_gold:
            print(f"\nEstaciones de la tabla integrada que no llegan a gold ({len(sin_gold)}):")
            for r in (spark.read.parquet(INTEGRADO).filter(F.col("CodigoEstacion").isin(sin_gold))
                      .groupBy("CodigoEstacion", "NombreEstacion", "Departamento")
                      .agg(F.date_format(F.min("Fecha"), "yyyy-MM-dd").alias("desde"),
                           F.date_format(F.max("Fecha"), "yyyy-MM-dd").alias("hasta"))
                      .orderBy("CodigoEstacion").collect()):
                print(f"   {r['CodigoEstacion']}  {r['NombreEstacion'] or '':<40} {r['Departamento']:<14}"
                      f" datos {r['desde']} a {r['hasta']}")
        print(f"\nConclusión: {suma} descargadas -> {len(integrado)} en la tabla integrada -> {len(gold)} en gold")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
