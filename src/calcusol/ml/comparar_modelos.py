"""
ML — Comparación de familias de modelos (R1).

Evidencia reproducible de por qué se eligió LightGBM. Compara, con EXACTAMENTE las mismas variables (las 17 de
ml/features.py), el mismo objetivo (residuo = Valor - ALLSKY) y la misma validación cruzada agrupada por Sitio
(5 pliegues, solo años <= 2020, el test final no se toca), estas familias:

  B-0  NASA sin corregir (residuo = 0)       B-1  calibración lineal global (y = a + b·ALLSKY)
  Ridge · Lasso (lineales)  ·  Random Forest · XGBoost · LightGBM (ensambles de árboles)

Por pliegue calcula MAE horario, MAE mensual (la métrica de negocio), RMSE, sesgo (MBE) y la mejora frente a NASA.
Reporta el promedio y la desviación entre pliegues: si dos modelos difieren menos que esa desviación, es un empate.

Los modelos usan ajustes fijos y declarados (columna `parametros` del CSV). Los hiperparámetros de LightGBM son los
del modelo final (ml/train.py); la búsqueda que los justifica está en ml/buscar_hiperparametros.py.

EJECUCIÓN (xgboost es opcional: pip install xgboost):
  python -m calcusol.ml.comparar_modelos                  # completo (largo: varias horas con Random Forest)
  python -m calcusol.ml.comparar_modelos --muestra 0.2    # entrena con el 20 % de las filas de cada pliegue
  python -m calcusol.ml.comparar_modelos --modelos LightGBM Ridge
  Con más RAM / GPU (Colab): notebooks/comparacion_modelos.ipynb, que usa ejecutar() de este módulo.
Salida: reports/ml_comparacion/comparacion_modelos.csv y comparacion_por_pliegue.csv
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Lasso, LinearRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from calcusol.ml import features as feat
from calcusol.ml import splits
from calcusol.ml.train import HP as HP_LIGHTGBM
from calcusol.rutas import PARQUET_MODELO, RAIZ

SEED = 42
SALIDA = RAIZ / "reports" / "ml_comparacion"
USAR_GPU = False     # True: XGBoost usa la GPU (CUDA). LightGBM y Random Forest siguen en CPU (más RAM y núcleos ayudan)


# ---------------------------------------------------------------- modelos
def _lightgbm():
    import lightgbm as lgb
    return lgb.LGBMRegressor(**HP_LIGHTGBM)


def _xgboost():
    from xgboost import XGBRegressor
    return XGBRegressor(n_estimators=400, learning_rate=0.05, max_depth=8, subsample=0.8, colsample_bytree=0.5,
                        reg_lambda=3.0, tree_method="hist", n_jobs=-1, random_state=SEED,
                        device="cuda" if USAR_GPU else "cpu")


def _random_forest():
    # max_samples limita cada árbol a una parte de las filas: sin eso, con 2 millones de filas es impracticable
    return RandomForestRegressor(n_estimators=100, min_samples_leaf=50, max_features=0.5, max_samples=0.25,
                                 n_jobs=-1, random_state=SEED)


MODELOS = {
    "Ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
    "Lasso": lambda: make_pipeline(StandardScaler(), Lasso(alpha=0.1, max_iter=500)),
    "Random Forest": _random_forest,
    "XGBoost": _xgboost,
    "LightGBM": _lightgbm,
}


# ---------------------------------------------------------------- datos y métricas
def preparar_datos(ruta: str | None = None):
    """Devuelve (X, residuo, base, valor, grupos_mes, pliegues) sobre las horas de sol de los años <= 2020.

    `pliegues` es una lista de (posiciones_train, posiciones_test) agrupadas por Sitio (GroupKFold)."""
    df, pliegues = splits.folds_espaciales_por_sitio(ruta or str(PARQUET_MODELO))
    f = feat.build_features(df)                       # solo horas de sol; conserva el índice de `df`
    X = feat.matriz_X(f).reset_index(drop=True)
    pos = pd.Series(np.arange(len(f)), index=f.index)  # índice de df -> posición en X
    pl = []
    for i_tr, i_te in pliegues:
        a = pos.reindex(df.index[i_tr]).dropna().astype(int).to_numpy()
        b = pos.reindex(df.index[i_te]).dropna().astype(int).to_numpy()
        pl.append((a, b))
    base = f["ALLSKY_SFC_SW_DWN"].to_numpy()
    valor = f["Valor"].to_numpy()
    claves = (f["CodigoEstacion"].astype(str) + "|" + f["year"].astype(str) + "|" + f["Mes"].astype(str)).to_numpy()
    return X, (valor - base).astype("float32"), base, valor, claves, pl


def metricas(pred, valor, claves) -> dict:
    """MAE/RMSE/MBE por hora y MAE mensual (promedio por estación-año-mes, como en la evaluación de R2)."""
    e = pred - valor
    m = pd.DataFrame({"k": claves, "p": pred, "v": valor}).groupby("k").mean()
    return {"MAE_hora": float(np.abs(e).mean()), "RMSE_hora": float(np.sqrt((e ** 2).mean())),
            "MBE_hora": float(e.mean()), "MAE_mes": float((m.p - m.v).abs().mean())}


def evaluar(nombre, fabrica, datos, muestra: float) -> list[dict]:
    X, residuo, base, valor, claves, pliegues = datos
    rng = np.random.default_rng(SEED)
    filas = []
    for k, (i_tr, i_te) in enumerate(pliegues, 1):
        if nombre == "B-0 NASA sin corregir":
            pred = base[i_te]
            t = 0.0
        else:
            if muestra < 1.0:
                i_tr = rng.choice(i_tr, size=int(len(i_tr) * muestra), replace=False)
            t0 = time.time()
            if nombre == "B-1 recta":
                m = LinearRegression().fit(base[i_tr].reshape(-1, 1), valor[i_tr])
                pred = m.predict(base[i_te].reshape(-1, 1))
            else:
                m = fabrica().fit(X.iloc[i_tr], residuo[i_tr])
                pred = base[i_te] + m.predict(X.iloc[i_te])
            t = time.time() - t0
        pred = np.clip(pred, 0, None)
        r = metricas(pred, valor[i_te], claves[i_te])
        r0 = metricas(base[i_te], valor[i_te], claves[i_te])
        filas.append({"modelo": nombre, "pliegue": k, **r, "segundos": round(t, 1),
                      "mejora_hora_pct": 100 * (1 - r["MAE_hora"] / r0["MAE_hora"]),
                      "mejora_mes_pct": 100 * (1 - r["MAE_mes"] / r0["MAE_mes"])})
        print(f"  {nombre:<22} pliegue {k}/{len(pliegues)}: MAE hora {r['MAE_hora']:.2f} | "
              f"MAE mes {r['MAE_mes']:.2f} | {t:.0f}s", flush=True)
    return filas


def ejecutar(muestra: float = 1.0, modelos: list[str] | None = None, ruta: str | None = None, gpu: bool = False,
             salida: Path | None = None) -> pd.DataFrame:
    """Corre la comparación completa (la usan la línea de comandos y el notebook) y guarda los CSV."""
    global USAR_GPU
    USAR_GPU = gpu
    assert 0 < muestra <= 1, "muestra debe estar en (0, 1]"
    modelos = modelos or list(MODELOS)
    salida = Path(salida or SALIDA)

    datos = preparar_datos(ruta)
    print(f"Datos: {len(datos[0]):,} horas de sol | {len(datos[5])} pliegues por Sitio | muestra {muestra:.0%} | "
          f"GPU para XGBoost: {gpu}", flush=True)
    todo = []
    for nombre in ["B-0 NASA sin corregir", "B-1 recta"] + modelos:
        todo += evaluar(nombre, MODELOS.get(nombre), datos, muestra)
        salida.mkdir(parents=True, exist_ok=True)   # guarda el avance: si Colab se desconecta no se pierde lo ya corrido
        pd.DataFrame(todo).round(4).to_csv(salida / "comparacion_por_pliegue_parcial.csv", index=False)
    p = pd.DataFrame(todo)

    orden = list(dict.fromkeys(p.modelo))
    res = p.groupby("modelo").agg(**{c + sufijo: (c, f) for c in
                                     ("MAE_hora", "MAE_mes", "RMSE_hora", "MBE_hora", "mejora_hora_pct", "mejora_mes_pct", "segundos")
                                     for sufijo, f in (("", "mean"), ("_desv", "std"))}).loc[orden].round(3)
    res.insert(0, "fraccion_entrenamiento", muestra)
    res["parametros"] = [("-" if n.startswith("B-") else str(MODELOS[n]().get_params(deep=False))[:300]) for n in res.index]
    salida.mkdir(parents=True, exist_ok=True)
    p.round(4).to_csv(salida / "comparacion_por_pliegue.csv", index=False)
    res.to_csv(salida / "comparacion_modelos.csv")
    print(f"\nGuardado en {salida}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Compara familias de modelos con validación cruzada por Sitio.")
    ap.add_argument("--muestra", type=float, default=1.0, help="fracción de las filas de entrenamiento de cada pliegue (0-1]")
    ap.add_argument("--modelos", nargs="+", default=list(MODELOS), choices=list(MODELOS))
    ap.add_argument("--ruta", default=None, help="parquet de datos (por defecto data/gold/dataset_modelo.parquet)")
    ap.add_argument("--gpu", action="store_true", help="XGBoost en GPU (CUDA)")
    a = ap.parse_args()
    res = ejecutar(a.muestra, a.modelos, a.ruta, a.gpu)
    print("\n" + res[["MAE_hora", "MAE_hora_desv", "MAE_mes", "MAE_mes_desv", "mejora_mes_pct"]].to_string())


if __name__ == "__main__":
    main()
