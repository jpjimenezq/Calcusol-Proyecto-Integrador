"""
ML — Búsqueda de hiperparámetros de LightGBM con Optuna (R1).

Hace ejecutable la búsqueda que justifica los hiperparámetros de ml/train.py. Cada prueba (trial) entrena LightGBM
con unos ajustes y los mide con la MISMA validación cruzada agrupada por Sitio de ml/comparar_modelos.py
(5 pliegues, solo años <= 2020: el test final no se toca). Se minimiza el MAE MENSUAL promedio, que es la métrica de
negocio (el Payback depende de la energía acumulada).

Espacio de búsqueda (todo lo demás queda fijo como en train.py):
  n_estimators 200-600 · learning_rate 0.01-0.10 (log) · num_leaves 31-255 · min_child_samples 50-400
  colsample_bytree 0.3-1.0 · reg_lambda 0-10
(subsample no se busca: con subsample_freq = 0, que es el valor del modelo final, no tiene efecto.)

No cambia el modelo: deja los mejores valores en reports/ml_comparacion/hiperparametros_optuna.json para que,
si se adoptan, se copien a HP en ml/train.py y se reentrene.

EJECUCIÓN (optuna es opcional: pip install optuna):
  python -m calcusol.ml.buscar_hiperparametros --trials 25 --muestra 0.25
"""
from __future__ import annotations

import argparse
import json
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

from calcusol.ml.comparar_modelos import SALIDA, SEED, metricas, preparar_datos
from calcusol.ml.train import HP as HP_ACTUALES


def objetivo_factory(datos, muestra: float):
    X, residuo, base, valor, claves, pliegues = datos

    def objetivo(trial) -> float:
        hp = dict(HP_ACTUALES)
        hp.update(
            n_estimators=trial.suggest_int("n_estimators", 200, 600),
            learning_rate=trial.suggest_float("learning_rate", 0.01, 0.10, log=True),
            num_leaves=trial.suggest_int("num_leaves", 31, 255),
            min_child_samples=trial.suggest_int("min_child_samples", 50, 400),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.3, 1.0),
            reg_lambda=trial.suggest_float("reg_lambda", 0.0, 10.0),
        )
        rng = np.random.default_rng(SEED)
        maes = []
        for i_tr, i_te in pliegues:
            if muestra < 1.0:
                i_tr = rng.choice(i_tr, size=int(len(i_tr) * muestra), replace=False)
            m = lgb.LGBMRegressor(**hp).fit(X.iloc[i_tr], residuo[i_tr])
            pred = np.clip(base[i_te] + m.predict(X.iloc[i_te]), 0, None)
            maes.append(metricas(pred, valor[i_te], claves[i_te])["MAE_mes"])
        return float(np.mean(maes))

    return objetivo


def ejecutar(trials: int = 25, muestra: float = 0.25, ruta: str | None = None) -> dict:
    """Corre la búsqueda (la usan la línea de comandos y el notebook) y guarda el resultado."""
    try:
        import optuna
    except ImportError as e:
        raise SystemExit("Falta optuna: pip install optuna") from e
    datos = preparar_datos(ruta)
    print(f"Datos: {len(datos[0]):,} horas de sol | {trials} pruebas | muestra {muestra:.0%}", flush=True)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    estudio = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=SEED))
    t0 = time.time()
    estudio.optimize(objetivo_factory(datos, muestra), n_trials=trials,
                     callbacks=[lambda s, t: print(f"  prueba {t.number + 1}/{trials}: MAE mensual {t.value:.3f} "
                                                   f"(mejor {s.best_value:.3f})", flush=True)])
    SALIDA.mkdir(parents=True, exist_ok=True)
    resultado = {"mejor_MAE_mes": estudio.best_value, "mejores_hiperparametros": estudio.best_params,
                 "hiperparametros_actuales_train_py": {k: HP_ACTUALES[k] for k in estudio.best_params},
                 "pruebas": trials, "fraccion_entrenamiento": muestra, "segundos": round(time.time() - t0),
                 "metrica": "MAE mensual promedio, validación cruzada de 5 pliegues por Sitio, años <= 2020"}
    (SALIDA / "hiperparametros_optuna.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    estudio.trials_dataframe().to_csv(SALIDA / "hiperparametros_optuna_pruebas.csv", index=False)
    return resultado


def main() -> None:
    ap = argparse.ArgumentParser(description="Búsqueda de hiperparámetros de LightGBM con Optuna.")
    ap.add_argument("--trials", type=int, default=25)
    ap.add_argument("--muestra", type=float, default=0.25, help="fracción de las filas de entrenamiento por pliegue")
    ap.add_argument("--ruta", default=None)
    a = ap.parse_args()
    print(json.dumps(ejecutar(a.trials, a.muestra, a.ruta), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
