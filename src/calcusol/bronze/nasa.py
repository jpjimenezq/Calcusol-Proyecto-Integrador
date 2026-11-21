"""
Bronze — Ingesta de NASA POWER (horaria).

Consulta la API de NASA POWER por las coordenadas de las estaciones IDEAM y
guarda las respuestas JSON ORIGINALES en bronze/nasa/, sin transformarlas.
La corrección temporal (UTC-4h) y la conversión de unidades son de Silver, no
de aquí: Bronze conserva el crudo.

Decisiones (ver docs/contrato_datos.md):
  - time-standard=UTC: se descarga en UTC; el alineamiento se hace en Silver.
  - Periodo por defecto desde 2005 (año de corte del dataset), acotado por el
    piso de NASA horaria (2001). Configurable por CLI.

Portabilidad: `descargar_estacion_anio` e `ingestar` son lógica pura sin AWS;
`main` es el entry point local (CLI). Un futuro handler de Lambda envuelve
`descargar_estacion_anio` sin tocar esta lógica.

EJECUCIÓN (desde la raíz, dentro de Docker; necesita red):
  docker compose run --rm calcusol uv run python -m calcusol.bronze.nasa
  docker compose run --rm calcusol uv run python -m calcusol.bronze.nasa \
      --estaciones 27015310 35095120 --anios 2017 2018
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import pandas as pd
import requests

from calcusol.bronze import manifest
from calcusol.config import settings

# ------------------------------------------------------------------ Parámetros
URL_NASA = "https://power.larc.nasa.gov/api/temporal/hourly/point"
VARIABLES = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]
COMUNIDAD = "RE"
ESTANDAR = "UTC"            # se descarga en UTC; el alineamiento es de Silver
ANIO_INICIO_DEFECTO = 2005  # año de corte del dataset (D-AÑO)
ANIO_FIN_DEFECTO = 2022
PISO_NASA = 2001            # NASA horaria no existe antes (doc oficial)

PAUSA_S = 1.0               # entre descargas, respeta el rate limit
TIMEOUT_S = 300
MAX_INTENTOS = 5
CODIGOS_REINTENTO = {429, 500, 502, 503, 504}

CATALOGO = settings.bronze_path("ideam", "catalogo", "CNE_IDEAM.csv")
MEDICIONES = settings.bronze_path("ideam", "mediciones")
# El código de estación va en el nombre: ideam_radiacion_<CODIGO>_<desde>_<hasta>.csv
PATRON_ARCHIVO = re.compile(r"ideam_radiacion_(\d+)_")


# ================================================================== Lógica pura
def nombre_archivo(lat: float, lon: float, anio: int) -> str:
    """Nombre canónico del JSON crudo (coordenadas a 4 decimales + año + std)."""
    return f"{lat:.4f}_{lon:.4f}_{anio}_{ESTANDAR}.json"


def _params(lat: float, lon: float, anio: int) -> dict:
    return {
        "parameters": ",".join(VARIABLES),
        "community": COMUNIDAD,
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start": f"{anio}0101",
        "end": f"{anio}1231",
        "time-standard": ESTANDAR,
        "format": "JSON",
    }


def descargar_estacion_anio(
    lat: float,
    lon: float,
    anio: int,
    destino: Path,
    sesion: requests.Session | None = None,
) -> None:
    """
    Descarga un año de una estación y lo guarda TAL CUAL en `destino`.
    Reintenta ante 429/5xx y errores de conexión con backoff lineal.
    Lógica pura: no sabe de manifest ni de rutas de proyecto.
    """
    cliente = sesion or requests
    params = _params(lat, lon, anio)
    for intento in range(1, MAX_INTENTOS + 1):
        try:
            r = cliente.get(URL_NASA, params=params, timeout=TIMEOUT_S)
            if r.status_code == 200:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(r.text, encoding="utf-8")
                return
            if r.status_code in CODIGOS_REINTENTO:
                espera = 15 * intento
                print(f"      NASA {r.status_code}, reintento {intento} en {espera}s")
                time.sleep(espera)
                continue
            raise RuntimeError(f"NASA respondió {r.status_code}: {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            espera = 15 * intento
            print(f"      conexión {e.__class__.__name__}, reintento {intento} en {espera}s")
            time.sleep(espera)
    raise RuntimeError(f"NASA no respondió tras {MAX_INTENTOS} intentos")


# ================================================================== Coordinación
def estaciones_con_radiacion() -> set[str]:
    """
    Códigos de estación que tienen mediciones de RAD SOLAR, extraídos de los
    nombres de archivo en bronze/ideam/mediciones/ (sin abrir los CSV).
    Son las estaciones con variable objetivo Y; solo para ellas tiene sentido
    pedir NASA.
    """
    base = MEDICIONES
    if "://" in base:
        raise NotImplementedError("estaciones_con_radiacion asume mediciones locales.")
    codigos = set()
    for archivo in Path(base).rglob("*.csv"):
        m = PATRON_ARCHIVO.search(archivo.name)
        if m:
            codigos.add(str(int(m.group(1))))  # normaliza ceros a la izquierda
    return codigos


def leer_coordenadas(estaciones: list[str] | None = None) -> dict[str, tuple[float, float]]:
    """
    Coordenadas de las estaciones a descargar.

    - Si `estaciones` se da: usa esas (para pruebas).
    - Si no: usa SOLO las estaciones que tienen mediciones de radiación IDEAM
      (las que tienen Y), no todo el catálogo nacional.

    Toma lat/lon del catálogo CNE, normalizando coma decimal y código sin ceros
    a la izquierda.
    """
    ruta = CATALOGO
    if "://" in ruta:
        raise NotImplementedError("leer_coordenadas aún asume catálogo local.")
    cat = pd.read_csv(ruta, usecols=["CODIGO", "LATITUD", "LONGITUD"], dtype=str)
    cat["CodigoEstacion"] = cat["CODIGO"].str.strip().astype("int64").astype(str)
    cat["Latitud"] = cat["LATITUD"].str.replace(",", ".", regex=False).astype(float)
    cat["Longitud"] = cat["LONGITUD"].str.replace(",", ".", regex=False).astype(float)
    cat = cat.dropna(subset=["Latitud", "Longitud"]).drop_duplicates("CodigoEstacion")

    pedidas = {str(e) for e in estaciones} if estaciones else estaciones_con_radiacion()
    cat = cat[cat["CodigoEstacion"].isin(pedidas)]

    encontradas = set(cat["CodigoEstacion"])
    sin_coords = sorted(pedidas - encontradas)
    if sin_coords:
        print(f"  ! {len(sin_coords)} estación(es) con radiación sin coordenadas "
              f"en el catálogo: {sin_coords}")
    return {r.CodigoEstacion: (r.Latitud, r.Longitud) for r in cat.itertuples()}


def ingestar(
    estaciones: list[str] | None = None,
    anio_inicio: int = ANIO_INICIO_DEFECTO,
    anio_fin: int = ANIO_FIN_DEFECTO,
) -> dict[str, int]:
    """
    Descarga NASA para las estaciones y años dados. Idempotente: salta lo ya
    descargado y registrado OK. Registra cada resultado en el manifest.
    Devuelve un conteo {descargados, saltados, fallidos}.
    """
    anio_inicio = max(anio_inicio, PISO_NASA)
    if anio_fin < anio_inicio:
        raise ValueError(f"Rango de años inválido: {anio_inicio}-{anio_fin}")

    coords = leer_coordenadas(estaciones)
    if not coords:
        raise SystemExit("No se encontraron coordenadas para las estaciones dadas.")

    carpeta = Path(settings.bronze_path("nasa"))
    conteo = {"descargados": 0, "saltados": 0, "fallidos": 0}
    sesion = requests.Session()

    print(f"Estaciones: {len(coords)} | años {anio_inicio}-{anio_fin}")
    for codigo, (lat, lon) in coords.items():
        for anio in range(anio_inicio, anio_fin + 1):
            archivo = nombre_archivo(lat, lon, anio)
            destino = carpeta / archivo

            if destino.exists() and manifest.ya_registrado_ok("nasa", archivo):
                conteo["saltados"] += 1
                continue

            base = {
                "fuente": "nasa", "archivo": archivo, "codigo_estacion": codigo,
                "latitud": round(lat, 4), "longitud": round(lon, 4), "anio": anio,
                "variables": VARIABLES, "time_standard": ESTANDAR,
            }
            try:
                print(f"  descargando {codigo} {anio} -> {archivo}")
                descargar_estacion_anio(lat, lon, anio, destino, sesion=sesion)
                manifest.registrar("nasa", {
                    **base, "estado": "ok",
                    "bytes": destino.stat().st_size,
                    "sha256": manifest.checksum_sha256(destino),
                })
                conteo["descargados"] += 1
                time.sleep(PAUSA_S)
            except RuntimeError as e:
                print(f"  ! fallo {codigo} {anio}: {e}")
                manifest.registrar("nasa", {**base, "estado": "error", "detalle": str(e)})
                conteo["fallidos"] += 1

    print(f"\nResumen NASA: {conteo}")
    return conteo


# ================================================================== Entry point
def main() -> None:
    p = argparse.ArgumentParser(description="Ingesta Bronze de NASA POWER.")
    p.add_argument("--estaciones", nargs="*", default=None,
                   help="Códigos de estación IDEAM; por defecto todas las del catálogo.")
    p.add_argument("--anios", nargs="*", type=int, default=None,
                   help="Años concretos a descargar; por defecto el rango completo.")
    p.add_argument("--anio-inicio", type=int, default=ANIO_INICIO_DEFECTO)
    p.add_argument("--anio-fin", type=int, default=ANIO_FIN_DEFECTO)
    args = p.parse_args()

    if args.anios:
        ingestar(args.estaciones, min(args.anios), max(args.anios))
    else:
        ingestar(args.estaciones, args.anio_inicio, args.anio_fin)


if __name__ == "__main__":
    main()
