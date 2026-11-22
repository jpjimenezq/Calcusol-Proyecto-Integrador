"""
Bronze — Incorporación de archivos IDEAM.

Los CSV del IDEAM se descargan a mano del portal DHIME y YA están en
bronze/ideam/. Bronze no los re-descarga ni los modifica: son el crudo intacto.
Este módulo FORMALIZA su incorporación, registrando cada archivo en el manifest
con procedencia, tamaño y checksum, y una validación técnica básica (legible,
con las columnas esperadas). Así la ingesta es auditable y reproducible.

NO hace limpieza analítica (encoding definitivo, fechas, unidades, calidad):
eso es de Silver.

Portabilidad: `validar_archivo` es lógica pura; `incorporar` coordina y registra.

EJECUCIÓN (desde la raíz, dentro de Docker; no necesita red):
  docker compose run --rm calcusol uv run python -m calcusol.bronze.ideam
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from calcusol.bronze import manifest
from calcusol.config import settings

MEDICIONES = settings.bronze_path("ideam", "mediciones")
CATALOGO_DIR = settings.bronze_path("ideam", "catalogo")

COLUMNAS_MEDICION = [
    "CodigoEstacion", "NombreEstacion", "Variable", "Parametro",
    "Fecha", "Unidad", "Valor", "NivelAprobacion",
]
COLUMNAS_CATALOGO = ["CODIGO", "LATITUD", "LONGITUD", "ALTITUD"]
CATALOGO_PRINCIPAL = "CNE_IDEAM.csv"


@dataclass
class Resultado:
    valido: bool
    columnas: list[str] = field(default_factory=list)
    filas_aprox: int = 0
    encoding: str | None = None
    separador: str | None = None
    detalle: str = ""


# ================================================================== Lógica pura
def _leer_cabecera(ruta: Path) -> tuple[str, str, list[str]] | None:
    """
    Detecta encoding y separador probando combinaciones y devuelve la cabecera.
    No carga el archivo entero. Devuelve None si no se puede leer.
    """
    for enc in ("utf-8", "latin-1"):
        for sep in (",", ";"):
            try:
                with open(ruta, encoding=enc, newline="") as f:
                    primera = f.readline()
                cols = next(csv.reader([primera], delimiter=sep))
                if len(cols) > 1:
                    return enc, sep, [c.strip() for c in cols]
            except (UnicodeDecodeError, StopIteration):
                continue
    return None


def _contar_filas(ruta: Path, encoding: str) -> int:
    """Cuenta líneas de datos (sin cabecera) sin cargar todo a memoria."""
    with open(ruta, encoding=encoding, errors="replace") as f:
        total = sum(1 for _ in f)
    return max(total - 1, 0)


def validar_archivo(ruta: Path, columnas_esperadas: list[str]) -> Resultado:
    """
    Validación técnica de un CSV: legible, con las columnas esperadas, no vacío.
    Lógica pura, no registra nada.
    """
    lectura = _leer_cabecera(ruta)
    if lectura is None:
        return Resultado(valido=False, detalle="ilegible o separador no detectado")
    enc, sep, cols = lectura

    faltantes = [c for c in columnas_esperadas if c not in cols]
    if faltantes:
        return Resultado(valido=False, columnas=cols, encoding=enc, separador=sep,
                         detalle=f"faltan columnas: {faltantes}")

    filas = _contar_filas(ruta, enc)
    if filas == 0:
        return Resultado(valido=False, columnas=cols, encoding=enc, separador=sep,
                         detalle="sin filas de datos")

    return Resultado(valido=True, columnas=cols, filas_aprox=filas,
                     encoding=enc, separador=sep)


# ================================================================== Coordinación
def _registrar(archivo: Path, subfuente: str, columnas_esperadas: list[str],
               conteo: dict) -> None:
    rel = str(archivo.relative_to(Path(settings.bronze_path("ideam"))))
    if manifest.ya_registrado_ok("ideam", rel):
        conteo["saltados"] += 1
        return
    res = validar_archivo(archivo, columnas_esperadas)
    base = {
        "fuente": "ideam", "subfuente": subfuente, "archivo": rel,
        "bytes": archivo.stat().st_size,
        # OJO: encoding/separador detectados sobre la CABECERA (ASCII), no sobre
        # el cuerpo. El cuerpo tiene tildes en latin-1/cp1252; Silver debe
        # resolver el encoding real al leer las celdas con texto.
        "encoding_cabecera": res.encoding, "separador_detectado": res.separador,
        "filas_aprox": res.filas_aprox,
    }
    if res.valido:
        manifest.registrar("ideam", {
            **base, "estado": "ok", "sha256": manifest.checksum_sha256(archivo),
        })
        conteo["ok"] += 1
    else:
        manifest.registrar("ideam", {**base, "estado": "invalido", "detalle": res.detalle})
        conteo["invalidos"] += 1
        print(f"  ! {rel}: {res.detalle}")


def incorporar() -> dict[str, int]:
    """
    Registra en el manifest todos los CSV de mediciones y el catálogo principal,
    con validación técnica. Idempotente: salta lo ya registrado OK.
    """
    base_med = Path(MEDICIONES)
    if not base_med.exists():
        raise SystemExit(f"No existe {base_med}")

    conteo = {"ok": 0, "saltados": 0, "invalidos": 0}

    archivos = sorted(base_med.rglob("*.csv"))
    print(f"Mediciones IDEAM: {len(archivos)} CSV")
    for archivo in archivos:
        _registrar(archivo, "mediciones", COLUMNAS_MEDICION, conteo)

    catalogo = Path(CATALOGO_DIR) / CATALOGO_PRINCIPAL
    if catalogo.exists():
        print(f"Catálogo: {CATALOGO_PRINCIPAL}")
        _registrar(catalogo, "catalogo", COLUMNAS_CATALOGO, conteo)
    else:
        print(f"  ! No se encontró el catálogo {catalogo}")

    print(f"\nResumen IDEAM: {conteo}")
    return conteo


def main() -> None:
    incorporar()


if __name__ == "__main__":
    main()
