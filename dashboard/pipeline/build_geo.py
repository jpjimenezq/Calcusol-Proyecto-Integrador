"""Contornos de Colombia para el dashboard: país, departamentos y municipios.

Descarga de geoBoundaries (versión fija) los límites simplificados, los reduce más, empareja cada
municipio con su departamento y escribe tres archivos en pipeline/geo/:

    colombia.geojson                 contorno del país (solo el territorio continental)
    colombia_departamentos.geojson   33 departamentos (incluye Bogotá D.C.)
    colombia_municipios.geojson      1.122 municipios, con su departamento y el rango de las capitales
    colombia_islas.geojson           San Andrés y Providencia con todo su detalle (el mapa las dibuja en recuadros)

Cada elemento lleva un solo anillo exterior (el de mayor área), que es lo que dibuja el mapa.
Solo se necesita al cambiar la fuente o la simplificación (requiere shapely):
    uv run --no-project --with shapely python pipeline/build_geo.py

Fuentes (geoBoundaries, https://www.geoboundaries.org):
    ADM0  país            geoBoundaries / Wikimedia           CC0 1.0
    ADM1  departamentos   OpenStreetMap (Wambacher)           ODbL 1.0
    ADM2  municipios      DANE                                CC BY 4.0
"""
from __future__ import annotations

import json
import re
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

from shapely.geometry import shape
from shapely.prepared import prep

GEO = Path(__file__).resolve().parent / "geo"
CRUDO = Path(tempfile.gettempdir()) / "calcusol_geo"
BASE = "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/COL"
NIVELES = ("ADM0", "ADM1", "ADM2")
TOLERANCIA = {"ADM0": 0.008, "ADM1": 0.01, "ADM2": 0.01}   # grados; 0,01 ≈ 1,1 km

# Nombres de departamento como los escribe el resto del proyecto
DEPARTAMENTOS = {
    "Bogota Capital District": "Bogotá D.C.",
    "Archipiélago de San Andrés, Providencia y Santa Catalina": "San Andrés y Providencia",
}

# Capitales por orden de importancia (población): fijan qué municipios se rotulan primero en el mapa
CAPITALES = [
    ("Bogotá D.C.", "Bogotá D.C."), ("Antioquia", "Medellín"), ("Valle del Cauca", "Cali"),
    ("Atlántico", "Barranquilla"), ("Bolívar", "Cartagena de Indias"), ("Norte de Santander", "Cúcuta"),
    ("Santander", "Bucaramanga"), ("Risaralda", "Pereira"), ("Magdalena", "Santa Marta"),
    ("Tolima", "Ibagué"), ("Nariño", "Pasto"), ("Caldas", "Manizales"), ("Huila", "Neiva"),
    ("Meta", "Villavicencio"), ("Quindío", "Armenia"), ("Córdoba", "Montería"), ("Cesar", "Valledupar"),
    ("Cauca", "Popayán"), ("Sucre", "Sincelejo"), ("Boyacá", "Tunja"), ("Caquetá", "Florencia"),
    ("La Guajira", "Riohacha"), ("Chocó", "Quibdó"), ("Casanare", "Yopal"), ("Putumayo", "Mocoa"),
    ("Arauca", "Arauca"), ("Guaviare", "San José del Guaviare"), ("Amazonas", "Leticia"),
    ("Guainía", "Inírida"), ("Vaupés", "Mitú"), ("Vichada", "Puerto Carreño"),
    ("San Andrés y Providencia", "San Andrés"),
]


def clave(texto: str) -> str:
    """Nombre sin tildes, en minúsculas y sin signos: sirve para comparar escrituras distintas."""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", t)).strip()


def nombre_municipio(bruto: str) -> str:
    """Corrige tildes graves de la fuente (San Andrès) y escribe en minúscula 'de', 'del', 'la', 'los'."""
    if clave(bruto).startswith("distrito especial industrial y portuario de barr"):   # nombre cortado en la fuente
        return "Barranquilla"
    n = bruto.translate(str.maketrans("èòìùà", "éóíúá")).replace(", D.c.", " D.C.")
    n = re.sub(r"\b(De|Del|La|Las|Los|Y)\b", lambda m: m.group(1).lower(), n)
    return n[0].upper() + n[1:]


def cargar(nivel: str) -> list[dict]:
    CRUDO.mkdir(exist_ok=True)
    ruta = CRUDO / f"COL-{nivel}.geojson"
    if not ruta.exists():
        print(f"descargando {nivel}…")
        urllib.request.urlretrieve(f"{BASE}/{nivel}/geoBoundaries-COL-{nivel}_simplified.geojson", ruta)
    return json.loads(ruta.read_text(encoding="utf-8"))["features"]


def mayor(g):
    """Polígono de mayor área (un MultiPolygon trae islas; el mapa dibuja un solo anillo)."""
    return max(g.geoms, key=lambda p: p.area) if g.geom_type == "MultiPolygon" else g


def anillo(poligono, tol: float) -> list[list[float]]:
    q = mayor(poligono.simplify(tol, preserve_topology=True))
    pts: list[list[float]] = []
    for x, y in q.exterior.coords:
        p = [round(x, 3), round(y, 3)]
        if not pts or p != pts[-1]:           # el redondeo junta puntos casi iguales
            pts.append(p)
    return pts


def centro(poligono) -> list[float]:
    c = poligono.centroid
    if not poligono.contains(c):
        c = poligono.representative_point()
    return [round(c.x, 3), round(c.y, 3)]


def escribir(nombre: str, features: list[dict]) -> None:
    salida = {"type": "FeatureCollection", "features": features}
    ruta = GEO / nombre
    ruta.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  {nombre}: {len(features)} elementos, {ruta.stat().st_size / 1024:.0f} kB")


def main() -> None:
    GEO.mkdir(exist_ok=True)

    # País: territorio continental (una sola geometría)
    pais = mayor(shape(cargar("ADM0")[0]["geometry"]))
    escribir("colombia.geojson", [{"type": "Feature", "properties": {"nombre": "Colombia"},
                                   "geometry": {"type": "Polygon", "coordinates": [anillo(pais, TOLERANCIA["ADM0"])]}}])

    # Departamentos
    deptos, completos = [], []
    for f in cargar("ADM1"):
        g = shape(f["geometry"])
        nombre = DEPARTAMENTOS.get(f["properties"]["shapeName"], f["properties"]["shapeName"])
        completos.append((nombre, g, prep(g)))
        p = mayor(g)
        deptos.append({"type": "Feature", "properties": {"nombre": nombre, "centro": centro(p)},
                       "geometry": {"type": "Polygon", "coordinates": [anillo(g, TOLERANCIA["ADM1"])]}})
    deptos.sort(key=lambda f: f["properties"]["nombre"])
    escribir("colombia_departamentos.geojson", deptos)

    # Municipios, cada uno con su departamento (el que contiene su punto representativo)
    rango = {(clave(d), clave(m)): i for i, (d, m) in enumerate(CAPITALES)}
    munis, sin_depto = [], 0
    adm2 = cargar("ADM2")
    for f in adm2:
        g = shape(f["geometry"])
        pt = g.representative_point()
        depto = next((n for n, _, pg in completos if pg.contains(pt)), None)
        if depto is None:                      # un punto en la costa puede quedar fuera por la simplificación
            depto = min(completos, key=lambda d: d[1].distance(pt))[0]
            sin_depto += 1
        nombre = nombre_municipio(f["properties"]["shapeName"])
        props = {"nombre": nombre, "centro": centro(mayor(g)), "depto": depto}
        r = rango.get((clave(depto), clave(nombre)))
        if r is not None:
            props["r"] = r
        munis.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Polygon", "coordinates": [anillo(g, TOLERANCIA["ADM2"])]}})
    munis.sort(key=lambda f: (f["properties"]["depto"], f["properties"]["nombre"]))
    escribir("colombia_municipios.geojson", munis)

    # Islas: San Andrés y Providencia están a unos 700 km del continente y miden pocos kilómetros, así que en el mapa
    # del país no se verían. Se guardan aparte, con todo su detalle, para dibujarlas en recuadros ampliados.
    isla_nombres = {"san andres": "San Andrés", "providencia santa isabel": "Providencia y Santa Catalina"}
    islas = []
    for f in adm2:
        g = shape(f["geometry"])
        k = clave(f["properties"]["shapeName"])
        if g.bounds[0] < -80 and k in isla_nombres:
            polis = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
            anillos = [[[round(x, 4), round(y, 4)] for x, y in p.exterior.coords] for p in sorted(polis, key=lambda p: -p.area)]
            islas.append({"type": "Feature",
                          "properties": {"nombre": isla_nombres[k], "depto": "San Andrés y Providencia", "centro": centro(mayor(g))},
                          "geometry": {"type": "MultiPolygon", "coordinates": [[a] for a in anillos]}})
    escribir("colombia_islas.geojson", islas)

    encontradas = {f["properties"]["r"] for f in munis if "r" in f["properties"]}
    faltan = [CAPITALES[i] for i in range(len(CAPITALES)) if i not in encontradas]
    print(f"  municipios fuera de su departamento por la simplificación: {sin_depto}")
    print(f"  capitales encontradas: {len(encontradas)} de {len(CAPITALES)}" + (f" · faltan: {faltan}" if faltan else ""))


if __name__ == "__main__":
    main()
