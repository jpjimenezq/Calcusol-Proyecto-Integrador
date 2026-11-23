"""
Gold — Consolidar el dataset particionado en un único Parquet.

El dataset Gold está particionado por año (`data/gold/dataset/Anio=.../`). Para
entrenar en Colab conviene un solo archivo fácil de subir a Drive.

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.gold.consolidar

Alternativas:
  python -m calcusol.gold.consolidar --entrada data/gold/dataset --overwrite
  python -m calcusol.gold.consolidar --entrada /ruta/gold --salida /ruta/gold.parquet
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil


def resolver_ruta(valor: str, defecto: str) -> str:
    ruta = Path(valor).expanduser() if valor else Path(defecto)
    if "://" in str(ruta):
        raise ValueError("La consolidación local requiere rutas locales, no s3://.")
    return str(ruta.resolve())


def sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def limpiar_temporal(ruta: Path) -> None:
    if ruta.exists():
        shutil.rmtree(ruta)


def main() -> None:
    # settings no depende de PySpark y permite calcular los valores por defecto.
    from calcusol.config import settings
    entrada_defecto = settings.gold_path("dataset")
    salida_defecto = settings.gold_path("dataset_modelo.parquet")

    parser = argparse.ArgumentParser(
        description="Consolida las particiones Gold actuales en un único Parquet.",
    )
    parser.add_argument("--entrada", default=entrada_defecto)
    parser.add_argument("--salida", default=salida_defecto)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    # Las dependencias Spark se cargan después del help para que --help funcione
    # incluso en un entorno local sin PySpark.
    from calcusol.common.spark import get_spark
    from calcusol.config import schema

    entrada = Path(resolver_ruta(args.entrada, entrada_defecto))
    salida = Path(resolver_ruta(args.salida, salida_defecto))

    if not entrada.exists():
        raise FileNotFoundError(
            f"No existe el Gold particionado: {entrada}. "
            "Descárgalo de S3 o ejecuta primero el pipeline Gold."
        )
    if salida.exists() and not args.overwrite:
        raise FileExistsError(
            f"Ya existe {salida}. Usa --overwrite para reemplazarlo."
        )

    salida.parent.mkdir(parents=True, exist_ok=True)

    spark = get_spark("Calcusol-Gold-Consolidar")
    spark.sparkContext.setLogLevel("WARN")
    temporal = salida.parent / f"_tmp_{salida.stem}"
    limpiar_temporal(temporal)

    try:
        df = spark.read.parquet(str(entrada)).select(*schema.COLUMNAS_GOLD)
        schema.validar_gold(df)
        total = df.count()

        # coalesce(1) produce un solo archivo Parquet dentro de un directorio temporal.
        df.coalesce(1).write.mode("overwrite").parquet(str(temporal))

        archivos = list(temporal.rglob("*.parquet"))
        if len(archivos) != 1:
            raise RuntimeError(
                f"Se esperaba 1 archivo Parquet en el temporal, se encontraron {len(archivos)}."
            )

        if salida.exists():
            salida.unlink()
        archivos[0].replace(salida)

        mb = salida.stat().st_size / (1024 * 1024)
        hash_archivo = sha256(salida)
        print(
            f"Consolidado: {total:,} filas, "
            f"{len(schema.COLUMNAS_GOLD)} columnas -> {salida} ({mb:.1f} MB)"
        )
        print(f"SHA-256: {hash_archivo}")
        print("Súbelo a tu Drive para entrenar en Colab.")
    finally:
        spark.stop()
        limpiar_temporal(temporal)


if __name__ == "__main__":
    main()
