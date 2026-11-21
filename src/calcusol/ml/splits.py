"""
ML — Particiones del dataset para entrenamiento y evaluación (split 1b).

Reproduce el split validado, sin fuga:
  - Solo registros DEFINITIVOS (decisión A/B: los preliminares degradaban
    +5.5% el MAE mensual).
  - test_temporal: estaciones de train, años >= 2021 (años futuros vistos).
  - test_spatial: 20% de estaciones apartadas, ESTRATIFICADAS por cuartil de
    altitud (cubre costa y montaña). Estaciones nunca vistas = caso de uso real.
  - train: el resto (estaciones no apartadas, años <= 2020).

Determinista con seed fija, para que el repo reproduzca el mismo split del
entrenamiento. Lee el Gold local (data/gold/dataset) por defecto.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calcusol.config import settings

DATASET = settings.gold_path("dataset")
SEED = 42
TEST_YEAR_FROM = 2021
N_BINS_ALT = 4
HOLDOUT_FRAC = 0.2
MAX_WM2 = 1500


def cargar_gold(ruta: str | None = None) -> pd.DataFrame:
    """Lee el Gold, excluye preliminares, limpieza física, añade ts/year."""
    df = pd.read_parquet(ruta or DATASET)
    if "anio" in df.columns:
        df = df.rename(columns={"anio": "Anio"})
    df = df[~df["EsPreliminar"]].copy()                     # solo definitivos
    for c in ["Valor", "ALLSKY_SFC_SW_DWN"]:
        df = df[(df[c] >= 0) & (df[c] <= MAX_WM2)]
    df["ts"] = pd.to_datetime(df["Fecha"])
    df["year"] = df["ts"].dt.year
    df = df.drop_duplicates(["CodigoEstacion", "ts"])
    return df


def estaciones_holdout(df: pd.DataFrame) -> list[int]:
    """20% de estaciones apartadas, estratificadas por cuartil de altitud."""
    est = df.groupby("CodigoEstacion")["Altitud"].first().reset_index()
    est["alt_bin"] = pd.qcut(est["Altitud"], N_BINS_ALT, labels=False, duplicates="drop")
    rng = np.random.default_rng(SEED)
    hold: list[int] = []
    for _, g in est.groupby("alt_bin"):
        k = max(1, round(len(g) * HOLDOUT_FRAC))
        hold += list(rng.choice(g["CodigoEstacion"].to_numpy(), size=k, replace=False))
    return sorted(int(x) for x in hold)


def hacer_splits(ruta: str | None = None) -> dict[str, pd.DataFrame]:
    """Devuelve {'train','test_temporal','test_spatial'}."""
    df = cargar_gold(ruta)
    hold = estaciones_holdout(df)
    is_hold = df["CodigoEstacion"].isin(hold)
    is_recent = df["year"] >= TEST_YEAR_FROM
    return {
        "train":         df[~is_hold & ~is_recent].reset_index(drop=True),
        "test_temporal": df[~is_hold &  is_recent].reset_index(drop=True),
        "test_spatial":  df[is_hold].reset_index(drop=True),
    }


def resumen() -> None:
    sp = hacer_splits()
    for n, d in sp.items():
        print(f"{n:<14} {len(d):>10,} filas | {d['CodigoEstacion'].nunique()} estaciones")


if __name__ == "__main__":
    resumen()
