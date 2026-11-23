"""Pipeline de datos del dashboard Calcusol · Antioquia.

Lee dataset_modelo.parquet, filtra el departamento de Antioquia, aplica el
modelo de corrección (XGBoost) a la radiación satelital y exporta un JSON
compacto con la climatología mes x hora por estación, que es todo lo que el
dashboard necesita para simular energía, autoconsumo, ahorro, Payback y VPN.

Uso:  python pipeline/build_data.py
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PARQUET = ROOT / "dataset_modelo.parquet"
MODELO = ROOT / "modelo_radiacion_calcusol.joblib"
LIMITE = ROOT / "pipeline" / "geo" / "antioquia.geojson"  # contorno simplificado (geoBoundaries, ODbL)
MUNICIPIOS = ROOT / "pipeline" / "geo" / "antioquia_municipios.geojson"  # límites municipales (geoBoundaries / DANE, CC BY 4.0)
SALIDA = ROOT / "dashboard" / "data" / "antioquia.json"

# Nombres que la fuente trae incompletos o sin tilde
NOMBRES = {
    "San Andrès": "San Andrés de Cuerquia", "Yondó (Casabe)": "Yondó", "Yali": "Yalí",
    "Vigia Del Fuerte": "Vigía del Fuerte", "San Pedro De Uraba": "San Pedro de Urabá",
    "Entrerrios": "Entrerríos", "San Pedro": "San Pedro de los Milagros", "Santuario": "El Santuario",
    "Peñol": "El Peñol", "Retiro": "El Retiro", "Santa Fe De Antioquia": "Santa Fe de Antioquia",
}
# Municipios que se rotulan en el mapa, por orden de prioridad cuando no caben todos
ROTULOS = [
    "Medellín", "Turbo", "Caucasia", "Rionegro", "Puerto Berrío", "Santa Fe de Antioquia", "Yarumal",
    "Apartadó", "Urrao", "Sonsón", "Segovia", "Ituango", "Andes", "Necoclí", "El Bagre", "Amalfi",
    "Dabeiba", "Santa Rosa de Osos", "Frontino", "Yondó", "Chigorodó", "Remedios", "Bello", "Puerto Triunfo",
    "Támesis", "Valdivia", "Arboletes", "San Carlos", "Vigía del Fuerte", "Yolombó", "Abejorral", "Anorí",
]


def municipios() -> list[dict]:
    """Nombre, centro y contorno de cada municipio, con su prioridad de rótulo."""
    out = []
    for f in json.loads(MUNICIPIOS.read_text(encoding="utf-8"))["features"]:
        bruto = f["properties"]["nombre"]
        nombre = NOMBRES.get(bruto) or re.sub(r"\b(De|Del|La|Los)\b", lambda m: m.group(1).lower(), bruto)
        nombre = nombre[0].upper() + nombre[1:]
        m = {"n": nombre, "c": f["properties"]["centro"], "p": f["geometry"]["coordinates"][0]}
        if nombre in ROTULOS:
            m["r"] = ROTULOS.index(nombre)
        out.append(m)
    faltan = set(ROTULOS) - {m["n"] for m in out}
    assert not faltan, f"Rótulos sin municipio: {faltan}"
    return sorted(out, key=lambda m: m["n"])

DEPARTAMENTO = "Antioquia"
HORAS = list(range(7, 19))  # 7 h a 18 h: la hora 19 casi no tiene registros

FEATURES = [
    "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M",
    "Latitud", "Longitud", "Altitud",
    "Hora_sin", "Hora_cos", "Mes_sin", "Mes_cos", "Dia_sin", "Dia_cos",
]


def cargar() -> pd.DataFrame:
    return duckdb.execute(
        "SELECT * FROM read_parquet(?) WHERE Departamento = ?",
        [str(PARQUET), DEPARTAMENTO],
    ).df()


def corregir(df: pd.DataFrame) -> np.ndarray:
    """Radiación corregida con el modelo ML (W/m2), sin valores negativos."""
    modelo = joblib.load(MODELO)
    x = df[FEATURES[:7]].copy()
    for nombre, col, periodo in (("Hora", "Hora", 24), ("Mes", "Mes", 12), ("Dia", "DiaDelAnio", 365)):
        ang = 2 * np.pi * df[col] / periodo
        x[f"{nombre}_sin"] = np.sin(ang)
        x[f"{nombre}_cos"] = np.cos(ang)
    assert list(modelo.feature_names_in_) == FEATURES, "El modelo espera otras variables"
    return np.clip(modelo.predict(x[FEATURES]), 0, None)


def nombre_limpio(nombre: str) -> str:
    base = re.sub(r"\s*\[\d+\]\s*$", "", nombre).replace(" - AUT", "")
    return base.title().replace("Metromedellin", "Metro Medellín").replace(
        "Aragón", "Aragón").replace("Chigorodo", "Chigorodó").replace(
        "Vegachi", "Vegachí").replace("Santa Barbara", "Santa Bárbara").replace(
        "Cotove", "Cotové")


def matriz(df: pd.DataFrame, col: str) -> list[list[float | None]]:
    """Promedio [mes 1-12][hora 7-18] de una columna."""
    t = df.pivot_table(index="Mes", columns="Hora", values=col, aggfunc="mean")
    t = t.reindex(index=range(1, 13), columns=HORAS)
    return [[None if pd.isna(v) else round(float(v), 1) for v in fila] for fila in t.values]


def rmse(a: pd.Series, b: pd.Series) -> float:
    return round(float(np.sqrt(((a - b) ** 2).mean())), 1)


def main() -> None:
    df = cargar()
    df["ML"] = corregir(df)
    df = df[df["Hora"].isin(HORAS)].rename(
        columns={"Valor": "IDEAM", "ALLSKY_SFC_SW_DWN": "NASA"})
    df["Anio"] = df["Fecha"].dt.year

    estaciones = []
    for codigo, g in df.groupby("CodigoEstacion"):
        estaciones.append({
            "id": int(codigo),
            "nombre": nombre_limpio(g["NombreEstacion"].iloc[0]),
            "lat": round(float(g["Latitud"].iloc[0]), 4),
            "lon": round(float(g["Longitud"].iloc[0]), 4),
            "alt": int(g["Altitud"].iloc[0]),
            "registros": int(len(g)),
            "desde": int(g["Anio"].min()),
            "hasta": int(g["Anio"].max()),
            "sesgo_nasa": round(float(g["NASA"].sum() / g["IDEAM"].sum() - 1), 4),
            "sesgo_ml": round(float(g["ML"].sum() / g["IDEAM"].sum() - 1), 4),
            "rmse_nasa": rmse(g["NASA"], g["IDEAM"]),
            "rmse_ml": rmse(g["ML"], g["IDEAM"]),
            "clim": {k: matriz(g, c) for k, c in
                     (("ideam", "IDEAM"), ("nasa", "NASA"), ("ml", "ML"), ("t2m", "T2M"))},
        })
    estaciones.sort(key=lambda e: e["nombre"])

    huecos = sum(v is None for e in estaciones for m in e["clim"].values() for f in m for v in f)
    assert huecos == 0, f"Hay {huecos} celdas mes-hora sin datos"

    salida = {
        "meta": {
            "departamento": DEPARTAMENTO,
            "generado": date.today().isoformat(),
            "registros": int(len(df)),
            "estaciones": len(estaciones),
            "desde": int(df["Anio"].min()),
            "hasta": int(df["Anio"].max()),
            "horas": HORAS,
            "sesgo_nasa": round(float(df["NASA"].sum() / df["IDEAM"].sum() - 1), 4),
            "rmse_nasa": rmse(df["NASA"], df["IDEAM"]),
            "rmse_ml": rmse(df["ML"], df["IDEAM"]),
        },
        "limite": json.loads(LIMITE.read_text(encoding="utf-8"))["geometry"]["coordinates"][0],
        "municipios": municipios(),
        "estaciones": estaciones,
    }
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    m = salida["meta"]
    print(f"{SALIDA.relative_to(ROOT)}: {m['estaciones']} estaciones, {m['registros']:,} registros "
          f"({m['desde']}-{m['hasta']}), sesgo NASA {m['sesgo_nasa']:+.1%}, "
          f"RMSE NASA {m['rmse_nasa']} -> ML {m['rmse_ml']} W/m2, {SALIDA.stat().st_size/1024:.0f} kB")
    for e in estaciones:
        print(f"  {e['nombre']:<26} {e['alt']:>5} m  {e['desde']}-{e['hasta']}  "
              f"n={e['registros']:>6}  sesgo NASA {e['sesgo_nasa']:+.1%}")


if __name__ == "__main__":
    main()
