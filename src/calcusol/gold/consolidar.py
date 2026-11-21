"""
Gold — Consolidar el dataset particionado en un único Parquet.

El dataset Gold está particionado por año (data/gold/dataset/_anio=.../). Para
entrenar en Colab conviene un solo archivo fácil de subir a Drive. Este script
une las particiones en data/gold/dataset_modelo.parquet con las 17 columnas del
contrato (sin la columna técnica _anio de partición).

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.gold.consolidar
"""

from __future__ import annotations

from pathlib import Path

from calcusol.common.spark import get_spark
from calcusol.config import schema, settings

ENTRADA = settings.gold_path("dataset")
SALIDA = settings.gold_path("dataset_modelo.parquet")


def main() -> None:
    spark = get_spark("Calcusol-Gold-Consolidar")
    spark.sparkContext.setLogLevel("WARN")
    try:
        df = spark.read.parquet(ENTRADA).select(*schema.COLUMNAS_GOLD)
        schema.validar_gold(df)
        total = df.count()

        # coalesce(1) -> un solo archivo de salida. Spark escribe un directorio;
        # luego se renombra el part-*.parquet al nombre final plano.
        tmp = settings.gold_path("_tmp_consolidado")
        df.coalesce(1).write.mode("overwrite").parquet(tmp)

        part = next(Path(tmp).glob("*.parquet"))
        destino = Path(SALIDA)
        if destino.exists():
            destino.unlink()
        part.rename(destino)
        # limpiar el directorio temporal
        for p in Path(tmp).iterdir():
            p.unlink()
        Path(tmp).rmdir()

        mb = destino.stat().st_size / (1024 * 1024)
        print(f"Consolidado: {total:,} filas, 17 columnas -> {destino} ({mb:.1f} MB)")
        print("Súbelo a tu Drive para entrenar en Colab.")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
