"""Piezas comunes de los scripts que generan dashboard/data/colombia.json.

Las usan build_data.py (parquet), build_data_aws.py (Athena) y build_demo_data.py (demostración), para que los
tres escriban exactamente el mismo formato: climatología mes x hora por estación, más los contornos del país,
los departamentos y los municipios.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent          # dashboard/
GEO = Path(__file__).resolve().parent / "geo"
SALIDA = ROOT / "dashboard" / "data" / "colombia.json"
HORAS = list(range(7, 19))                              # 7 h a 18 h: fuera de ellas casi no hay registros
BOGOTA = (4.711, -74.072)                               # estación inicial: la más cercana a Bogotá


def clave(texto: str) -> str:
    """Nombre sin tildes, en minúsculas y sin signos: para comparar escrituras distintas del mismo nombre."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", t)).strip()


# ---------------------------------------------------------------- geografía
def _features(archivo: str) -> list[dict]:
    return json.loads((GEO / archivo).read_text(encoding="utf-8"))["features"]


def limite() -> list[list[float]]:
    return _features("colombia.geojson")[0]["geometry"]["coordinates"][0]


def departamentos() -> list[dict]:
    """Nombre (n), centro (c) y contorno (p) de cada departamento, por orden alfabético."""
    return [{"n": f["properties"]["nombre"], "c": f["properties"]["centro"], "p": f["geometry"]["coordinates"][0]}
            for f in _features("colombia_departamentos.geojson")]


def municipios(deptos: list[dict]) -> list[dict]:
    """Nombre, centro y contorno de cada municipio, el índice de su departamento (d) y, si es capital, su rango (r)."""
    indice = {d["n"]: i for i, d in enumerate(deptos)}
    salida = []
    for f in _features("colombia_municipios.geojson"):
        p = f["properties"]
        m = {"n": p["nombre"], "c": p["centro"], "p": f["geometry"]["coordinates"][0], "d": indice[p["depto"]]}
        if "r" in p:
            m["r"] = p["r"]
        salida.append(m)
    return salida


def islas(deptos: list[dict]) -> list[dict]:
    """San Andrés y Providencia: nombre (n), centro (c), índice de su departamento (d) y sus contornos (p)."""
    indice = {d["n"]: i for i, d in enumerate(deptos)}
    return [{"n": f["properties"]["nombre"], "c": f["properties"]["centro"], "d": indice[f["properties"]["depto"]],
             "p": [poligono[0] for poligono in f["geometry"]["coordinates"]]}
            for f in _features("colombia_islas.geojson")]


def nombre_departamento(bruto: str, deptos: list[dict]) -> str:
    """Escribe el departamento como el mapa: 'VALLE DEL CAUCA' o 'Valle Del Cauca' -> 'Valle del Cauca'."""
    por_clave = {clave(d["n"]): d["n"] for d in deptos}
    k = clave(bruto)
    alias = {"bogota": "Bogotá D.C.", "bogota dc": "Bogotá D.C.", "bogota d c": "Bogotá D.C.",
             "san andres": "San Andrés y Providencia", "san andres y providencia": "San Andrés y Providencia",
             "archipielago de san andres providencia y santa catalina": "San Andrés y Providencia"}
    return por_clave.get(k) or alias.get(k) or re.sub(r"\b(De|Del|La|Los|Y)\b", lambda m: m.group(1).lower(), str(bruto).title())


# ---------------------------------------------------------------- datos
def nombre_limpio(nombre: str) -> str:
    """Nombre de la estación sin el código '[123]' ni '- AUT', con tildes y mayúsculas corregidas."""
    base = re.sub(r"\s*\[\d+\]\s*$", "", nombre).replace(" - AUT", "")
    return base.title().replace("Metromedellin", "Metro Medellín").replace(
        "Chigorodo", "Chigorodó").replace("Vegachi", "Vegachí").replace(
        "Santa Barbara", "Santa Bárbara").replace("Cotove", "Cotové")


def matriz(df: pd.DataFrame, col: str) -> list[list[float]]:
    """Promedio [mes 1-12][hora 7-18] de una columna. Una celda sin datos toma el promedio de su mes."""
    t = df.pivot_table(index="Mes", columns="Hora", values=col, aggfunc="mean").reindex(index=range(1, 13), columns=HORAS)
    t = t.apply(lambda fila: fila.fillna(fila.mean() if fila.notna().any() else 0.0), axis=1)
    return [[round(float(v), 1) for v in fila] for fila in t.values]


def rmse(a: pd.Series, b: pd.Series) -> float:
    return round(float(np.sqrt(((a - b) ** 2).mean())), 1)


def estacion_inicial(estaciones: list[dict]) -> str:
    """Nombre de la estación más cercana a Bogotá (aproximación plana, suficiente para elegir una)."""
    return min(estaciones, key=lambda e: (e["lat"] - BOGOTA[0]) ** 2 + (e["lon"] - BOGOTA[1]) ** 2)["nombre"]


def estaciones_desde(df: pd.DataFrame, deptos: list[dict], validacion: tuple[int, int] | None = None) -> list[dict]:
    """Una estación por CodigoEstacion. df trae: CodigoEstacion, NombreEstacion, Departamento, Latitud, Longitud,
    Altitud, Anio, Mes, Hora y las columnas IDEAM, NASA, ML y T2M (solo horas 7 a 18).
    validacion = (desde, hasta): si se da, el sesgo y el error se miden solo con esos años (los que el modelo no vio
    al entrenar); una estación sin registros en ese rango se mide con todos los suyos."""
    salida = []
    for codigo, g in df.groupby("CodigoEstacion"):
        v = g[g["Anio"].between(*validacion)] if validacion else g
        if v.empty:
            v = g
        salida.append({
            "id": int(codigo),
            "nombre": nombre_limpio(g["NombreEstacion"].iloc[0]),
            "departamento": nombre_departamento(g["Departamento"].iloc[0], deptos),
            "lat": round(float(g["Latitud"].iloc[0]), 4),
            "lon": round(float(g["Longitud"].iloc[0]), 4),
            "alt": int(g["Altitud"].iloc[0]),
            "registros": int(len(g)),
            # Fracción de sus registros que el IDEAM marca como preliminares (sin validar), si el dato viene en la tabla
            **({"preliminar": round(float(g["EsPreliminar"].astype(float).mean()), 3)} if "EsPreliminar" in g.columns else {}),
            "desde": int(g["Anio"].min()),
            "hasta": int(g["Anio"].max()),
            "sesgo_nasa": round(float(v["NASA"].sum() / v["IDEAM"].sum() - 1), 4),
            "sesgo_ml": round(float(v["ML"].sum() / v["IDEAM"].sum() - 1), 4),
            "rmse_nasa": rmse(v["NASA"], v["IDEAM"]),
            "rmse_ml": rmse(v["ML"], v["IDEAM"]),
            "clim": {k: matriz(g, c) for k, c in (("ideam", "IDEAM"), ("nasa", "NASA"), ("ml", "ML"), ("t2m", "T2M"))},
        })
    return salida


def armar_salida(estaciones: list[dict], deptos: list[dict], meta: dict) -> dict:
    """Une estaciones, metadatos y contornos en el formato que lee el dashboard."""
    estaciones = sorted(estaciones, key=lambda e: (e["departamento"], e["nombre"]))
    huecos = sum(v is None for e in estaciones for m in e["clim"].values() for f in m for v in f)
    assert huecos == 0, f"Hay {huecos} celdas mes-hora sin datos"
    return {
        "meta": {
            "departamento": "Colombia", "generado": date.today().isoformat(), "estaciones": len(estaciones),
            "departamentos": len({e["departamento"] for e in estaciones}), "horas": HORAS,
            "estacion_inicial": estacion_inicial(estaciones), **meta,
        },
        "limite": limite(),
        "departamentos": deptos,
        "islas": islas(deptos),
        "municipios": municipios(deptos),
        "estaciones": estaciones,
    }


def escribir(salida: dict, ruta: Path = SALIDA) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return ruta
