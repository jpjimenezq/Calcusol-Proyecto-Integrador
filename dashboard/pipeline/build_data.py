"""Pipeline de datos del dashboard Calcusol (desde un parquet local).

Lee dataset_modelo.parquet, aplica el modelo de corrección (XGBoost) a la radiación satelital y exporta un JSON
compacto con la climatología mes x hora por estación, que es todo lo que el dashboard necesita para simular
energía, autoconsumo, ahorro, Payback y VPN. Por defecto incluye todos los departamentos de Colombia.

Uso:  python pipeline/build_data.py                         # toda Colombia
      python pipeline/build_data.py --departamento Antioquia  # solo un departamento

Con los datos de AWS (Athena) se usa build_data_aws.py; sin ninguno de los dos, build_demo_data.py.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import formato

ROOT = formato.ROOT
PARQUET = ROOT / "dataset_modelo.parquet"
MODELO = ROOT / "modelo_radiacion_calcusol.joblib"

FEATURES = [
    "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M",
    "Latitud", "Longitud", "Altitud",
    "Hora_sin", "Hora_cos", "Mes_sin", "Mes_cos", "Dia_sin", "Dia_cos",
]


def cargar(departamento: str | None = None) -> pd.DataFrame:
    import duckdb  # solo hace falta con el parquet real
    if departamento:
        return duckdb.execute("SELECT * FROM read_parquet(?) WHERE Departamento = ?",
                              [str(PARQUET), departamento]).df()
    return duckdb.execute("SELECT * FROM read_parquet(?)", [str(PARQUET)]).df()


def corregir(df: pd.DataFrame) -> np.ndarray:
    """Radiación corregida con el modelo ML (W/m2), sin valores negativos."""
    import joblib
    modelo = joblib.load(MODELO)
    x = df[FEATURES[:7]].copy()
    for nombre, col, periodo in (("Hora", "Hora", 24), ("Mes", "Mes", 12), ("Dia", "DiaDelAnio", 365)):
        ang = 2 * np.pi * df[col] / periodo
        x[f"{nombre}_sin"] = np.sin(ang)
        x[f"{nombre}_cos"] = np.cos(ang)
    assert list(modelo.feature_names_in_) == FEATURES, "El modelo espera otras variables"
    return np.clip(modelo.predict(x[FEATURES]), 0, None)


def main(departamento: str | None = None) -> None:
    df = cargar(departamento)
    df["ML"] = corregir(df)
    df = df[df["Hora"].isin(formato.HORAS)].rename(columns={"Valor": "IDEAM", "ALLSKY_SFC_SW_DWN": "NASA"})
    df["Anio"] = df["Fecha"].dt.year

    deptos = formato.departamentos()
    estaciones = formato.estaciones_desde(df, deptos)
    meta = {
        "departamento": departamento or "Colombia", "registros": int(len(df)),
        "desde": int(df["Anio"].min()), "hasta": int(df["Anio"].max()),
        "sesgo_nasa": round(float(df["NASA"].sum() / df["IDEAM"].sum() - 1), 4),
        "rmse_nasa": formato.rmse(df["NASA"], df["IDEAM"]), "rmse_ml": formato.rmse(df["ML"], df["IDEAM"]),
    }
    salida = formato.armar_salida(estaciones, deptos, meta)
    ruta = formato.escribir(salida)
    m = salida["meta"]
    print(f"{ruta.relative_to(ROOT)}: {m['estaciones']} estaciones en {m['departamentos']} departamentos, "
          f"{m['registros']:,} registros ({m['desde']}-{m['hasta']}), sesgo NASA {m['sesgo_nasa']:+.1%}, "
          f"RMSE NASA {m['rmse_nasa']} -> ML {m['rmse_ml']} W/m2, {ruta.stat().st_size / 1024:.0f} kB")
    for e in salida["estaciones"]:
        print(f"  {e['departamento'][:18]:<18} {e['nombre']:<26} {e['alt']:>5} m  {e['desde']}-{e['hasta']}  "
              f"n={e['registros']:>6}  sesgo NASA {e['sesgo_nasa']:+.1%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--departamento", help="solo este departamento (por defecto, todos)")
    main(ap.parse_args().departamento)
