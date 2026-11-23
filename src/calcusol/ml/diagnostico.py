"""
ML — Diagnóstico: curvas de aprendizaje (sesgo vs varianza) (R1, criterio 4).

Entrena el modelo con fracciones crecientes del train (10/25/50/100 %) y mide el
MAE en el propio train y en validación espacial (GroupKFold por estación). La
forma de las curvas indica:

  - Validación baja al añadir datos y sigue cayendo -> más datos ayudarían.
  - Validación ya aplanada -> techo de información/features, no de cantidad.
  - Brecha grande train-validación -> varianza (sobreajuste).
  - Ambas altas y juntas -> sesgo (subajuste).

Las fracciones se muestrean POR ESTACIÓN (no por fila) para no romper la
validación espacial.

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.ml.diagnostico
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold

from calcusol.ml import features as feat
from calcusol.ml import splits
from calcusol.ml.train import HP

FRACCIONES = [0.10, 0.25, 0.50, 1.00]
N_FOLDS = 3          # folds espaciales para la curva (3 basta para la tendencia)
SEED = 42
SALIDA = Path("reports") / "ml_diagnostico"


def _mae_cv(tr: pd.DataFrame) -> tuple[float, float]:
    """MAE medio en train y en validación espacial (GroupKFold por estación)."""
    X = feat.matriz_X(tr)
    y = tr[feat.OBJETIVO].to_numpy("float32")
    base = tr["ALLSKY_SFC_SW_DWN"].to_numpy("float32")
    yv_real = tr["Valor"].to_numpy("float32")
    grupos = tr["CodigoEstacion"].to_numpy()

    gkf = GroupKFold(n_splits=N_FOLDS)
    mae_tr, mae_va = [], []
    for i_tr, i_va in gkf.split(X, groups=grupos):
        m = lgb.LGBMRegressor(**HP).fit(X.iloc[i_tr], y[i_tr])
        # error sobre la radiación reconstruida (ALLSKY + residuo), no sobre el residuo
        pred_tr = np.clip(base[i_tr] + m.predict(X.iloc[i_tr]), 0, None)
        pred_va = np.clip(base[i_va] + m.predict(X.iloc[i_va]), 0, None)
        mae_tr.append(mean_absolute_error(yv_real[i_tr], pred_tr))
        mae_va.append(mean_absolute_error(yv_real[i_va], pred_va))
    return float(np.mean(mae_tr)), float(np.mean(mae_va))


def curva_aprendizaje() -> pd.DataFrame:
    sp = splits.hacer_splits()
    tr_full = feat.build_features(sp["train"])
    estaciones = tr_full["CodigoEstacion"].unique()
    rng = np.random.default_rng(SEED)

    filas = []
    for frac in FRACCIONES:
        k = max(2, round(len(estaciones) * frac))
        est_sub = rng.choice(estaciones, size=k, replace=False)
        tr = tr_full[tr_full["CodigoEstacion"].isin(est_sub)]
        mae_tr, mae_va = _mae_cv(tr)
        filas.append({"fraccion": frac, "estaciones": k, "filas": len(tr),
                      "MAE_train": round(mae_tr, 2), "MAE_validacion": round(mae_va, 2),
                      "brecha": round(mae_va - mae_tr, 2)})
        print(f"  {int(frac*100):>3}% | {k:>2} est | {len(tr):>9,} filas | "
              f"MAE train={mae_tr:.2f} val={mae_va:.2f} (brecha {mae_va-mae_tr:+.2f})")
    return pd.DataFrame(filas)


def main() -> None:
    print("Curva de aprendizaje (MAE horario W/m², validación espacial):")
    df = curva_aprendizaje()
    SALIDA.mkdir(parents=True, exist_ok=True)
    df.to_csv(SALIDA / "curva_aprendizaje.csv", index=False)

    # Lectura automática de la tendencia (mira TODA la curva, no solo el último tramo)
    val = df["MAE_validacion"].to_numpy()
    rango_val = val.max() - val.min()
    tendencia = val[-1] - val[0]            # 100% vs 10%
    print(f"\nGuardado: {SALIDA / 'curva_aprendizaje.csv'}")
    print("\nLectura:")
    print(f"  MAE validación por fracción: {', '.join(f'{v:.1f}' for v in val)}")
    print(f"  Rango de oscilación: {rango_val:.1f} W/m² | tendencia 10%->100%: {tendencia:+.1f}")
    if rango_val < 8 and abs(tendencia) < 5:
        print("  -> La validación está PLANA: más datos (estaciones) no mejoran la")
        print("     generalización. El límite es de información/features, no de cantidad.")
    elif tendencia < -5:
        print("  -> La validación baja al añadir datos: más estaciones ayudarían.")
    else:
        print("  -> La validación no mejora con más datos (posible techo).")
    brechas = df["brecha"].to_numpy()
    print(f"  Brecha train-validación: {brechas[0]:.1f} (10%) -> {brechas[-1]:.1f} (100%)")
    if brechas[-1] < brechas[0] - 2:
        print(f"     La brecha se reduce con más datos -> varianza que baja; "
              f"queda {brechas[-1]:.0f} W/m² (algo de sobreajuste residual).")


if __name__ == "__main__":
    main()
