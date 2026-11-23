"""Datos de DEMOSTRACIÓN de Colombia para el dashboard, cuando no se tienen los datos reales.

Descarga de NASA POWER tres años de radiación horaria en la capital de cada departamento (32 puntos), los corrige
con el modelo de R1 (el mismo que sirve la API en /predict) y escribe dashboard/data/colombia.json con el mismo
formato que build_data.py. Los puntos son capitales, NO estaciones del IDEAM: no hay mediciones, por eso el
archivo lleva meta.demo = true y el dashboard lo avisa en pantalla.

Necesita internet (NASA POWER) y las dependencias del proyecto. Desde la raíz del repositorio:

    python dashboard/pipeline/build_demo_data.py

Para los datos reales use build_data_aws.py (Athena) o build_data.py (parquet); reemplazan este archivo.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import formato

REPO = formato.ROOT.parent
sys.path.insert(0, str(REPO / "src"))

from calcusol.fisica_finanzas import calcusol_r3 as r3  # noqa: E402
from calcusol.ml.predict import CorrectorRadiacion  # noqa: E402

MODELO = REPO / "models" / "radiacion" / "v1" / "modelo_lgbm.txt"
ANIOS = [2021, 2022, 2023]


def cargar_modelo() -> CorrectorRadiacion:
    """LightGBM no lee el modelo si trae saltos de línea de Windows (\\r\\n): se usa una copia con \\n."""
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "modelo_lgbm.txt"
        copia.write_bytes(MODELO.read_bytes().replace(b"\r\n", b"\n"))
        return CorrectorRadiacion(str(copia))


def capitales() -> list[dict]:
    """Las capitales de departamento (los municipios con rango), con su centro y su departamento."""
    fs = json.loads((formato.GEO / "colombia_municipios.geojson").read_text(encoding="utf-8"))["features"]
    caps = [f["properties"] for f in fs if "r" in f["properties"]]
    return sorted(caps, key=lambda p: p["r"])


def main() -> None:
    cfg = r3.cargar_config()
    corrector = cargar_modelo()
    deptos = formato.departamentos()
    estaciones, nasa_todo, ml_todo, filas = [], [], [], 0
    for p in capitales():
        lon, lat = p["centro"]
        print(f"{p['nombre']:<24} {p['depto']:<26} descargando NASA POWER {ANIOS[0]}-{ANIOS[-1]}…", flush=True)
        d = r3.descargar_nasa_punto(lat, lon, ANIOS, cfg)
        d["CodigoEstacion"] = 9000 + p["r"]
        d = corrector.predecir(d)                                  # radiacion_corregida (W/m2), solo horas de sol
        d = d[d["Hora"].isin(formato.HORAS)].rename(columns={"ALLSKY_SFC_SW_DWN": "NASA", "radiacion_corregida": "ML"})
        nasa_todo.append(d["NASA"]); ml_todo.append(d["ML"]); filas += len(d)
        estaciones.append({
            "id": 9000 + p["r"], "nombre": p["nombre"], "departamento": p["depto"],
            "lat": round(lat, 4), "lon": round(lon, 4), "alt": int(round(float(d["Altitud"].iloc[0]))),
            "registros": int(len(d)), "desde": ANIOS[0], "hasta": ANIOS[-1],
            # Sin mediciones del IDEAM no hay sesgo real: aquí se compara NASA con la radiación corregida
            "sesgo_nasa": round(float(d["NASA"].sum() / d["ML"].sum() - 1), 4), "sesgo_ml": 0.0,
            "rmse_nasa": formato.rmse(d["NASA"], d["ML"]), "rmse_ml": 0.0,
            "clim": {"ideam": formato.matriz(d, "NASA"), "nasa": formato.matriz(d, "NASA"),
                     "ml": formato.matriz(d, "ML"), "t2m": formato.matriz(d, "T2M")},
        })
    nasa, ml = pd.concat(nasa_todo), pd.concat(ml_todo)
    meta = {"registros": int(filas), "desde": ANIOS[0], "hasta": ANIOS[-1],
            "sesgo_nasa": round(float(nasa.sum() / ml.sum() - 1), 4), "rmse_nasa": formato.rmse(nasa, ml),
            "rmse_ml": 0.0, "demo": True}
    salida = formato.armar_salida(estaciones, deptos, meta)
    ruta = formato.escribir(salida)
    m = salida["meta"]
    print(f"\n{ruta.relative_to(REPO)}: {m['estaciones']} capitales en {m['departamentos']} departamentos, "
          f"{m['registros']:,} registros ({m['desde']}-{m['hasta']}), NASA frente a corregida {m['sesgo_nasa']:+.1%}, "
          f"{ruta.stat().st_size / 1024:.0f} kB  [DEMOSTRACIÓN]")


if __name__ == "__main__":
    main()
