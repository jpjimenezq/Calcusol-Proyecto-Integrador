"""
Auditoría — Estaciones gemelas: ¿son copias? (respaldo de la nota D19 y de la regla 12b)

Para las 8 estaciones que salen de la tabla final, compara hora por hora su Valor
con el de su gemela (la estación del mismo sitio que sí queda en gold).
Responde: ¿las dos estaciones miden lo mismo?

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.auditoria.gemelas                      -> las 8 parejas de la regla 12
  python -m calcusol.auditoria.gemelas 21205970:21206790    -> otra pareja (regla 12b)
"""

import sys
from collections import defaultdict

from pyspark.sql import Window, functions as F

from calcusol.common import archivos
from calcusol.common.spark import get_spark
from calcusol.silver.ideam import MEDICIONES, detectar_formato


# (estación que sale, gemela que se conserva) — salida de conteo_estaciones.py
PAREJAS = [
    (21115160, 21115180),  # Hacienda Manila
    (23120250, 23125160),  # Borbur
    (26225030, 26225060),  # Cotove
    (26255020, 26255030),  # Santa Isabel
    (27015070, 27015330),  # Aeropuerto Olaya Herrera
    (35030370, 35035100),  # Villavicencio ICA
    (44035030, 44035050),  # Macagual
    (52010020, 52015050),  # Balboa
]
FORMATOS_FECHA = ["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm", "dd/MM/yyyy HH:mm:ss", "dd/MM/yyyy HH:mm"]


def leer_estaciones(spark, codigos):
    """Lee de bronze solo las filas de las estaciones pedidas: código, fecha, valor, preliminar."""
    grupos = defaultdict(list)
    for carpeta in archivos.listar_carpetas(MEDICIONES):
        for archivo in archivos.listar(carpeta, ".csv"):
            formato = detectar_formato(archivo)
            if formato:
                grupos[formato].append(archivo)

    partes = []
    for (codificacion, separador), rutas in grupos.items():
        df = spark.read.csv(rutas, header=True, sep=separador, encoding=codificacion)
        df = df.toDF(*[c.lstrip("﻿").strip().strip('"') for c in df.columns])
        fecha = F.coalesce(*[F.to_timestamp(F.trim("Fecha"), f) for f in FORMATOS_FECHA])
        partes.append(df.select(
            F.col("CodigoEstacion").cast("long").alias("codigo"),
            fecha.alias("fecha"),
            F.regexp_replace("Valor", ",", ".").cast("double").alias("valor"),
            (F.lower(F.trim("NivelAprobacion")) == "preliminar").alias("preliminar"),
        ).filter(F.col("codigo").isin(codigos)))

    datos = partes[0]
    for p in partes[1:]:
        datos = datos.unionByName(p)

    # Igual que la regla 4 de silver: una fila por estación y hora, la de mejor aprobación
    orden = Window.partitionBy("codigo", "fecha").orderBy(F.col("preliminar").asc())
    return (datos.filter(F.col("fecha").isNotNull())
            .withColumn("_n", F.row_number().over(orden)).filter("_n = 1").drop("_n"))


def main():
    spark = get_spark("Calcusol-Auditoria-Gemelas")
    try:
        parejas_pedidas = PAREJAS
        if len(sys.argv) > 1:   # parejas escritas como vieja:gemela
            parejas_pedidas = [tuple(int(x) for x in a.split(":")) for a in sys.argv[1:]]
        codigos = [c for pareja in parejas_pedidas for c in pareja]
        datos = leer_estaciones(spark, codigos).cache()

        vieja = datos.select(F.col("codigo").alias("vieja"), "fecha",
                             F.col("valor").alias("v_vieja"),
                             F.col("preliminar").alias("vieja_preliminar"))
        gemela = datos.select(F.col("codigo").alias("gemela"), "fecha",
                              F.col("valor").alias("v_gemela"),
                              F.col("preliminar").alias("gemela_preliminar"))
        parejas = spark.createDataFrame(parejas_pedidas, ["vieja", "gemela"])

        cruce = (vieja.join(parejas, "vieja")
                 .join(gemela, ["gemela", "fecha"], "left")
                 .withColumn("dif", F.abs(F.col("v_vieja") - F.col("v_gemela"))))

        resumen = (cruce.groupBy("vieja", "gemela").agg(
            F.count(F.lit(1)).alias("horas"),
            F.sum((~F.col("vieja_preliminar")).cast("int")).alias("vieja_aprobada"),
            F.sum(F.col("v_gemela").isNotNull().cast("int")).alias("con_gemela"),
            F.sum((F.col("v_gemela").isNotNull() & ~F.col("gemela_preliminar")).cast("int"))
             .alias("gemela_aprobada"),
            F.sum((F.col("dif") < 0.01).cast("int")).alias("identicas"),
            F.sum((F.col("dif") <= 1).cast("int")).alias("hasta_1"),
            F.avg("dif").alias("dif_media"),
            F.corr("v_vieja", "v_gemela").alias("correlacion"),
        ).orderBy("vieja").collect())

        print(f"\n{'Sale':>9} {'Gemela':>9} {'Horas':>8} {'Aprob. propia':>14} {'Con gemela':>11} {'Gemela aprob.':>14} "
              f"{'Idénticas':>10} {'Dif<=1':>8} {'Dif media':>10} {'Correl.':>8}")
        tot = defaultdict(float)
        for r in resumen:
            h, cg = r["horas"], r["con_gemela"] or 0
            pct = lambda x: f"{(x or 0) / cg * 100:.1f}%" if cg else "-"
            print(f"{r['vieja']:>9} {r['gemela']:>9} {h:>8,} {(r['vieja_aprobada'] or 0) / h * 100:>13.1f}% "
                  f"{cg / h * 100:>10.1f}% "
                  f"{pct(r['gemela_aprobada']):>14} {pct(r['identicas']):>10} {pct(r['hasta_1']):>8} "
                  f"{(r['dif_media'] or 0):>10.2f} {(r['correlacion'] or 0):>8.3f}")
            for k in ("horas", "con_gemela", "gemela_aprobada", "identicas", "hasta_1"):
                tot[k] += r[k] or 0

        cg = tot["con_gemela"]
        if not cg:
            print("\nLas estaciones NO tienen ninguna hora en común: no son copias simultáneas "
                  "(una mide en un periodo y la otra en otro).")
            return
        print(f"\nTOTAL de {len(parejas_pedidas)} pareja(s)")
        print(f"   Horas de las estaciones que salen:              {int(tot['horas']):,}")
        print(f"   ...con dato de la gemela a esa misma hora:       {cg / tot['horas'] * 100:.1f} %")
        print(f"   ...y ese dato de la gemela está aprobado:        {tot['gemela_aprobada'] / cg * 100:.1f} %")
        print(f"   Valor idéntico (diferencia < 0,01 Wh/m²):        {tot['identicas'] / cg * 100:.1f} %")
        print(f"   Diferencia de 1 Wh/m² o menos:                   {tot['hasta_1'] / cg * 100:.1f} %")
        print("\n(Los porcentajes de valor se calculan sobre las horas en que ambas estaciones tienen dato.)")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
