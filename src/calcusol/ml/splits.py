"""
ML — Particiones del dataset para entrenamiento y evaluación (split 1b).

Reproduce el split validado, sin fuga:
  - Solo registros DEFINITIVOS (decisión A/B: los preliminares degradaban
    +5.5% el MAE mensual).
  - test_temporal: estaciones de train, años >= 2021 (años futuros vistos).
  - test_spatial: 20% de estaciones apartadas, ESTRATIFICADAS por cuartil de
    altitud (cubre costa y montaña). Estaciones nunca vistas = caso de uso real.
  - train: el resto (estaciones no apartadas, años <= 2020).

Además, para R2 (validación y evaluación) añade los tres exámenes del plan:
  - Espacial: GroupKFold de 5 pliegues agrupando por SITIO (no por estación),
    que cierra la fuga de las estaciones gemelas que comparten celda NASA.
  - Espacio-temporal: sitios nuevos en años futuros (el caso más exigente).
  - Prueba de no-fuga: verifica que ningún sitio está en train y test a la vez.

Determinista con seed fija, para que el repo reproduzca el mismo split del
entrenamiento. Lee el Gold local (data/gold/dataset) por defecto.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from calcusol.config import settings

DATASET = settings.gold_path("dataset")
SEED = 42
TEST_YEAR_FROM = 2021
N_BINS_ALT = 4
HOLDOUT_FRAC = 0.2
MAX_WM2 = 1500


@lru_cache(maxsize=2)
def _leer_parquet(ruta: str) -> pd.DataFrame:
    """
    Lee un dataset parquet (con particiones Anio=YYYY). En s3:// pasa por s3fs/fsspec: así
    solo hace falta ListBucket + GetObject sobre los archivos (pyarrow nativo exige HeadObject
    sobre la carpeta y falla con permisos mínimos).
    La lectura se guarda en memoria: los exámenes de R2 piden el mismo Gold varias veces.
    Quien use el resultado no debe modificarlo en sitio (cargar_gold ya trabaja sobre copias).
    """
    if "://" not in ruta:
        return pd.read_parquet(ruta)
    import fsspec
    import pyarrow.dataset as ds

    ruta = "s3://" + ruta[len("s3a://"):] if ruta.startswith("s3a://") else ruta
    fs, base = fsspec.core.url_to_fs(ruta)
    archivos = sorted(p for p in fs.find(base) if p.endswith(".parquet"))
    if not archivos:
        raise FileNotFoundError(f"No hay archivos parquet en {ruta}")
    dataset = ds.dataset(archivos, filesystem=fs, format="parquet", partition_base_dir=base,
                         partitioning=ds.partitioning(flavor="hive"))
    return dataset.to_table().to_pandas()


def cargar_gold(ruta: str | None = None) -> pd.DataFrame:
    """Lee el Gold, excluye preliminares, limpieza física, añade ts/year."""
    df = _leer_parquet(ruta or DATASET)
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


# ============================================================ R2-1: por Sitio
N_FOLDS_ESPACIAL = 5


def folds_espaciales_por_sitio(ruta: str | None = None):
    """
    Examen espacial de R2-1: validación cruzada de 5 pliegues dejando fuera
    SITIOS completos (no estaciones). Agrupar por Sitio evita la fuga de las
    estaciones gemelas que comparten celda NASA.

    Devuelve (df, lista_de_pliegues). Cada pliegue es (idx_train, idx_test)
    sobre el índice de `df`, con la garantía de que ningún Sitio aparece a la
    vez en train y en test del mismo pliegue. Usa solo años <= 2020 (el examen
    espacio-temporal se encarga de los años futuros).
    """
    from sklearn.model_selection import GroupKFold

    df = cargar_gold(ruta)
    df = df[df["year"] < TEST_YEAR_FROM].reset_index(drop=True)
    gkf = GroupKFold(n_splits=N_FOLDS_ESPACIAL)
    grupos = df["Sitio"].to_numpy()
    pliegues = list(gkf.split(df, groups=grupos))
    return df, pliegues


def split_espacio_temporal(ruta: str | None = None) -> dict[str, pd.DataFrame]:
    """
    Examen espacio-temporal de R2-1: sitios que el modelo NO vio, en años
    futuros (>= 2021). Es el caso más exigente: lugar nuevo Y tiempo nuevo.

    train: sitios de entrenamiento, años <= 2020.
    test:  sitios apartados (holdout espacial por Sitio), años >= 2021.
    """
    df = cargar_gold(ruta)
    sitios_hold = sitios_holdout(df)
    is_hold = df["Sitio"].isin(sitios_hold)
    is_recent = df["year"] >= TEST_YEAR_FROM
    return {
        "train":         df[~is_hold & ~is_recent].reset_index(drop=True),
        "test_spatiotemporal": df[is_hold & is_recent].reset_index(drop=True),
    }


def sitios_holdout(df: pd.DataFrame) -> list[int]:
    """20% de SITIOS apartados, estratificados por cuartil de altitud."""
    sit = df.groupby("Sitio")["Altitud"].first().reset_index()
    sit["alt_bin"] = pd.qcut(sit["Altitud"], N_BINS_ALT, labels=False, duplicates="drop")
    rng = np.random.default_rng(SEED)
    hold: list[int] = []
    for _, g in sit.groupby("alt_bin"):
        k = max(1, round(len(g) * HOLDOUT_FRAC))
        hold += list(rng.choice(g["Sitio"].to_numpy(), size=k, replace=False))
    return sorted(int(x) for x in hold)


def verificar_sin_fuga(ruta: str | None = None) -> dict:
    """
    Prueba de no-fuga de R2-1. Comprueba que:
      1. En el examen espacial (5-fold por Sitio) ningún Sitio aparece en train
         y test del mismo pliegue.
      2. Las estaciones gemelas (misma celda NASA / mismo Sitio) nunca quedan
         separadas entre train y test espacial.
    Devuelve un dict con el veredicto y las cifras de respaldo.
    """
    df, pliegues = folds_espaciales_por_sitio(ruta)
    sitio = df["Sitio"].to_numpy()
    fuga_pliegues = []
    for i, (i_tr, i_te) in enumerate(pliegues):
        inter = set(sitio[i_tr]) & set(sitio[i_te])
        fuga_pliegues.append((i, len(inter)))
    sin_fuga_espacial = all(n == 0 for _, n in fuga_pliegues)

    # estaciones que comparten Sitio (gemelas)
    por_sitio = df.groupby("Sitio")["CodigoEstacion"].nunique()
    sitios_multi = int((por_sitio > 1).sum())

    return {
        "sin_fuga_espacial": sin_fuga_espacial,
        "interseccion_por_pliegue": fuga_pliegues,
        "n_estaciones": int(df["CodigoEstacion"].nunique()),
        "n_sitios": int(df["Sitio"].nunique()),
        "sitios_con_varias_estaciones": sitios_multi,
    }


def resumen() -> None:
    sp = hacer_splits()
    print("Split 1b (R1 — holdout por estación):")
    for n, d in sp.items():
        print(f"  {n:<14} {len(d):>10,} filas | {d['CodigoEstacion'].nunique()} estaciones")

    print("\nExamen espacial por Sitio (R2-1 — 5-fold GroupKFold):")
    df, pliegues = folds_espaciales_por_sitio()
    for i, (i_tr, i_te) in enumerate(pliegues):
        s_tr = df.iloc[i_tr]["Sitio"].nunique()
        s_te = df.iloc[i_te]["Sitio"].nunique()
        print(f"  pliegue {i}: train {len(i_tr):>9,} filas / {s_tr} sitios | "
              f"test {len(i_te):>8,} filas / {s_te} sitios")

    print("\nExamen espacio-temporal (R2-1 — sitios nuevos, años >=2021):")
    st = split_espacio_temporal()
    for n, d in st.items():
        print(f"  {n:<20} {len(d):>10,} filas | {d['Sitio'].nunique()} sitios")

    print("\nPrueba de no-fuga (R2-1):")
    v = verificar_sin_fuga()
    print(f"  estaciones: {v['n_estaciones']} | sitios: {v['n_sitios']} | "
          f"sitios con >1 estación: {v['sitios_con_varias_estaciones']}")
    print(f"  sin fuga espacial (ningún sitio en train+test del mismo pliegue): "
          f"{v['sin_fuga_espacial']}")


if __name__ == "__main__":
    resumen()
