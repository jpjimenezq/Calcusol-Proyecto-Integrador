"""ML — El modelo de R1 como regresor de scikit-learn, sin archivos intermedios.

El modelo se guarda en formato nativo de LightGBM (modelo_lgbm.txt). La validación (R2) y R3 lo necesitan
como `LGBMRegressor` (con .predict, .get_params y clone, para reentrenar con los mismos ajustes).
`cargar_regresor` lo arma en memoria desde el .txt; no hace falta convertirlo a .joblib.

Los hiperparámetros salen de metadatos.json (al lado del .txt) y, si no está, del final del propio .txt.
Si no se recuperan, clone() reentrenaría con los valores por defecto de LightGBM (100 árboles, tasa 0.1),
que no son los del modelo: por eso se exige que existan.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import lightgbm as lgb

from calcusol.rutas import MODELO_TXT

# nombre en el .txt -> nombre en LGBMRegressor
_DEL_TXT = {"num_iterations": "n_estimators", "learning_rate": "learning_rate", "num_leaves": "num_leaves",
            "min_data_in_leaf": "min_child_samples", "bagging_fraction": "subsample",
            "feature_fraction": "colsample_bytree", "lambda_l2": "reg_lambda"}


def _hiperparametros(ruta: Path) -> dict:
    meta = ruta.with_name("metadatos.json")
    if meta.exists():
        hp = json.loads(meta.read_text(encoding="utf-8")).get("hiperparametros")
        if hp:
            return dict(hp)
    bloque = re.search(r"^parameters:\n(.*?)^end of parameters", ruta.read_text(encoding="utf-8"), re.S | re.M)
    if not bloque:
        raise ValueError(f"No se encontraron los hiperparámetros de {ruta} (ni metadatos.json ni bloque 'parameters').")
    valores = dict(re.findall(r"\[(\w+): ([^\]]*)\]", bloque.group(1)))
    hp = {destino: float(valores[origen]) for origen, destino in _DEL_TXT.items() if origen in valores}
    hp["n_estimators"] = int(hp["n_estimators"]); hp["num_leaves"] = int(hp["num_leaves"])
    hp["min_child_samples"] = int(hp["min_child_samples"])
    return hp


def cargar_regresor(ruta: str | Path | None = None) -> lgb.LGBMRegressor:
    """Abre el modelo de R1 como LGBMRegressor. Acepta .txt (nativo) o .joblib."""
    ruta = Path(ruta or MODELO_TXT)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el modelo: {ruta}")
    if ruta.suffix.lower() == ".joblib":
        return joblib.load(ruta)
    booster = lgb.Booster(model_file=str(ruta))
    modelo = lgb.LGBMRegressor(**_hiperparametros(ruta))
    modelo._Booster = booster
    modelo._n_features = modelo._n_features_in = booster.num_feature()
    modelo._n_features_in_ = booster.num_feature()
    modelo._feature_names = booster.feature_name()
    modelo._objective = booster.params.get("objective", "regression")
    modelo.fitted_ = True
    return modelo


if __name__ == "__main__":      # opcional: guardar una copia .joblib  (python -m calcusol.ml.modelo salida.joblib)
    import sys
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else MODELO_TXT.with_suffix(".joblib")
    joblib.dump(cargar_regresor(), destino, compress=3)
    print(f"Guardado: {destino}")
