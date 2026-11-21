"""
Manifest de ingesta Bronze.

Registra cada archivo incorporado a Bronze con su procedencia y huella, para que
la ingesta sea auditable y reproducible: qué se pidió, cuándo, con qué resultado
y con qué checksum. Es un JSONL (una línea por entrada) append-only.

No contiene lógica de descarga ni de transformación: solo registra hechos.

Portabilidad: hoy escribe en el sistema de archivos local. La ruta del manifest
se resuelve por settings; para AWS se puede apuntar a una ruta montada o
sustituir el backend sin cambiar a los llamadores.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from calcusol.config import settings


def _manifest_path(fuente: str) -> Path:
    """Ruta local del manifest de una fuente (bronze/<fuente>/_manifest.jsonl)."""
    ruta = settings.bronze_path(fuente, "_manifest.jsonl")
    if "://" in ruta:
        raise NotImplementedError(
            "El manifest solo soporta backend local por ahora; "
            "para s3:// hace falta un backend de objetos."
        )
    return Path(ruta)


def checksum_sha256(ruta: str | Path) -> str:
    """SHA-256 de un archivo, leído por bloques (sirve para archivos grandes)."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def registrar(fuente: str, entrada: dict[str, Any]) -> None:
    """
    Añade una entrada al manifest de una fuente. Agrega marca de tiempo UTC.

    `entrada` es libre por fuente, pero por convención incluye:
      archivo, estado, y los parámetros de la consulta (coordenadas, variables,
      periodo) más tamaño y checksum cuando el archivo existe.
    """
    path = _manifest_path(fuente)
    path.parent.mkdir(parents=True, exist_ok=True)
    registro = {"registrado_utc": datetime.now(timezone.utc).isoformat(), **entrada}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def leer(fuente: str) -> list[dict[str, Any]]:
    """Lee todas las entradas del manifest de una fuente (vacío si no existe)."""
    path = _manifest_path(fuente)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def ya_registrado_ok(fuente: str, archivo: str) -> bool:
    """True si el archivo ya tiene una entrada con estado OK en el manifest."""
    nombre = Path(archivo).name
    return any(
        Path(e.get("archivo", "")).name == nombre and e.get("estado") == "ok"
        for e in leer(fuente)
    )
