"""
ML — Entrenamiento del modelo final (R1).

Entrena el modelo de corrección de radiación sobre el split de entrenamiento
(solo definitivos, años <= 2020) y guarda el artefacto portable + metadatos.

Modelo: LightGBM, variante A0 (17 features), target = residuo (Valor - ALLSKY).
Hiperparámetros: los hallados por Optuna (búsqueda contra validación espacial).
Selección de familia: LightGBM ganó por velocidad en empate técnico con XGBoost
y Random Forest (ver docs/modelo.md).

Corre en Docker local (CPU). Para acelerar en GPU, usar --device gpu (requiere
LightGBM con soporte GPU; en CPU el resultado es reproducible).

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.ml.train
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import sklearn

from calcusol.config import settings
from calcusol.ml import features as feat
from calcusol.ml import splits

SEED = 42
# Hiperparámetros finales (Optuna, 25 trials, GroupKFold espacial)
HP = dict(
    n_estimators=400, learning_rate=0.0235, num_leaves=113, min_child_samples=233,
    subsample=0.794, colsample_bytree=0.501, reg_lambda=3.0,
    random_state=SEED, n_jobs=-1, verbose=-1,
)
SALIDA = str(Path("models") / "radiacion" / "v1")


def entrenar(device: str = "cpu") -> tuple:
    """Construye features del train y entrena LightGBM A0. Devuelve (modelo, train_feat)."""
    sp = splits.hacer_splits()
    tr = feat.build_features(sp["train"])
    print(f"Train (features, horas de sol): {len(tr):,} filas | "
          f"{tr['CodigoEstacion'].nunique()} estaciones")

    hp = dict(HP)
    if device == "gpu":
        hp["device"] = "gpu"
    X = feat.matriz_X(tr)
    y = tr[feat.OBJETIVO].astype("float32")

    t0 = time.time()
    try:
        modelo = lgb.LGBMRegressor(**hp).fit(X, y)
    except Exception as e:  # p. ej. LightGBM sin soporte GPU
        print(f"GPU no disponible ({e}); usando CPU.")
        hp.pop("device", None)
        modelo = lgb.LGBMRegressor(**hp).fit(X, y)
    print(f"Entrenado en {time.time()-t0:.0f}s")
    return modelo, tr


def guardar(modelo, tr) -> None:
    carpeta = Path(SALIDA)
    carpeta.mkdir(parents=True, exist_ok=True)
    modelo.booster_.save_model(str(carpeta / "modelo_lgbm.txt"))

    imp = dict(zip(feat.FEATURES,
                   (modelo.feature_importances_ / modelo.feature_importances_.sum()).round(4).tolist()))
    meta = {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "modelo": "LightGBM", "variante": "A0 (17 features)",
        "formato": "lightgbm-txt",
        "target": "residuo = Valor - ALLSKY; prediccion = ALLSKY + residuo; clip>=0",
        "features": feat.FEATURES,
        "hiperparametros": HP,
        "decisiones": {
            "preliminares": "excluidos (A/B: +5.5% MAE mensual)",
            "split": "1b estratificado por altitud, test temporal >=2021, holdout espacial 20%",
            "seleccion_familia": "LightGBM (empate con XGBoost/RF, elegido por velocidad)",
            "ablacion": "A0 gana: lat/lon aportan +3% mensual en estaciones nuevas",
        },
        "train": {"filas": int(len(tr)), "estaciones": int(tr["CodigoEstacion"].nunique())},
        "importancia_features": imp,
        "versiones": {"python": platform.python_version(), "lightgbm": lgb.__version__,
                      "sklearn": sklearn.__version__, "numpy": np.__version__},
        "seed": SEED,
    }
    (carpeta / "metadatos.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Artefacto y metadatos guardados en: {carpeta}")


def main() -> None:
    p = argparse.ArgumentParser(description="Entrena el modelo final de corrección de radiación.")
    p.add_argument("--device", default="cpu", choices=["cpu", "gpu"])
    args = p.parse_args()
    modelo, tr = entrenar(args.device)
    guardar(modelo, tr)
    print("\nModelo entrenado. Evalúa con: python -m calcusol.ml.evaluate")


if __name__ == "__main__":
    main()
