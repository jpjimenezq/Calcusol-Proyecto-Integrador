"""
ML — Evaluación del modelo en los tres exámenes de R2-1.

Evalúa el modelo entrenado sobre:
  - test_temporal: estaciones de train, años >= 2021 (referencia; inflado porque
    el modelo conoce esas estaciones — se declara como tal).
  - test_spatial:  estaciones nunca vistas (holdout por estación).
  - test_spatiotemporal: SITIOS nunca vistos EN años futuros (el caso más
    exigente de R2-1: lugar nuevo y tiempo nuevo).

Reporta MAE/RMSE/R²/sesgo y mejora sobre NASA cruda en tres niveles de
agregación (hora/día/mes). La métrica de negocio es el MAE mensual (el Payback
depende de la energía acumulada).

El desglose fino del error (mapa de errores R2-3, tabla por sitio, "dónde no
supera a NASA" R2-4) está en ml/validacion.py.

EJECUCIÓN (tras entrenar):
  docker compose run --rm calcusol uv run python -m calcusol.ml.evaluate
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from calcusol.ml import features as feat
from calcusol.ml import splits
from calcusol.ml.predict import cargar


def _metricas(y, p):
    err = y - p
    ss_tot = np.sum((y - y.mean()) ** 2)
    return {
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err ** 2))),
        "R2": float(1 - np.sum(err ** 2) / ss_tot) if ss_tot else float("nan"),
        "sesgo_%": float(100 * (p.mean() - y.mean()) / y.mean()),
    }


def _niveles(d: pd.DataFrame, pred) -> dict:
    d = d.assign(pred=pred, f=pd.to_datetime(d["Fecha"]))
    out = {"hora": _metricas(d["Valor"].to_numpy(), pred)}
    for niv, per in [("dia", d["f"].dt.date), ("mes", d["f"].dt.to_period("M"))]:
        g = d.groupby(["CodigoEstacion", per]).agg(y=("Valor", "mean"), p=("pred", "mean"))
        out[niv] = _metricas(g["y"].to_numpy(), g["p"].to_numpy())
    return out


def evaluar() -> dict:
    corrector = cargar()
    sp = splits.hacer_splits()
    resultados = {}

    for nombre in ["test_temporal", "test_spatial"]:
        d = feat.build_features(sp[nombre])
        pred = np.clip(d["ALLSKY_SFC_SW_DWN"].to_numpy()
                       + corrector.booster.predict(feat.matriz_X(d)), 0, None)
        base = d["ALLSKY_SFC_SW_DWN"].to_numpy()
        resultados[nombre] = {"modelo": _niveles(d, pred), "baseline": _niveles(d, base)}

        print(f"\n{'='*58}\n{nombre.upper()}\n{'='*58}")
        print(f"{'nivel':<6}{'MAE_B0':>9}{'MAE_mod':>9}{'mejora':>8}{'sesgoB0':>9}{'sesgoMod':>9}")
        for niv in ["hora", "dia", "mes"]:
            mb = resultados[nombre]["baseline"][niv]
            mm = resultados[nombre]["modelo"][niv]
            print(f"{niv:<6}{mb['MAE']:>9.1f}{mm['MAE']:>9.1f}"
                  f"{100*(1-mm['MAE']/mb['MAE']):>7.1f}%"
                  f"{mb['sesgo_%']:>8.1f}%{mm['sesgo_%']:>8.1f}%")

    # R2-1: examen espacio-temporal (sitios nuevos EN años futuros).
    st = splits.split_espacio_temporal()
    d = feat.build_features(st["test_spatiotemporal"])
    pred = np.clip(d["ALLSKY_SFC_SW_DWN"].to_numpy()
                   + corrector.booster.predict(feat.matriz_X(d)), 0, None)
    base = d["ALLSKY_SFC_SW_DWN"].to_numpy()
    resultados["test_spatiotemporal"] = {
        "modelo": _niveles(d, pred), "baseline": _niveles(d, base)}
    print(f"\n{'='*58}\nTEST_SPATIOTEMPORAL (sitios nuevos, años >=2021)\n{'='*58}")
    print(f"{'nivel':<6}{'MAE_B0':>9}{'MAE_mod':>9}{'mejora':>8}{'sesgoB0':>9}{'sesgoMod':>9}")
    for niv in ["hora", "dia", "mes"]:
        mb = resultados["test_spatiotemporal"]["baseline"][niv]
        mm = resultados["test_spatiotemporal"]["modelo"][niv]
        print(f"{niv:<6}{mb['MAE']:>9.1f}{mm['MAE']:>9.1f}"
              f"{100*(1-mm['MAE']/mb['MAE']):>7.1f}%"
              f"{mb['sesgo_%']:>8.1f}%{mm['sesgo_%']:>8.1f}%")
    return resultados


def main() -> None:
    evaluar()
    print("\nCifra defendible: test_spatial, nivel mensual (estaciones nunca vistas).")


if __name__ == "__main__":
    main()
