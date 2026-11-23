"""
ML — Líneas base (R1-1).

Las dos referencias contra las que se compara cualquier modelo:

  B-0  NASA tal cual: usar ALLSKY_SFC_SW_DWN como predicción de la radiación
       medida. Es el "antes" del antes/después; mide el sesgo satelital crudo.
  B-1  Calibración lineal global: una regresión lineal simple de la medición
       IDEAM sobre la radiación NASA (y = a + b·ALLSKY). Corrige el sesgo medio
       global, sin usar ubicación, hora ni nada más.

Un modelo de ML solo es "bueno" si supera a B-1 (y por mucho a B-0).

Meta del plan: B-0 en test ≈ 109.12 Wh/m². Nuestra tabla corta en 2005 con
reglas propias, así que se espera CERCA de 109, no exacto; sirve de control de
que el dataset y el código están alineados con el trabajo previo del equipo.

Métrica principal: MAE horario (Wh/m²). También RMSE y sesgo (MBE).

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.ml.baseline
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from calcusol.gold import splits

OBJETIVO = "Valor"
NASA_RAD = "ALLSKY_SFC_SW_DWN"
SEMILLA = 42


def _metricas(y_true, y_pred) -> dict:
    return {
        "MAE": float(mean_absolute_error(y_true, y_pred)),
        "RMSE": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "MBE": float(np.mean(np.asarray(y_pred) - np.asarray(y_true))),
        "R2": float(r2_score(y_true, y_pred)),
    }


def b0_nasa_cruda(y_test, nasa_test) -> dict:
    """B-0: la predicción ES la radiación NASA sin tocar."""
    return _metricas(y_test, nasa_test)


def b1_calibracion_lineal(nasa_train, y_train, nasa_test, y_test) -> tuple[dict, dict]:
    """
    B-1: ajusta y = a + b·ALLSKY con el TRAIN y evalúa en TEST. Devuelve
    (coeficientes, métricas de test). Sin fuga: se ajusta solo con train.
    """
    reg = LinearRegression()
    reg.fit(nasa_train.reshape(-1, 1), y_train)
    pred_test = reg.predict(nasa_test.reshape(-1, 1))
    coef = {"a_intercepto": float(reg.intercept_), "b_pendiente": float(reg.coef_[0])}
    return coef, _metricas(y_test, pred_test)


def main() -> None:
    cols = [OBJETIVO, NASA_RAD, "Fecha", "Sitio"]
    train = splits.cargar_train(cols)
    test = splits.cargar_test(cols)

    y_train = train[OBJETIVO].to_numpy(dtype="float64")
    nasa_train = train[NASA_RAD].to_numpy(dtype="float64")
    y_test = test[OBJETIVO].to_numpy(dtype="float64")
    nasa_test = test[NASA_RAD].to_numpy(dtype="float64")

    print(f"Train: {len(train):,} filas | Test: {len(test):,} filas\n")

    b0 = b0_nasa_cruda(y_test, nasa_test)
    coef, b1 = b1_calibracion_lineal(nasa_train, y_train, nasa_test, y_test)

    print("=" * 56)
    print("LÍNEAS BASE (evaluadas en TEST >= 2020)")
    print("=" * 56)
    print(f"B-0 NASA cruda       MAE={b0['MAE']:.2f}  RMSE={b0['RMSE']:.2f}  "
          f"MBE={b0['MBE']:+.2f}  R2={b0['R2']:.4f}")
    print(f"B-1 calibración lin. MAE={b1['MAE']:.2f}  RMSE={b1['RMSE']:.2f}  "
          f"MBE={b1['MBE']:+.2f}  R2={b1['R2']:.4f}")
    print(f"    (y = {coef['a_intercepto']:.2f} + {coef['b_pendiente']:.4f}·ALLSKY)")
    mejora_b1 = (1 - b1["MAE"] / b0["MAE"]) * 100
    print(f"\nMejora de B-1 sobre B-0: {mejora_b1:.2f} %")
    print(f"Meta del plan para B-0: ≈ 109.12 Wh/m²  (se espera cerca, no exacto)")


if __name__ == "__main__":
    main()
