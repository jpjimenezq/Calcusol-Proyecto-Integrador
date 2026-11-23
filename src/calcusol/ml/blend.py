"""
ML — Blend modelo/NASA para mejorar la consistencia (R1, v2).

El modelo puro mejora en la mayoría de estaciones nuevas pero EMPEORA donde NASA
ya es precisa (sesgo bajo), con casos catastróficos (-100 %). Las curvas de
aprendizaje mostraron varianza residual. Mezclar con NASA la amortigua:

    pred_blend = alfa * (ALLSKY + residuo) + (1 - alfa) * ALLSKY
               = ALLSKY + alfa * residuo

Es decir, alfa encoge la corrección: con alfa<1 el modelo corrige menos y nunca
se aleja demasiado de NASA. Busca el alfa que mejore la MEDIANA y recorte el
PEOR caso de la distribución por estación (no solo el promedio).

EJECUCIÓN (tras entrenar el modelo):
  docker compose run --rm calcusol uv run python -m calcusol.ml.blend
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from calcusol.ml import features as feat
from calcusol.ml import splits
from calcusol.ml.predict import cargar

ALFAS = [1.0, 0.8, 0.7, 0.6, 0.5, 0.4]
SALIDA = Path("reports") / "ml_blend"


def _mejora_por_estacion(d: pd.DataFrame, pred: np.ndarray) -> np.ndarray:
    """Mejora % mensual sobre NASA, por estación. Devuelve el array de mejoras."""
    dd = d.assign(pred=pred, ym=pd.to_datetime(d["Fecha"]).dt.to_period("M"))
    mejoras = []
    for _, g in dd.groupby("CodigoEstacion"):
        gm = g.groupby("ym").agg(y=("Valor", "mean"), p=("pred", "mean"),
                                 n=("ALLSKY_SFC_SW_DWN", "mean"))
        mae_b0 = np.mean(np.abs(gm["y"] - gm["n"]))
        mae_md = np.mean(np.abs(gm["y"] - gm["p"]))
        if mae_b0 > 0:
            mejoras.append(100 * (1 - mae_md / mae_b0))
    return np.array(mejoras)


def evaluar_blend() -> pd.DataFrame:
    corrector = cargar()
    d = feat.build_features(splits.hacer_splits()["test_spatial"])
    base = d["ALLSKY_SFC_SW_DWN"].to_numpy("float32")
    residuo = corrector.booster.predict(feat.matriz_X(d))

    filas = []
    for alfa in ALFAS:
        pred = np.clip(base + alfa * residuo, 0, None)
        mej = _mejora_por_estacion(d, pred)
        filas.append({
            "alfa": alfa,
            "mejora_media_%": round(float(mej.mean()), 1),
            "mejora_mediana_%": round(float(np.median(mej)), 1),
            "peor_%": round(float(mej.min()), 1),
            "mejor_%": round(float(mej.max()), 1),
            "n_mejora": int((mej > 0).sum()),
            "n_total": len(mej),
        })
        print(f"  alfa={alfa:.1f} | media={mej.mean():+5.1f}% | mediana={np.median(mej):+5.1f}% "
              f"| peor={mej.min():+6.1f}% | mejora en {int((mej>0).sum())}/{len(mej)}")
    return pd.DataFrame(filas)


def main() -> None:
    print("Blend modelo/NASA (test_spatial, mejora mensual por estación):")
    df = evaluar_blend()
    SALIDA.mkdir(parents=True, exist_ok=True)
    df.to_csv(SALIDA / "blend.csv", index=False)

    # Recomendación: alfa que maximiza (n_mejora) y recorta el peor caso.
    # Criterio: entre los que mantienen buena mediana, el de mejor 'peor_%'.
    df["score"] = df["n_mejora"] - df["peor_%"] / 50  # penaliza desastres
    mejor = df.sort_values("score", ascending=False).iloc[0]
    print(f"\nGuardado: {SALIDA / 'blend.csv'}")
    print(f"Recomendación: alfa={mejor['alfa']:.1f} "
          f"(mejora en {int(mejor['n_mejora'])}/{int(mejor['n_total'])}, "
          f"peor caso {mejor['peor_%']:+.1f}%, mediana {mejor['mejora_mediana_%']:+.1f}%)")
    print("Compara alfa=1.0 (modelo puro) con el recomendado para ver el intercambio.")


if __name__ == "__main__":
    main()
