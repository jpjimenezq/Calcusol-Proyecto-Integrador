"""
Auditoría — Paridad: compara fila por fila dos versiones de gold (o de la tabla integrada).

Sirve para demostrar que una versión nueva da lo mismo que una de referencia
(por ejemplo, esta versión por carpetas contra el gold v1 anterior, o Spark contra pandas).
Llave: CodigoEstacion + Fecha. Compara todas las columnas que tengan las dos.

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.auditoria.paridad --referencia <ruta del gold de referencia>
  python -m calcusol.auditoria.paridad --nuevo <ruta> --referencia <ruta> --excluir 21205970
"""

from __future__ import annotations

import argparse
import sys

from pyspark.sql import functions as F

from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

LLAVE = ["CodigoEstacion", "Fecha"]


def leer_parquet(spark, ruta):
    """Lee un parquet de Spark o de pandas. Según la versión de pandas, la fecha viene en
    nanosegundos (Spark 3.5 no la entiende): se lee como número y se convierte sin mover la hora."""
    spark.conf.set("spark.sql.legacy.parquet.nanosAsLong", "true")
    df = spark.read.parquet(ruta)
    if dict(df.dtypes)["Fecha"] in ("bigint", "long"):
        df = df.withColumn("Fecha", F.expr("timestamp_micros(Fecha div 1000)"))
    else:
        df = df.withColumn("Fecha", F.col("Fecha").cast("timestamp"))
    return df.withColumn("CodigoEstacion", F.col("CodigoEstacion").cast("long"))


def grupos_sitio(df):
    """Estaciones que comparten Sitio (se comparan los grupos, no el número del Sitio)."""
    if "Sitio" not in df.columns:
        return None
    filas = (df.groupBy("Sitio").agg(F.collect_set("CodigoEstacion").alias("c"))
             .filter(F.size("c") > 1).collect())
    return sorted(sorted(r["c"]) for r in filas)


def main() -> None:
    p = argparse.ArgumentParser(description="Paridad fila por fila entre dos versiones de gold")
    p.add_argument("--nuevo", default=settings.gold_path("dataset"))
    p.add_argument("--referencia", required=True)
    p.add_argument("--excluir", type=int, nargs="*", default=[],
                   help="códigos que la versión nueva quita a propósito (se sacan de la referencia)")
    args, _ = p.parse_known_args()

    spark = get_spark("Calcusol-Paridad")
    try:
        nuevo, ref = leer_parquet(spark, args.nuevo), leer_parquet(spark, args.referencia)
        if args.excluir:
            print(f"Se excluyen de la referencia: {args.excluir}")
            ref = ref.filter(~F.col("CodigoEstacion").isin(args.excluir))
        comunes = [c for c in schema.COLUMNAS_GOLD if c in nuevo.columns and c in ref.columns and c not in LLAVE]
        cruce = (ref.select(*LLAVE, *[F.col(c).alias(f"{c}_r") for c in comunes], F.lit(True).alias("_en_r"))
                 .join(nuevo.select(*LLAVE, *[F.col(c).alias(f"{c}_n") for c in comunes],
                                    F.lit(True).alias("_en_n")), LLAVE, "full_outer"))
        en_r, en_n = F.col("_en_r").isNotNull(), F.col("_en_n").isNotNull()
        ambos = en_r & en_n
        r = cruce.agg(
            F.sum(en_r.cast("int")).alias("filas_ref"), F.sum(en_n.cast("int")).alias("filas_nuevo"),
            F.sum((en_r & ~en_n).cast("int")).alias("solo_ref"),
            F.sum((en_n & ~en_r).cast("int")).alias("solo_nuevo"),
            *[F.sum((ambos & ~F.col(f"{c}_r").eqNullSafe(F.col(f"{c}_n"))).cast("int")).alias(c) for c in comunes],
        ).first()

        print(f"\n{'':<34}{'referencia':>14}{'nuevo':>14}")
        print(f"{'Filas':<34}{r['filas_ref'] or 0:>14,}{r['filas_nuevo'] or 0:>14,}")
        print(f"{'Estaciones':<34}{ref.select('CodigoEstacion').distinct().count():>14}"
              f"{nuevo.select('CodigoEstacion').distinct().count():>14}")
        print(f"\nFilas solo en la referencia: {r['solo_ref'] or 0:,}")
        print(f"Filas solo en la nueva:      {r['solo_nuevo'] or 0:,}")
        print("Filas con valor distinto, por columna (solo filas que están en las dos):")
        difs = {c: r[c] or 0 for c in comunes}
        for c in comunes:
            print(f"   {c:<22}{difs[c]:>10,}")

        g_ref, g_nuevo = grupos_sitio(ref), grupos_sitio(nuevo)
        sitio_ok = g_ref is None or g_ref == g_nuevo
        if g_ref is not None:
            print(f"\nEstaciones que comparten Sitio — referencia: {g_ref} | nuevo: {g_nuevo} "
                  f"-> {'iguales' if sitio_ok else 'DISTINTOS'}")

        igual = not (r["solo_ref"] or r["solo_nuevo"] or sum(difs.values())) and sitio_ok
        print("\nRESULTADO:", "PARIDAD OK — las dos versiones dan lo mismo" if igual
              else "HAY DIFERENCIAS — revisar arriba")
        if not igual:
            sys.exit(1)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
