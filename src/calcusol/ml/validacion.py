"""
ML — Validación y evaluación (R2).

Produce los entregables de evaluación del rol R2:

  R2-3  Mapa de errores: desglose del error (MAE base vs modelo, mejora, sesgo)
        por estación, departamento, hora, banda de altitud y nivel de nubosidad
        (ALLSKY_KT), a nivel horario y mensual.
  R2-3  Tabla de mejora por sitio (examen espacial por Sitio).
  R2-4  Tabla "dónde NO supera a NASA": sitios en que el modelo empeora o no
        mejora el baseline, con el porqué (NASA ya tenía bajo error base).

La evaluación espacial usa validación cruzada de 5 pliegues agrupando por SITIO
(ver ml/splits.folds_espaciales_por_sitio): cada fila recibe la predicción del
pliegue en que su sitio fue TEST, de modo que ninguna predicción proviene de un
modelo que vio ese sitio. Esto evita la fuga que preocupa a R2-1.

Salida: CSV en reports/ml_validacion/. Todo determinista (seed fija).

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.ml.validacion
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from calcusol.ml import features as feat
from calcusol.ml import splits
from calcusol.ml.train import HP

SALIDA = Path("reports") / "ml_validacion"
BANDAS_ALT = [0, 310, 1259, 1952, 10000]
ETIQ_ALT = ["≤310", "310–1259", "1259–1952", ">1952"]
# cortes de nubosidad sobre ALLSKY_KT (0=muy nublado, 1=muy despejado)
BANDAS_KT = [0, 0.4, 0.6, 1.5]
ETIQ_KT = ["nublado (<0.4)", "parcial (0.4–0.6)", "despejado (>0.6)"]


# ===================================================== predicción espacial CV
def _predecir_cv_espacial() -> pd.DataFrame:
    """
    Entrena un modelo por pliegue (5-fold por Sitio) y predice sobre el pliegue
    de test. Devuelve TODAS las filas con la predicción out-of-fold (cada fila
    predicha por un modelo que NO vio su sitio). Añade columnas de error.
    """
    df, pliegues = splits.folds_espaciales_por_sitio()
    df = feat.build_features(df)                     # añade features + residuo
    X = feat.matriz_X(df)
    base = df["ALLSKY_SFC_SW_DWN"].to_numpy("float32")
    y_real = df["Valor"].to_numpy("float32")

    # recomputar pliegues sobre el df ya filtrado por build_features (horas sol)
    from sklearn.model_selection import GroupKFold
    gkf = GroupKFold(n_splits=splits.N_FOLDS_ESPACIAL)
    pred = np.full(len(df), np.nan, dtype="float32")
    y_resid = df[feat.OBJETIVO].to_numpy("float32")
    for i_tr, i_te in gkf.split(df, groups=df["Sitio"].to_numpy()):
        m = lgb.LGBMRegressor(**HP).fit(X.iloc[i_tr], y_resid[i_tr])
        pred[i_te] = np.clip(base[i_te] + m.predict(X.iloc[i_te]), 0, None)

    df = df.assign(
        pred_modelo=pred, pred_base=base, y=y_real,
        err_modelo=np.abs(y_real - pred),
        err_base=np.abs(y_real - base),
    )
    return df


# ===================================================== métricas por grupo
def _mae_mejora(g: pd.DataFrame) -> pd.Series:
    mb, mm = g["err_base"].mean(), g["err_modelo"].mean()
    return pd.Series({
        "n": len(g),
        "MAE_base": mb,
        "MAE_modelo": mm,
        "mejora_%": 100 * (1 - mm / mb) if mb else np.nan,
        "sesgo_base_%": 100 * (g["pred_base"].mean() - g["y"].mean()) / g["y"].mean(),
        "sesgo_modelo_%": 100 * (g["pred_modelo"].mean() - g["y"].mean()) / g["y"].mean(),
    })


def _mensual(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega a promedio mensual por estación antes de medir (métrica de negocio)."""
    f = pd.to_datetime(df["Fecha"])
    per = f.dt.to_period("M")
    g = df.groupby(["CodigoEstacion", per]).agg(
        y=("y", "mean"), pred_modelo=("pred_modelo", "mean"),
        pred_base=("pred_base", "mean"),
        Departamento=("Departamento", "first"), Altitud=("Altitud", "first"),
        Sitio=("Sitio", "first"))
    g["err_modelo"] = (g["y"] - g["pred_modelo"]).abs()
    g["err_base"] = (g["y"] - g["pred_base"]).abs()
    return g.reset_index()


# ===================================================== R2-3 mapa de errores
def mapa_errores(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Desglose del error por varias dimensiones, en horario y mensual."""
    dfm = _mensual(df)
    dfm["banda_alt"] = pd.cut(dfm["Altitud"], BANDAS_ALT, labels=ETIQ_ALT)
    df = df.copy()
    df["banda_alt"] = pd.cut(df["Altitud"], BANDAS_ALT, labels=ETIQ_ALT)
    df["banda_kt"] = pd.cut(df["ALLSKY_KT"], BANDAS_KT, labels=ETIQ_KT)

    salida = {
        "por_estacion_mes": dfm.groupby("CodigoEstacion").apply(_mae_mejora, include_groups=False),
        "por_departamento_mes": dfm.groupby("Departamento").apply(_mae_mejora, include_groups=False),
        "por_banda_altitud_mes": dfm.groupby("banda_alt", observed=True).apply(_mae_mejora, include_groups=False),
        "por_hora_horario": df.groupby("Hora").apply(_mae_mejora, include_groups=False),
        "por_nubosidad_horario": df.groupby("banda_kt", observed=True).apply(_mae_mejora, include_groups=False),
    }
    return {k: v.round(2) for k, v in salida.items()}


# ===================================================== R2-3 tabla por sitio
def tabla_por_sitio(df: pd.DataFrame) -> pd.DataFrame:
    """Mejora mensual por SITIO (la que R3 usa para elegir sus casos)."""
    dfm = _mensual(df)
    t = dfm.groupby("Sitio").apply(_mae_mejora, include_groups=False)
    alt = dfm.groupby("Sitio")["Altitud"].first()
    dep = dfm.groupby("Sitio")["Departamento"].first()
    t = t.assign(Altitud=alt, Departamento=dep).sort_values("mejora_%", ascending=False)
    return t.round(2)


# ===================================================== R2-4 dónde no supera NASA
def donde_no_supera(tabla_sitio: pd.DataFrame) -> pd.DataFrame:
    """
    Sitios donde el modelo NO mejora a NASA (mejora_% <= 0). Incluye el MAE base
    para mostrar que suele ocurrir donde NASA ya tenía poco error.
    """
    peor = tabla_sitio[tabla_sitio["mejora_%"] <= 0].copy()
    peor = peor.sort_values("mejora_%")
    return peor.round(2)


# ===================================================== orquestación
def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    print("Evaluando con validación cruzada espacial por Sitio (5-fold, sin fuga)...")
    df = _predecir_cv_espacial()
    print(f"Filas evaluadas: {len(df):,} | sitios: {df['Sitio'].nunique()}")

    mapa = mapa_errores(df)
    for nombre, tabla in mapa.items():
        tabla.to_csv(SALIDA / f"mapa_{nombre}.csv")
        print(f"\n== {nombre} ==")
        print(tabla.to_string())

    sitio = tabla_por_sitio(df)
    sitio.to_csv(SALIDA / "tabla_por_sitio.csv")
    print(f"\n== tabla por sitio (mejora mensual) — {len(sitio)} sitios ==")
    print(sitio.to_string())

    peor = donde_no_supera(sitio)
    peor.to_csv(SALIDA / "donde_no_supera_nasa.csv")
    n = len(sitio)
    print(f"\n== dónde NO supera a NASA: {len(peor)} de {n} sitios ==")
    print(peor.to_string())
    print(f"\nResumen: el modelo mejora en {n - len(peor)}/{n} sitios "
          f"({100*(n-len(peor))/n:.0f}%).")
    print(f"\nCSV guardados en: {SALIDA}")


if __name__ == "__main__":
    main()
