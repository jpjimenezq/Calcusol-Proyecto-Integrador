"""
Gold — Particiones y validación sin fuga.

Expone cómo leer el dataset Gold para ML, sin modificar el Parquet (opción B):
el criterio de corte (año) vive solo aquí, en un único lugar.

Particiones (decisión del plan D0):
  - train: Fecha con año <= 2019
  - test:  Fecha con año >= 2020   (hold-out, intacto hasta la evaluación final)

Esquemas de validación (ambos exigidos por la rúbrica SI7009):
  - Espacial:  GroupKFold agrupando por Sitio -> generaliza a estaciones no vistas.
  - Temporal:  TimeSeriesSplit por año        -> generaliza a años futuros.

Estas funciones trabajan sobre pandas (el modelado con sklearn/xgboost es local,
fuera de Spark). El dataset Gold cabe en memoria (~3.8M filas).

Las features cíclicas NO se materializan aquí: se derivan en ml/ para no tocar
el contrato de 17 columnas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calcusol.config import settings

DATASET = settings.gold_path("dataset")
ANIO_CORTE_TEST = 2020   # test: >= 2020 ; train: <= 2019


# ================================================================== Carga
def cargar(columnas: list[str] | None = None) -> pd.DataFrame:
    """
    Lee el dataset Gold a pandas y garantiza la columna 'Anio'.

    El dataset particionado trae 'anio' (de la partición). Si se piden columnas
    concretas, se asegura incluir 'Fecha' para poder derivar el año. Se expone
    siempre como 'Anio' (mayúscula) para uniformidad.
    """
    if columnas is not None and "Fecha" not in columnas:
        columnas = [*columnas, "Fecha"]
    df = pd.read_parquet(DATASET, columns=columnas)
    if "Anio" not in df.columns:
        if "anio" in df.columns:
            df = df.rename(columns={"anio": "Anio"})
        else:
            df["Anio"] = pd.to_datetime(df["Fecha"]).dt.year
    # pyarrow lee la columna de partición como categórica; forzar entero para
    # poder comparar con el año de corte.
    df["Anio"] = df["Anio"].astype("int32")
    return df


def cargar_train(columnas: list[str] | None = None) -> pd.DataFrame:
    df = cargar(columnas)
    return df[df["Anio"] <= ANIO_CORTE_TEST - 1].reset_index(drop=True)


def cargar_test(columnas: list[str] | None = None) -> pd.DataFrame:
    """Hold-out. No usar para seleccionar modelos ni hiperparámetros."""
    df = cargar(columnas)
    return df[df["Anio"] >= ANIO_CORTE_TEST].reset_index(drop=True)


# ================================================================== Validación
def folds_espaciales(df_train: pd.DataFrame, n_splits: int = 5):
    """
    GroupKFold por Sitio: cada fold deja fuera sitios completos, de modo que el
    modelo se valida en estaciones que no vio. Evita fuga espacial.
    Devuelve un generador de (idx_train, idx_val) sobre las filas de df_train.
    """
    from sklearn.model_selection import GroupKFold

    gkf = GroupKFold(n_splits=n_splits)
    return gkf.split(df_train, groups=df_train["Sitio"].to_numpy())


def folds_temporales(df_train: pd.DataFrame, n_splits: int = 5):
    """
    Validación temporal por año: entrena con años pasados, valida con el año
    siguiente retenido. Evita fuga temporal.

    Se implementa de forma explícita por AÑO (no TimeSeriesSplit sobre filas,
    que mezclaría años si el df no está ordenado). Para k splits se usan los
    últimos k años del train como validaciones sucesivas.
    Devuelve lista de (idx_train, idx_val, anio_val).
    """
    anios = sorted(df_train["Anio"].unique())
    if len(anios) <= n_splits:
        cortes = anios[1:]            # cada año (salvo el primero) es una validación
    else:
        cortes = anios[-n_splits:]    # últimos n_splits años

    idx = np.arange(len(df_train))
    anio_col = df_train["Anio"].to_numpy()
    splits = []
    for anio_val in cortes:
        tr = idx[anio_col < anio_val]
        va = idx[anio_col == anio_val]
        if len(tr) and len(va):
            splits.append((tr, va, int(anio_val)))
    return splits


def resumen() -> None:
    """Imprime tamaños de train/test y nº de sitios/años, para verificación."""
    tr = cargar_train()
    te = cargar_test()
    print(f"Train (<= {ANIO_CORTE_TEST - 1}): {len(tr):,} filas | "
          f"sitios: {tr['Sitio'].nunique()} | años: {sorted(tr['Anio'].unique())}")
    print(f"Test  (>= {ANIO_CORTE_TEST}): {len(te):,} filas | "
          f"sitios: {te['Sitio'].nunique()} | años: {sorted(te['Anio'].unique())}")
    # Comprobación anti-fuga: ningún año compartido
    solape = set(tr["Anio"].unique()) & set(te["Anio"].unique())
    print(f"Años compartidos train/test (debe ser vacío): {solape or '∅'}")


if __name__ == "__main__":
    resumen()
