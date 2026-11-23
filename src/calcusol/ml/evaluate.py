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

`evaluar_modelos` evalúa varios modelos (candidato y activo) sobre EXACTAMENTE
las mismas filas, requisito para compararlos de forma justa (ver ml/promocion.py).

EJECUCIÓN (tras entrenar):
  docker compose run --rm calcusol uv run python -m calcusol.ml.evaluate
  ... python -m calcusol.ml.evaluate --version v2 --guardar     # escribe metricas.json de v2
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from calcusol.ml import features as feat
from calcusol.ml import registro as reg
from calcusol.ml import splits
from calcusol.ml.predict import CorrectorRadiacion, cargar

EXAMENES = ("test_temporal", "test_spatial", "test_spatiotemporal")


def _metricas(y, p):
    err = y - p
    ss_tot = np.sum((y - y.mean()) ** 2)
    return {
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err ** 2))),
        "R2": float(1 - np.sum(err ** 2) / ss_tot) if ss_tot else float("nan"),
        "sesgo_%": float(100 * (p.mean() - y.mean()) / y.mean()),
    }


def _mensual(d: pd.DataFrame, pred) -> pd.DataFrame:
    d = d.assign(pred=pred, f=pd.to_datetime(d["Fecha"]))
    g = d.groupby(["CodigoEstacion", d["f"].dt.to_period("M")]).agg(y=("Valor", "mean"), p=("pred", "mean"))
    return g.reset_index()


def _niveles(d: pd.DataFrame, pred) -> dict:
    d = d.assign(pred=pred, f=pd.to_datetime(d["Fecha"]))
    out = {"hora": _metricas(d["Valor"].to_numpy(), pred)}
    for niv, per in [("dia", d["f"].dt.date), ("mes", d["f"].dt.to_period("M"))]:
        g = d.groupby(["CodigoEstacion", per]).agg(y=("Valor", "mean"), p=("pred", "mean"))
        out[niv] = _metricas(g["y"].to_numpy(), g["p"].to_numpy())
    return out


def _pred(corrector: CorrectorRadiacion, d: pd.DataFrame, X) -> np.ndarray:
    return np.clip(d["ALLSKY_SFC_SW_DWN"].to_numpy() + corrector.booster.predict(X), 0, None)


def _examenes(ruta_gold: str | None) -> dict[str, pd.DataFrame]:
    sp = splits.hacer_splits(ruta_gold)
    st = splits.split_espacio_temporal(ruta_gold)
    return {"test_temporal": sp["test_temporal"], "test_spatial": sp["test_spatial"],
            "test_spatiotemporal": st["test_spatiotemporal"], "_train": sp["train"]}


def evaluar_modelos(modelos: dict[str, CorrectorRadiacion], ruta_gold: str | None = None):
    """
    Evalúa varios modelos sobre las mismas filas de examen.

    Devuelve (resultados, mensual, fuga):
      resultados[nombre][examen] = {"modelo": niveles, "baseline": niveles, "filas": n}
      mensual[examen]            = DataFrame (estación, mes, y, p_base, p_<nombre>...) para el bootstrap pareado
      fuga                       = veredicto de ml/splits.verificar_sin_fuga + cruces train/test
    """
    ex = _examenes(ruta_gold)
    train = ex.pop("_train")
    resultados: dict[str, dict] = {n: {} for n in modelos}
    mensual: dict[str, pd.DataFrame] = {}
    for examen in EXAMENES:
        d = feat.build_features(ex[examen])
        X = feat.matriz_X(d)
        base = d["ALLSKY_SFC_SW_DWN"].to_numpy()
        tabla = _mensual(d, base).rename(columns={"p": "p_base"})
        for nombre, corrector in modelos.items():
            pred = _pred(corrector, d, X)
            resultados[nombre][examen] = {"modelo": _niveles(d, pred), "baseline": _niveles(d, base),
                                          "filas": int(len(d))}
            tabla[f"p_{nombre}"] = _mensual(d, pred)["p"].to_numpy()
        mensual[examen] = tabla

    v = splits.verificar_sin_fuga(ruta_gold)
    est_train = set(train["CodigoEstacion"].unique())
    fuga = {
        "sin_fuga_espacial": bool(v["sin_fuga_espacial"]),
        "estaciones_en_train_y_test_spatial": int(len(est_train & set(ex["test_spatial"]["CodigoEstacion"].unique()))),
        "sitios_en_train_y_test_spatiotemporal": int(len(
            set(train["Sitio"].unique()) & set(ex["test_spatiotemporal"]["Sitio"].unique()))),
        "n_estaciones": v["n_estaciones"], "n_sitios": v["n_sitios"],
    }
    fuga["veredicto"] = ("OK: sin fuga" if fuga["sin_fuga_espacial"]
                         and fuga["estaciones_en_train_y_test_spatial"] == 0
                         and fuga["sitios_en_train_y_test_spatiotemporal"] == 0 else "FUGA DETECTADA")
    return resultados, mensual, fuga


def _imprimir(resultados: dict) -> None:
    for nombre, r in resultados.items():
        titulo = nombre.upper() + (" (sitios nuevos, años >=2021)" if nombre == "test_spatiotemporal" else "")
        print(f"\n{'='*58}\n{titulo}\n{'='*58}")
        print(f"{'nivel':<6}{'MAE_B0':>9}{'MAE_mod':>9}{'mejora':>8}{'sesgoB0':>9}{'sesgoMod':>9}")
        for niv in ["hora", "dia", "mes"]:
            mb, mm = r["baseline"][niv], r["modelo"][niv]
            print(f"{niv:<6}{mb['MAE']:>9.1f}{mm['MAE']:>9.1f}"
                  f"{100*(1-mm['MAE']/mb['MAE']):>7.1f}%"
                  f"{mb['sesgo_%']:>8.1f}%{mm['sesgo_%']:>8.1f}%")


def evaluar(corrector: CorrectorRadiacion | None = None, ruta_gold: str | None = None,
            verbose: bool = True) -> dict:
    """Compatibilidad: evalúa un solo modelo y devuelve {examen: {modelo, baseline}}."""
    res, _, _ = evaluar_modelos({"m": corrector or cargar()}, ruta_gold)
    r = res["m"]
    if verbose:
        _imprimir(r)
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="Evalúa un modelo en los exámenes de R2-1.")
    ap.add_argument("--version", help="versión del registro (por defecto, el modelo v1 local)")
    ap.add_argument("--registro", default=None, help="raíz del registro (local o s3://...)")
    ap.add_argument("--gold", default=None, help="ruta del Gold (local o s3://...)")
    ap.add_argument("--guardar", action="store_true", help="escribe metricas.json en la versión")
    a = ap.parse_args()

    if a.version:
        r = reg.Registro(a.registro)
        corrector = CorrectorRadiacion(r.descargar_modelo(a.version))
    else:
        r, corrector = None, cargar()
    res, _, fuga = evaluar_modelos({"m": corrector}, a.gold)
    _imprimir(res["m"])
    print(f"\nFuga: {fuga['veredicto']}")
    print("Cifra defendible: test_spatial, nivel mensual (estaciones nunca vistas).")
    if a.guardar:
        if r is None:
            raise SystemExit("--guardar requiere --version")
        meta = r.metadatos(a.version)
        r.escribir_metricas(a.version, {
            "version": a.version, "gold_version": meta.get("gold_version"),
            "evaluado_utc": reg.ahora_utc(), "examenes": res["m"], "fuga": fuga})
        print(f"metricas.json escrito en {a.version}")


if __name__ == "__main__":
    main()
