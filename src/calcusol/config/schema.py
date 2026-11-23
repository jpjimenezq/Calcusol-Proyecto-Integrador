"""
Contrato de columnas del dataset Gold (17 columnas).

Fuente única de verdad del esquema que consumen train/test. Si una tabla no
cumple este contrato, falla pronto (en Gold) y no se arrastra al modelo.

QUÉ REVISA validar_gold
  C1. Columnas: están las 17, ni una más (se ignora la columna de partición "Anio").
  C2. Tipos: cada columna tiene el tipo acordado.
  C3. Vacíos: ninguna columna tiene vacíos.
  C4. Reglas por fila (rangos físicos, años 2005-2022, variables de tiempo, departamento).
  C5. Una sola fila por estación y hora.
  C6. Cada estación tiene un solo Sitio y una sola ubicación.
  C7. Si existe "Anio" (partición), coincide con el año de Fecha.
validar_columnas revisa solo C1 y C2 (lo que se le exige a la tabla integrada, que aún
tiene noches, vacíos y años antes de 2005).

Prueba del contrato:  python -m calcusol.auditoria.prueba_contrato
"""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
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
    "Fecha": T.TimestampType(),          # hora de Colombia, marca el FINAL de la hora
    "EsPreliminar": T.BooleanType(),
    "ALLSKY_SFC_SW_DWN": T.FloatType(),  # Wh/m²
    "CLRSKY_SFC_SW_DWN": T.FloatType(),  # Wh/m²
    "ALLSKY_KT": T.FloatType(),          # 0 a 1
    "T2M": T.FloatType(),                # °C
    "Latitud": T.DoubleType(),
    "Longitud": T.DoubleType(),
    "Altitud": T.DoubleType(),           # m
    "Hora": T.IntegerType(),
    "Mes": T.IntegerType(),
    "DiaDelAnio": T.IntegerType(),
    "Sitio": T.IntegerType(),
    "Valor": T.FloatType(),              # radiación medida por el IDEAM, Wh/m²
}
ESQUEMA_GOLD = T.StructType([T.StructField(c, TIPOS_GOLD[c]) for c in COLUMNAS_GOLD])

COLUMNA_PARTICION = "Anio"    # D17: un archivo por año
ANIO_MINIMO = 2005            # D1
ANIO_MAXIMO = 2022
VALOR_MAXIMO = 1400.0         # Wh/m² en una hora (D3)

OFICIALES = [
    "Amazonas", "Antioquia", "Arauca", "Atlántico", "Bogotá", "Bolívar", "Boyacá",
    "Caldas", "Caquetá", "Casanare", "Cauca", "Cesar", "Chocó", "Córdoba",
    "Cundinamarca", "Guainía", "Guaviare", "Huila", "La Guajira", "Magdalena",
    "Meta", "Nariño", "Norte de Santander", "Putumayo", "Quindío", "Risaralda",
    "San Andrés", "Santander", "Sucre", "Tolima", "Valle del Cauca", "Vaupés", "Vichada",
]


def reglas_por_fila() -> dict:
    """C4: condición que debe cumplir CADA fila. Nombre legible -> expresión de Spark."""
    c = F.col
    return {
        "Valor entre 0 y 1400 (D3)": c("Valor").between(0, VALOR_MAXIMO),
        "Solo horas de sol: CLRSKY > 0": c("CLRSKY_SFC_SW_DWN") > 0,
        "ALLSKY no negativo": c("ALLSKY_SFC_SW_DWN") >= 0,
        "ALLSKY_KT entre 0 y 1": c("ALLSKY_KT").between(0, 1),
        "T2M entre -20 y 50 °C": c("T2M").between(-20, 50),
        "Latitud dentro de Colombia (-5 a 14)": c("Latitud").between(-5, 14),
        "Longitud dentro de Colombia (-82 a -66)": c("Longitud").between(-82, -66),
        "Altitud entre -50 y 6000 m": c("Altitud").between(-50, 6000),
        f"Año de Fecha entre {ANIO_MINIMO} y {ANIO_MAXIMO} (D1)":
            F.year("Fecha").between(ANIO_MINIMO, ANIO_MAXIMO),
        "Hora coincide con Fecha": c("Hora") == F.hour("Fecha"),
        "Mes coincide con Fecha": c("Mes") == F.month("Fecha"),
        "DiaDelAnio coincide con Fecha": c("DiaDelAnio") == F.dayofyear("Fecha"),
        "Departamento con nombre oficial": c("Departamento").isin(OFICIALES),
        "Sitio no negativo": c("Sitio") >= 0,
    }


class ContratoIncumplido(ValueError):
    def __init__(self, nombre: str, problemas: list[str]):
        self.problemas = problemas
        detalle = "\n".join(f"   - {p}" for p in problemas)
        super().__init__(f"[{nombre}] no cumple el contrato de gold:\n{detalle}")


def _tipo_aceptado(nombre: str, real: T.DataType, esperado: T.DataType) -> bool:
    # Fecha: se acepta timestamp con o sin zona (los parquet hechos con pandas se leen sin zona)
    if nombre == "Fecha":
        return isinstance(real, (T.TimestampType, T.TimestampNTZType))
    return real == esperado


def validar_columnas(df: DataFrame, nombre: str = "tabla") -> DataFrame:
    """C1 y C2. Devuelve la tabla con las 17 columnas en orden (más Anio si existe)."""
    problemas = []
    columnas = [c for c in df.columns if c != COLUMNA_PARTICION]
    faltan = [c for c in COLUMNAS_GOLD if c not in columnas]
    sobran = [c for c in columnas if c not in COLUMNAS_GOLD]
    if faltan:
        problemas.append(f"C1 faltan columnas: {faltan}")
    if sobran:
        problemas.append(f"C1 sobran columnas: {sobran}")
    if not problemas:
        tipos = {f.name: f.dataType for f in df.schema.fields}
        for col in COLUMNAS_GOLD:
            if not _tipo_aceptado(col, tipos[col], TIPOS_GOLD[col]):
                problemas.append(f"C2 tipo de {col}: es {tipos[col].simpleString()}, "
                                 f"debe ser {TIPOS_GOLD[col].simpleString()}")
    if problemas:
        raise ContratoIncumplido(nombre, problemas)
    extra = [COLUMNA_PARTICION] if COLUMNA_PARTICION in df.columns else []
    return df.select(*COLUMNAS_GOLD, *extra)


def validar_gold(df: DataFrame, nombre: str = "gold", silencioso: bool = False) -> DataFrame:
    """Revisa C1 a C7. Lanza ContratoIncumplido (un ValueError) si algo falla.
    Si todo cumple, devuelve la tabla con las 17 columnas en orden (más Anio si existe)."""
    df = validar_columnas(df, nombre)
    problemas = []

    # C3, C4 y C7 en una sola pasada por los datos
    reglas = reglas_por_fila()
    expresiones = [F.count(F.lit(1)).alias("_filas")]
    expresiones += [F.sum(F.col(c).isNull().cast("int")).alias(f"_nulos_{i}")
                    for i, c in enumerate(COLUMNAS_GOLD)]
    # when(...).otherwise(1): si la condición es falsa o vacía, la fila cuenta como falla
    expresiones += [F.sum(F.when(cond, 0).otherwise(1)).alias(f"_regla_{i}")
                    for i, cond in enumerate(reglas.values())]
    hay_particion = COLUMNA_PARTICION in df.columns
    if hay_particion:
        expresiones.append(F.sum(F.when(F.col(COLUMNA_PARTICION) == F.year("Fecha"), 0)
                                 .otherwise(1)).alias("_particion"))
    r = df.agg(*expresiones).first()

    filas = r["_filas"]
    if filas == 0:
        raise ContratoIncumplido(nombre, ["La tabla está vacía"])
    for i, c in enumerate(COLUMNAS_GOLD):
        if r[f"_nulos_{i}"]:
            problemas.append(f"C3 {c} tiene {r[f'_nulos_{i}']:,} vacíos")
    for i, texto in enumerate(reglas):
        if r[f"_regla_{i}"]:
            problemas.append(f"C4 '{texto}': falla en {r[f'_regla_{i}']:,} filas")
    if hay_particion and r["_particion"]:
        problemas.append(f"C7 '{COLUMNA_PARTICION}' no coincide con el año de Fecha "
                         f"en {r['_particion']:,} filas")

    # C5: una fila por estación y hora
    repetidas = (df.groupBy("CodigoEstacion", "Fecha").count()
                 .filter(F.col("count") > 1).count())
    if repetidas:
        problemas.append(f"C5 hay {repetidas:,} combinaciones estación-hora repetidas")

    # C6: cada estación con un solo Sitio y una sola ubicación
    por_estacion = df.groupBy("CodigoEstacion").agg(
        F.countDistinct("Sitio").alias("sitios"),
        F.countDistinct("Latitud", "Longitud", "Altitud").alias("ubicaciones"),
    )
    raras = por_estacion.filter((F.col("sitios") > 1) | (F.col("ubicaciones") > 1)).count()
    if raras:
        problemas.append(f"C6 {raras} estaciones con más de un Sitio o más de una ubicación")

    if problemas:
        raise ContratoIncumplido(nombre, problemas)
    if not silencioso:
        print(f"[{nombre}] cumple el contrato: {filas:,} filas, {por_estacion.count()} estaciones, 17 columnas")
    return df
