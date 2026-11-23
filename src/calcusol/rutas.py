"""Rutas de los archivos compartidos del proyecto (un solo lugar, para no repetirlas en cada módulo).

Funcionan igual en el repositorio y en la imagen de la API (donde la raíz es /app). Las rutas relativas
de config_r3.yaml también se leen desde RAIZ.
"""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]            # <raíz>/src/calcusol/rutas.py -> <raíz>

# Tabla de datos horarios IDEAM + NASA: un solo parquet. Se crea con
#   python -m calcusol.gold.consolidar --entrada data/gold/dataset --salida data/gold/dataset_modelo.parquet
PARQUET_MODELO = RAIZ / "data" / "gold" / "dataset_modelo.parquet"

# Modelo de R1 (LightGBM, 17 variables), formato nativo. La variable MODELO_RUTA lo reemplaza (la API la usa).
MODELO_TXT = Path(os.environ.get("MODELO_RUTA") or RAIZ / "models" / "radiacion" / "v1" / "modelo_lgbm.txt")

# Excel de evaluación de R2 que lee R3 (lo genera calcusol/validacion/evaluar_modelo.py)
EXCEL_EVALUACION = RAIZ / "src" / "calcusol" / "validacion" / "resultados" / "evaluacion_lightgbm.xlsx"
