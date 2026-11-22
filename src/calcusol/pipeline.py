"""
Pipeline completo de limpieza (R5): silver -> gold -> validación, con una sola sesión de Spark.

Etapas, en orden:
  silver-ideam   silver/ideam.py          reglas 1-7
  silver-nasa    silver/nasa.py           reglas 8-10
  integracion    gold/integracion.py      reglas 11-13 y 12b, Sitio
  features       gold/features.py         G1-G3, D1, contrato
  validacion     gold/validacion.py       contrato completo, resumen y embudo total

Los jobs de Glue agrupan estas etapas así:
  calcusol-silver      -> silver-ideam + silver-nasa
  calcusol-gold        -> integracion + features
  calcusol-validacion  -> validacion

EJECUCIÓN (desde la raíz del repositorio):
  python -m calcusol.pipeline                         -> todo
  python -m calcusol.pipeline --desde integracion     -> desde esa etapa
  python -m calcusol.pipeline Amazonas Caldas         -> solo esos departamentos (prueba rápida)
"""

from __future__ import annotations

import argparse
import time

from calcusol.common.spark import get_spark
from calcusol.config import settings
from calcusol.gold import features, integracion, validacion
from calcusol.silver import ideam, nasa

ETAPAS = ["silver-ideam", "silver-nasa", "integracion", "features", "validacion"]


def correr(etapas: list[str], departamentos: list[str] | None = None) -> None:
    spark = get_spark("Calcusol-Pipeline")
    tiempos = []
    try:
        print(f"Datos: {settings.data_root()}")
        for etapa in etapas:
            print(f"\n================ {etapa} ================")
            inicio = time.time()
            if etapa == "silver-ideam":
                ideam.main(spark, departamentos)
            elif etapa == "silver-nasa":
                nasa.main(spark)
            elif etapa == "integracion":
                integracion.main(spark)
            elif etapa == "features":
                features.main(spark)
            elif etapa == "validacion":
                validacion.main(spark)
            tiempos.append((etapa, time.time() - inicio))
        print("\n--- Tiempos ---")
        for etapa, segundos in tiempos:
            print(f"   {etapa:<14}{segundos / 60:>7.1f} min")
        print(f"   {'TOTAL':<14}{sum(s for _, s in tiempos) / 60:>7.1f} min")
    finally:
        spark.stop()


def main() -> None:
    p = argparse.ArgumentParser(description="Pipeline de limpieza Calcusol (silver -> gold -> validación)")
    p.add_argument("departamentos", nargs="*", help="solo estos departamentos (vacío = todos)")
    p.add_argument("--desde", choices=ETAPAS, default=ETAPAS[0])
    p.add_argument("--hasta", choices=ETAPAS, default=ETAPAS[-1])
    args, _ = p.parse_known_args()
    etapas = ETAPAS[ETAPAS.index(args.desde):ETAPAS.index(args.hasta) + 1]
    correr(etapas, args.departamentos or None)


if __name__ == "__main__":
    main()
