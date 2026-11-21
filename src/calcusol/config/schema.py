"""
Contrato de columnas del dataset Gold (17 columnas).

Fuente única de verdad del esquema que consumen train/test. Si una tabla no
cumple este contrato, falla pronto (en Gold) y no se arrastra al modelo.
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import types as T

# Identificación y trazabilidad
IDENTIFICACION = ["CodigoEstacion", "NombreEstacion", "Departamento", "Fecha", "EsPreliminar"]
# Predictoras X — NASA
X_NASA = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]
# Predictoras X — lugar
X_LUGAR = ["Latitud", "Longitud", "Altitud"]
# Predictoras X — tiempo
X_TIEMPO = ["Hora", "Mes", "DiaDelAnio"]
# Agrupador espacial para GroupKFold
AGRUPADOR = ["Sitio"]
# Objetivo Y
OBJETIVO = "Valor"

COLUMNAS_GOLD = (
    IDENTIFICACION + X_NASA + X_LUGAR + X_TIEMPO + AGRUPADOR + [OBJETIVO]
)

# Tipos esperados por columna (nombre -> tipo Spark)
TIPOS_GOLD: dict[str, T.DataType] = {
    "CodigoEstacion": T.LongType(),
    "NombreEstacion": T.StringType(),
    "Departamento": T.StringType(),
    "Fecha": T.TimestampType(),
    "EsPreliminar": T.BooleanType(),
    "ALLSKY_SFC_SW_DWN": T.FloatType(),
    "CLRSKY_SFC_SW_DWN": T.FloatType(),
    "ALLSKY_KT": T.FloatType(),
    "T2M": T.FloatType(),
    "Latitud": T.DoubleType(),
    "Longitud": T.DoubleType(),
    "Altitud": T.DoubleType(),
    "Hora": T.IntegerType(),
    "Mes": T.IntegerType(),
    "DiaDelAnio": T.IntegerType(),
    "Sitio": T.IntegerType(),
    "Valor": T.FloatType(),
}


def validar_gold(df: DataFrame) -> None:
    """
    Valida que un DataFrame cumpla el contrato Gold. Lanza ValueError si no.
    Comprueba columnas presentes; el chequeo de tipos es informativo.
    """
    faltantes = [c for c in COLUMNAS_GOLD if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas Gold: {faltantes}")

    sobrantes = [c for c in df.columns if c not in COLUMNAS_GOLD]
    if sobrantes:
        raise ValueError(f"Columnas no previstas en Gold: {sobrantes}")
