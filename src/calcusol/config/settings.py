"""
Resolución de rutas: local (data/) hoy, s3:// mañana.

Única fuente de verdad para dónde viven las zonas Medallion. El resto del código
pide rutas a través de estas funciones y NO construye paths a mano. Así migrar a
AWS es cambiar la variable de entorno CALCUSOL_DATA_ROOT, no el código.

Variables de entorno:
  CALCUSOL_DATA_ROOT   raíz de datos. Por defecto <repo>/data.
                       Para AWS: s3a://mi-bucket/  (con la / final).
"""

from __future__ import annotations

import os
from pathlib import Path

# Raíz del repositorio: este archivo está en src/calcusol/config/settings.py
REPO_ROOT = Path(__file__).resolve().parents[3]

_DEFAULT_DATA_ROOT = str(REPO_ROOT / "data")


def _is_remote(root: str) -> bool:
    return "://" in root


def data_root() -> str:
    """Raíz de datos como string (local o s3://...)."""
    return os.environ.get("CALCUSOL_DATA_ROOT", _DEFAULT_DATA_ROOT)


def _join(root: str, *parts: str) -> str:
    """Une segmentos respetando si la raíz es local o remota (s3://)."""
    if _is_remote(root):
        base = root.rstrip("/")
        return "/".join([base, *parts])
    return str(Path(root, *parts))


def zone_path(zone: str, *parts: str) -> str:
    """
    Ruta dentro de una zona Medallion.

    Ejemplos:
        zone_path("bronze", "nasa")          -> .../data/bronze/nasa
        zone_path("gold", "dataset.parquet") -> .../data/gold/dataset.parquet
    """
    if zone not in {"bronze", "silver", "gold"}:
        raise ValueError(f"Zona desconocida: {zone!r}")
    return _join(data_root(), zone, *parts)


def bronze_path(*parts: str) -> str:
    return zone_path("bronze", *parts)


def silver_path(*parts: str) -> str:
    return zone_path("silver", *parts)


def gold_path(*parts: str) -> str:
    return zone_path("gold", *parts)


def ensure_local_dir(path: str) -> str:
    """
    Crea el directorio si la ruta es local. En remoto (s3://) no hace nada,
    porque los object stores no tienen directorios reales.
    """
    if not _is_remote(path):
        Path(path).mkdir(parents=True, exist_ok=True)
    return path
