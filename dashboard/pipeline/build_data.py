"""Pipeline de datos del dashboard Calcusol (desde el parquet local).

Lee data/gold/dataset_modelo.parquet, aplica el modelo de corrección de R1 a la radiación satelital y exporta un JSON
compacto con la climatología mes x hora por estación, que es todo lo que el dashboard necesita para simular
energía, autoconsumo, ahorro, Payback y VPN. Por defecto incluye todos los departamentos de Colombia.

El modelo es el mismo que sirve la API y que usa build_data_aws.py: LightGBM de 17 variables, el archivo
models/radiacion/v1/modelo_lgbm.txt (predice el residuo IDEAM - NASA). Las variables se calculan con
calcusol.ml.features, igual que al entrenar.

Uso:  python pipeline/build_data.py                         # toda Colombia
      python pipeline/build_data.py --departamento Antioquia  # solo un departamento
      python pipeline/build_data.py --solo-validados          # sin los datos preliminares del IDEAM
      python pipeline/build_data.py --validacion 2021 2022    # años con los que se mide el error del modelo

Con los datos de AWS (Athena) se usa build_data_aws.py; sin ninguno de los dos, build_demo_data.py.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import pandas as pd

import formato

ROOT = formato.ROOT
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "src"))

from calcusol.ml.predict import CorrectorRadiacion  # noqa: E402
from calcusol.rutas import MODELO_TXT, PARQUET_MODELO  # noqa: E402

COLUMNAS = ["CodigoEstacion", "NombreEstacion", "Departamento", "Fecha", "Hora", "Mes", "DiaDelAnio", "Latitud",
            "Longitud", "Altitud", "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M", "Valor", "EsPreliminar"]


def cargar(departamento: str | None, preliminares: bool, parquet: Path) -> pd.DataFrame:
    df = pd.read_parquet(parquet, columns=COLUMNAS)
    df = df[df["Valor"].notna()]
    if not preliminares:
        df = df[~df["EsPreliminar"]]
    if departamento:
        df = df[df["Departamento"] == departamento]
    df = df.copy()
    df["Anio"] = pd.to_datetime(df["Fecha"]).dt.year
    return df


def cargar_modelo() -> CorrectorRadiacion:
    """LightGBM no lee el modelo si trae saltos de línea de Windows (\\r\\n): se usa una copia con \\n."""
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "modelo_lgbm.txt"
        copia.write_bytes(Path(MODELO_TXT).read_bytes().replace(b"\r\n", b"\n"))
        return CorrectorRadiacion(str(copia))


def main(departamento: str | None = None, preliminares: bool = True, validacion: tuple[int, int] = (2021, 2022),
         parquet: Path = PARQUET_MODELO) -> None:
    df = cargar(departamento, preliminares, Path(parquet))
    registros_tabla = len(df)
    # El cálculo usa las horas con sol: se descartan las de la madrugada y la noche y, dentro del modelo, las
    # filas con el sol bajo el horizonte.
    df = df[df["Hora"].between(formato.HORAS[0], formato.HORAS[-1])]
    d = cargar_modelo().predecir(df)                                    # radiacion_corregida (W/m2), solo horas de sol
    d = d.rename(columns={"Valor": "IDEAM", "ALLSKY_SFC_SW_DWN": "NASA", "radiacion_corregida": "ML"})

    deptos = formato.departamentos()
    estaciones = formato.estaciones_desde(d, deptos, validacion)
    v = d[d["Anio"].between(*validacion)]
    v = v if len(v) else d
    meta = {
        "departamento": departamento or "Colombia", "registros": int(registros_tabla), "registros_usados": int(len(d)),
        "desde": int(d["Anio"].min()), "hasta": int(d["Anio"].max()),
        "sesgo_nasa": round(float(v["NASA"].sum() / v["IDEAM"].sum() - 1), 4),
        "rmse_nasa": formato.rmse(v["NASA"], v["IDEAM"]), "rmse_ml": formato.rmse(v["ML"], v["IDEAM"]),
        "modelo": "LightGBM (R1)", "fuente": "Gold v1 (data/gold/dataset_modelo.parquet)",
        "validacion": f"{validacion[0]}–{validacion[1]}", "preliminares": preliminares,
    }
    salida = formato.armar_salida(estaciones, deptos, meta)
    ruta = formato.escribir(salida)
    m = salida["meta"]
    print(f"{ruta.relative_to(ROOT)}: {m['estaciones']} estaciones en {m['departamentos']} departamentos, "
          f"{m['registros']:,} registros en la tabla ({m['registros_usados']:,} con sol, {m['desde']}-{m['hasta']}), "
          f"{ruta.stat().st_size / 1024:.0f} kB")
    print(f"Con los años {m['validacion']}: sesgo de NASA {m['sesgo_nasa']:+.1%}, RMSE NASA {m['rmse_nasa']} -> "
          f"modelo {m['rmse_ml']} W/m2")
    for e in salida["estaciones"]:
        print(f"  {e['departamento'][:18]:<18} {e['nombre']:<26} {e['alt']:>5} m  {e['desde']}-{e['hasta']}  "
              f"n={e['registros']:>6}  sesgo NASA {e['sesgo_nasa']:+.1%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--departamento", help="solo este departamento (por defecto, todos)")
    ap.add_argument("--solo-validados", action="store_true",
                    help="excluye las filas marcadas EsPreliminar (por defecto se incluyen, y cada estación "
                         "guarda qué proporción de sus datos es preliminar)")
    ap.add_argument("--validacion", nargs=2, type=int, default=[2021, 2022], metavar=("DESDE", "HASTA"),
                    help="años con los que se mide el error del modelo (los que no vio al entrenar)")
    ap.add_argument("--parquet", default=str(PARQUET_MODELO), help="parquet de datos (por defecto data/gold/dataset_modelo.parquet)")
    a = ap.parse_args()
    main(a.departamento, not a.solo_validados, tuple(a.validacion), Path(a.parquet))
