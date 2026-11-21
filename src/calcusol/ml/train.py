"""
ML — Entrenamiento y selección de modelos (R1-2 / R1-4).

Compara las familias de modelos del plan con DOS esquemas de validación sin
fuga (rúbrica SI7009), selecciona el mejor por validación, lo reentrena con todo
el train y lo guarda en formato portable + metadatos.

Modelos: Ridge, Lasso, Random Forest, XGBoost, LightGBM.
Validación:
  - Espacial: GroupKFold por Sitio (generaliza a estaciones no vistas).
  - Temporal: por año, entrena pasado y valida el año siguiente.
Selección: por MAE de la validación ESPACIAL (no se mira el test).
Métrica principal: MAE horario (Wh/m²).

Reproducible en Docker y en Colab: solo cambia --dataset y --device. No importa
nada de Colab. El modelo se guarda en JSON portable (XGBoost/LightGBM) o joblib
(sklearn), con un metadatos.json con features, métricas, semilla y versiones.

EJECUCIÓN (local Docker, CPU, con muestra para iterar):
  docker compose run --rm calcusol uv run python -m calcusol.ml.train --muestra 0.2
EJECUCIÓN (Colab GPU, dataset en Drive):
  python -m calcusol.ml.train --dataset /content/dataset_modelo.parquet --device cuda
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Lasso, Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from calcusol.config import settings
from calcusol.ml import features as feat

SEMILLA = 42
OBJETIVO = feat.OBJETIVO


# ================================================================== Datos
def cargar_dataset(ruta: str | None, muestra: float | None) -> pd.DataFrame:
    """Lee el Gold (particionado o archivo único), añade Anio y cíclicas."""
    ruta = ruta or settings.gold_path("dataset")
    cols = feat.X_DIRECTAS + ["Hora", "Mes", "DiaDelAnio", OBJETIVO, "Sitio", "Fecha"]
    df = pd.read_parquet(ruta, columns=[c for c in cols])
    if muestra and 0 < muestra < 1:
        df = df.sample(frac=muestra, random_state=SEMILLA).reset_index(drop=True)
    df["Anio"] = pd.to_datetime(df["Fecha"]).dt.year
    df = feat.agregar_ciclicas(df)
    return df


# ================================================================== Modelos
def definir_modelos(device: str, con_rf: bool) -> dict:
    """Las familias del plan. XGBoost usa device (cpu/cuda)."""
    import lightgbm as lgb
    import xgboost as xgb

    modelos = {
        "Ridge": make_pipeline(StandardScaler(), Ridge(alpha=1.0, random_state=SEMILLA)),
        "Lasso": make_pipeline(StandardScaler(), Lasso(alpha=0.1, max_iter=2000, random_state=SEMILLA)),
        "XGBoost": xgb.XGBRegressor(
            n_estimators=300, learning_rate=0.05, max_depth=8,
            random_state=SEMILLA, tree_method="hist", device=device,
        ),
        "LightGBM": lgb.LGBMRegressor(
            n_estimators=300, learning_rate=0.05, random_state=SEMILLA,
            n_jobs=-1, verbosity=-1,
        ),
    }
    if con_rf:
        # Contenido en RAM: submuestreo por árbol y profundidad acotada.
        modelos["RandomForest"] = RandomForestRegressor(
            n_estimators=80, max_depth=14, max_samples=0.25,
            random_state=SEMILLA, n_jobs=-1,
        )
    return modelos


# ================================================================== Validación
def cv_espacial(modelo, X, y, grupos, n_splits=5) -> float:
    """MAE promedio en GroupKFold por Sitio."""
    from sklearn.model_selection import GroupKFold

    gkf = GroupKFold(n_splits=n_splits)
    maes = []
    for tr, va in gkf.split(X, y, groups=grupos):
        m = clone(modelo)
        m.fit(X.iloc[tr], y[tr])
        maes.append(mean_absolute_error(y[va], m.predict(X.iloc[va])))
        del m
        gc.collect()
    return float(np.mean(maes))


def cv_temporal(modelo, X, y, anios, n_splits=5) -> float:
    """MAE promedio validando año por año (entrena pasado, valida siguiente)."""
    cortes = sorted(np.unique(anios))[-n_splits:]
    idx = np.arange(len(y))
    maes = []
    for anio_val in cortes:
        tr = idx[anios < anio_val]
        va = idx[anios == anio_val]
        if not len(tr) or not len(va):
            continue
        m = clone(modelo)
        m.fit(X.iloc[tr], y[tr])
        maes.append(mean_absolute_error(y[va], m.predict(X.iloc[va])))
        del m
        gc.collect()
    return float(np.mean(maes)) if maes else float("nan")


# ================================================================== Persistencia
def guardar(mejor_nombre, modelo, resultados, X_cols, ruta_salida, device) -> None:
    carpeta = Path(ruta_salida)
    carpeta.mkdir(parents=True, exist_ok=True)

    import lightgbm as lgb
    import sklearn
    import xgboost as xgb

    # Formato portable según familia.
    if isinstance(modelo, xgb.XGBRegressor):
        modelo.get_booster().save_model(str(carpeta / "modelo.json"))
        formato = "xgboost-json"
    elif isinstance(modelo, lgb.LGBMRegressor):
        modelo.booster_.save_model(str(carpeta / "modelo.txt"))
        formato = "lightgbm-txt"
    else:
        import joblib
        joblib.dump(modelo, carpeta / "modelo.joblib")
        formato = "sklearn-joblib"

    metadatos = {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "mejor_modelo": mejor_nombre,
        "formato": formato,
        "features": X_cols,
        "objetivo": OBJETIVO,
        "semilla": SEMILLA,
        "device_entrenamiento": device,
        "resultados_validacion": resultados,
        "versiones": {
            "python": platform.python_version(),
            "sklearn": sklearn.__version__,
            "xgboost": xgb.__version__,
            "lightgbm": lgb.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    (carpeta / "metadatos.json").write_text(
        json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nModelo ({formato}) y metadatos guardados en: {carpeta}")


# ================================================================== Main
def main() -> None:
    p = argparse.ArgumentParser(description="Entrenamiento y selección de modelos.")
    p.add_argument("--dataset", default=None, help="Ruta del Gold (por defecto data/gold/dataset).")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    p.add_argument("--muestra", type=float, default=None, help="Fracción 0-1 para iterar rápido.")
    p.add_argument("--sin-rf", action="store_true", help="Omite Random Forest (ahorra RAM).")
    p.add_argument("--salida", default=str(Path("models") / "radiacion" / "v1"))
    args = p.parse_args()

    print(f"Cargando dataset (device={args.device}, muestra={args.muestra})...")
    df = cargar_dataset(args.dataset, args.muestra)
    train = df[df["Anio"] <= 2019].reset_index(drop=True)
    print(f"Train: {len(train):,} filas | sitios: {train['Sitio'].nunique()}")

    X = feat.matriz_X(train)
    y = feat.vector_y(train)
    grupos = train["Sitio"].to_numpy()
    anios = train["Anio"].to_numpy()

    modelos = definir_modelos(args.device, con_rf=not args.sin_rf)
    resultados = {}

    print("\n" + "=" * 60)
    print("VALIDACIÓN (espacial GroupKFold + temporal por año)")
    print("=" * 60)
    for nombre, modelo in modelos.items():
        t0 = time.time()
        mae_esp = cv_espacial(modelo, X, y, grupos)
        mae_tmp = cv_temporal(modelo, X, y, anios)
        resultados[nombre] = {"mae_espacial": mae_esp, "mae_temporal": mae_tmp}
        print(f"  {nombre:<13} MAE espacial={mae_esp:.3f} | temporal={mae_tmp:.3f} "
              f"| {time.time()-t0:.1f}s")
        gc.collect()

    # Selección por validación espacial (sin mirar el test).
    mejor = min(resultados, key=lambda k: resultados[k]["mae_espacial"])
    print(f"\nMejor modelo (MAE espacial): {mejor} "
          f"({resultados[mejor]['mae_espacial']:.3f} Wh/m²)")

    print(f"Reentrenando {mejor} con todo el train...")
    modelo_final = clone(modelos[mejor])
    modelo_final.fit(X, y)

    guardar(mejor, modelo_final, resultados, feat.FEATURES, args.salida, args.device)
    print("\nNo se tocó el test (>= 2020); la evaluación final es ml/evaluate.py.")


if __name__ == "__main__":
    main()
