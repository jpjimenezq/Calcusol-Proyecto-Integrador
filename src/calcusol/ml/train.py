"""
ML — Entrenamiento del modelo final (R1).

Entrena el modelo de corrección de radiación sobre el split de entrenamiento
(solo definitivos, años <= 2020) y guarda el artefacto portable + metadatos.

Modelo: LightGBM, variante A0 (17 features), target = residuo (Valor - ALLSKY).
Hiperparámetros: los hallados por Optuna (búsqueda contra validación espacial).
Selección de familia: LightGBM ganó por velocidad en empate técnico con XGBoost
y Random Forest (ver docs/modelo.md).

Corre en Docker local (CPU), en un contenedor de AWS (Fargate) o donde se pida:
la lógica es la misma. Para acelerar en GPU, usar --device gpu (requiere LightGBM
con soporte GPU; en CPU el resultado es reproducible).

Cada ejecución crea una versión NUEVA (v2, v3...) en el registro; nunca sobrescribe
una existente (v1 incluida) ni cambia el modelo activo. Promover es otro paso
(ml/promocion.py).

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.ml.train
  ... python -m calcusol.ml.train --version v2 --gold s3://calcusol-datos-eafit/gold/dataset         --salida s3://calcusol-datos-eafit/modelos
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import sklearn

from calcusol.config import settings
from calcusol.ml import features as feat
from calcusol.ml import registro as reg
from calcusol.ml import splits

SEED = 42
# Hiperparámetros finales (Optuna, 25 trials, GroupKFold espacial)
HP = dict(
    n_estimators=400, learning_rate=0.0235, num_leaves=113, min_child_samples=233,
    subsample=0.794, colsample_bytree=0.501, reg_lambda=3.0,
    random_state=SEED, n_jobs=-1, verbose=-1,
)


def entrenar(device: str = "cpu", ruta_gold: str | None = None) -> tuple:
    """Construye features del train y entrena LightGBM A0. Devuelve (modelo, train_feat, estaciones_holdout)."""
    sp = splits.hacer_splits(ruta_gold)
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
    return modelo, tr, sorted(int(x) for x in sp["test_spatial"]["CodigoEstacion"].unique())


def _commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           cwd=settings.REPO_ROOT, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return os.environ.get("GIT_COMMIT")


def guardar(modelo, tr, version: str, registro: reg.Registro, gold: dict, holdout: list[int]) -> dict:
    """Escribe la versión en el registro (local o S3). Falla si la versión ya existe."""
    if registro.existe_version(version):
        raise reg.VersionExistente(f"{version} ya existe en {registro.raiz}; no se sobrescribe.")
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        modelo.booster_.save_model(str(carpeta / "modelo_lgbm.txt"))
        sha = reg.sha256_bytes((carpeta / "modelo_lgbm.txt").read_bytes().replace(b"\r\n", b"\n"))

        imp = dict(zip(feat.FEATURES,
                       (modelo.feature_importances_ / modelo.feature_importances_.sum()).round(4).tolist()))
        meta = {
            "version": version, "estado": "candidato",
            "generado_utc": reg.ahora_utc(),
            "modelo": "LightGBM", "variante": "A0 (17 features)",
            "formato": "lightgbm-txt", "modelo_sha256": sha,
            "target": "residuo = Valor - ALLSKY; prediccion = ALLSKY + residuo; clip>=0",
            "features": feat.FEATURES,
            "hiperparametros": HP,
            **gold,
            "commit": _commit(),
            "decisiones": {
                "preliminares": "excluidos (A/B: +5.5% MAE mensual)",
                "split": "1b estratificado por altitud, test temporal >=2021, holdout espacial 20%",
                "seleccion_familia": "LightGBM (empate con XGBoost/RF, elegido por velocidad)",
                "ablacion": "A0 gana: lat/lon aportan +3% mensual en estaciones nuevas",
            },
            "train": {"filas": int(len(tr)), "estaciones": int(tr["CodigoEstacion"].nunique()),
                      "anio_max": int(tr["year"].max()), "estaciones_holdout": holdout},
            "importancia_features": imp,
            "versiones": {"python": platform.python_version(), "lightgbm": lgb.__version__,
                          "sklearn": sklearn.__version__, "numpy": np.__version__},
            "seed": SEED,
        }
        (carpeta / "metadatos.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        registro.publicar_version(version, carpeta)
    print(f"Versión {version} guardada en: {registro.raiz}/{registro.clave(version)}")
    return meta


def main() -> None:
    p = argparse.ArgumentParser(description="Entrena una versión nueva del modelo de corrección de radiación.")
    p.add_argument("--device", default="cpu", choices=["cpu", "gpu"])
    p.add_argument("--version", help="p. ej. v2 (por defecto, la siguiente libre). Nunca sobrescribe.")
    p.add_argument("--gold", help="ruta del Gold: local o s3://... (por defecto, el Gold de CALCUSOL_DATA_ROOT)")
    p.add_argument("--salida", help="raíz del registro: local o s3://... (por defecto, CALCUSOL_MODELOS_ROOT o ./models)")
    args = p.parse_args()

    registro = reg.Registro(args.salida)
    version = args.version or registro.siguiente_version()
    if registro.existe_version(version):
        raise SystemExit(f"{version} ya existe en {registro.raiz}. Elija otra versión (la siguiente libre es "
                         f"{registro.siguiente_version()}).")
    ruta_gold = args.gold or splits.DATASET
    gold = reg.huella_gold(ruta_gold)
    print(f"Gold: {gold['gold_version']} ({gold['gold_archivos']} archivos) | versión a crear: {version}")
    modelo, tr, holdout = entrenar(args.device, ruta_gold)
    guardar(modelo, tr, version, registro, gold, holdout)
    print(f"\nVersión {version} entrenada (candidata; el modelo activo no cambió). "
          f"Evalúa con: python -m calcusol.ml.evaluate --version {version} --guardar")


if __name__ == "__main__":
    main()
