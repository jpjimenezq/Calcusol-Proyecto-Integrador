"""
Entrada del job de Glue calcusol-validacion.

Corre: gold/validacion.py (contrato completo; si falla, el job falla)

Parámetros del job en Glue:
  --extra-py-files   s3://calcusol-datos-eafit/scripts/calcusol.zip   (el paquete src/calcusol comprimido)
  --data-root        s3://calcusol-datos-eafit                         (raíz con bronze/, silver/ y gold/)
"""

import os
import sys


def _parametro(nombre, por_defecto):
    return sys.argv[sys.argv.index(nombre) + 1] if nombre in sys.argv else por_defecto


# La raíz de datos se fija ANTES de importar el paquete: las rutas se calculan al importar.
os.environ["CALCUSOL_DATA_ROOT"] = _parametro("--data-root", "s3://calcusol-datos-eafit")

from calcusol.common.spark import get_spark  # noqa: E402
from calcusol.gold import validacion  # noqa: E402

spark = get_spark("calcusol-validacion")
validacion.main(spark)
