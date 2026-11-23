"""
ML — Pruebas de robustez (R2-2).

Tres pruebas que respaldan las decisiones de diseño con evidencia:

  1. Prueba por celda NASA (D10): confirma con los datos que estaciones de un
     mismo Sitio (misma celda NASA) tienen series NASA idénticas, sin suponer el
     tamaño de la celda. Justifica agrupar por Sitio en la validación (R2-1).
  2. A/B de preliminares: compara entrenar con (definitivos + preliminares)
     frente a (solo definitivos), midiendo SIEMPRE sobre prueba definitiva. La
     decisión se apoya en "no empeora", no en "mejora".
  3. Curvas de aprendizaje: se delegan en ml/diagnostico.py (ya implementadas).

Salida: CSV en reports/ml_robustez/.

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.ml.robustez
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from calcusol.ml import features as feat
from calcusol.ml import splits
from calcusol.ml.train import HP

SALIDA = Path("reports") / "ml_robustez"
VARS_NASA = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]


# ===================================================== 1. celda NASA (D10)
def prueba_celda_nasa() -> pd.DataFrame:
    """
    Para cada Sitio con más de una estación, comprueba si las series NASA de sus
    estaciones son idénticas en las horas comunes. Confirma D10 empíricamente.
    """
    df = splits.cargar_gold()
    por_sitio = df.groupby("Sitio")["CodigoEstacion"].nunique()
    sitios_multi = por_sitio[por_sitio > 1].index.tolist()

    filas = []
    for s in sitios_multi:
        sub = df[df["Sitio"] == s]
        ests = sorted(sub["CodigoEstacion"].unique())
        # tomar las dos primeras estaciones y cruzar por Fecha
        a = sub[sub["CodigoEstacion"] == ests[0]][["Fecha", *VARS_NASA]]
        b = sub[sub["CodigoEstacion"] == ests[1]][["Fecha", *VARS_NASA]]
        j = a.merge(b, on="Fecha", suffixes=("_a", "_b"))
        if j.empty:
            continue
        difs = {v: float(np.nanmax(np.abs(
            j[f"{v}_a"].to_numpy() - j[f"{v}_b"].to_numpy()))) for v in VARS_NASA}
        filas.append({
            "Sitio": s, "estacion_a": ests[0], "estacion_b": ests[1],
            "horas_comunes": len(j),
            "max_dif_ALLSKY": difs["ALLSKY_SFC_SW_DWN"],
            "identicas": all(d < 1e-6 for d in difs.values()),
        })
    return pd.DataFrame(filas)


# ===================================================== 2. A/B preliminares
def _mae_mensual_espacial(incluir_preliminares: bool) -> float:
    """
    Entrena (según la bandera) y mide el MAE MENSUAL en el holdout espacial por
    Sitio, SIEMPRE sobre filas definitivas (prueba limpia).
    """
    df = splits.cargar_gold()
    sitios_hold = splits.sitios_holdout(df)

    # train: sitios NO holdout, años <= 2020; filtra preliminares según bandera
    tr = df[(~df["Sitio"].isin(sitios_hold)) & (df["year"] < splits.TEST_YEAR_FROM)]
    if not incluir_preliminares:
        tr = tr[~tr["EsPreliminar"]]
    # test: sitios holdout, SIEMPRE definitivos
    te = df[(df["Sitio"].isin(sitios_hold)) & (~df["EsPreliminar"])]

    tr = feat.build_features(tr)
    te = feat.build_features(te)
    m = lgb.LGBMRegressor(**HP).fit(feat.matriz_X(tr), tr[feat.OBJETIVO].astype("float32"))

    pred = np.clip(te["ALLSKY_SFC_SW_DWN"].to_numpy() + m.predict(feat.matriz_X(te)), 0, None)
    d = te.assign(pred=pred, per=pd.to_datetime(te["Fecha"]).dt.to_period("M"))
    g = d.groupby(["CodigoEstacion", "per"]).agg(y=("Valor", "mean"), p=("pred", "mean"))
    return float(mean_absolute_error(g["y"], g["p"]))


def prueba_ab_preliminares() -> pd.DataFrame:
    mae_con = _mae_mensual_espacial(incluir_preliminares=True)
    mae_sin = _mae_mensual_espacial(incluir_preliminares=False)
    delta = 100 * (mae_con - mae_sin) / mae_sin
    return pd.DataFrame([
        {"config": "definitivos + preliminares", "MAE_mensual": round(mae_con, 2)},
        {"config": "solo definitivos", "MAE_mensual": round(mae_sin, 2)},
        {"config": "diferencia (%)", "MAE_mensual": round(delta, 2)},
    ])


# ===================================================== orquestación
def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)

    print("== 1. Prueba por celda NASA (D10) ==")
    celda = prueba_celda_nasa()
    celda.to_csv(SALIDA / "celda_nasa.csv", index=False)
    print(celda.to_string(index=False))
    if not celda.empty:
        print(f"\nSitios con series NASA idénticas entre sus estaciones: "
              f"{int(celda['identicas'].sum())}/{len(celda)}")

    print("\n== 2. A/B de preliminares (MAE mensual, holdout espacial) ==")
    ab = prueba_ab_preliminares()
    ab.to_csv(SALIDA / "ab_preliminares.csv", index=False)
    print(ab.to_string(index=False))
    print("Lectura: si 'solo definitivos' no empeora (o mejora), se excluyen "
          "los preliminares.")

    print("\n== 3. Curvas de aprendizaje ==")
    print("Ya implementadas en ml/diagnostico.py. Ejecuta:")
    print("  python -m calcusol.ml.diagnostico")

    print(f"\nCSV guardados en: {SALIDA}")


if __name__ == "__main__":
    main()
