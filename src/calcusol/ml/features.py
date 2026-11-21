"""
ML — Construcción de features para el modelo de corrección de radiación.

Deriva las features cíclicas (Hora/Mes/DiaDelAnio -> sin/cos) SIN tocar el
dataset Gold de 17 columnas. Centraliza qué columnas son X, para que train,
evaluate y la API usen exactamente las mismas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Predictoras directas del Gold
X_DIRECTAS = [
    "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M",
    "Latitud", "Longitud", "Altitud",
]
# Features cíclicas derivadas (orden fijo)
X_CICLICAS = ["Hora_sin", "Hora_cos", "Mes_sin", "Mes_cos", "Dia_sin", "Dia_cos"]

FEATURES = X_DIRECTAS + X_CICLICAS
OBJETIVO = "Valor"


def agregar_ciclicas(df: pd.DataFrame) -> pd.DataFrame:
    """Añade las columnas cíclicas. No modifica el original (devuelve copia)."""
    df = df.copy()
    df["Hora_sin"] = np.sin(2 * np.pi * df["Hora"] / 24.0)
    df["Hora_cos"] = np.cos(2 * np.pi * df["Hora"] / 24.0)
    df["Mes_sin"] = np.sin(2 * np.pi * df["Mes"] / 12.0)
    df["Mes_cos"] = np.cos(2 * np.pi * df["Mes"] / 12.0)
    df["Dia_sin"] = np.sin(2 * np.pi * df["DiaDelAnio"] / 365.25)
    df["Dia_cos"] = np.cos(2 * np.pi * df["DiaDelAnio"] / 365.25)
    return df


def matriz_X(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve X (features) en float32, en el orden fijo de FEATURES."""
    return df[FEATURES].astype(np.float32)


def vector_y(df: pd.DataFrame) -> np.ndarray:
    return df[OBJETIVO].to_numpy(dtype=np.float32)
