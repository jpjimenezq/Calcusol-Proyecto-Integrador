"""
ML — Carga del modelo ACTIVO para la API.

La API sirve el modelo que dice el manifiesto del registro (radiacion/active/manifest.json).
Si no hay registro configurado, no hay manifiesto, o cualquier cosa falla (S3 caído, archivo
dañado, sha256 distinto), usa el modelo embebido en la imagen: la API nunca se cae por esto.

  CALCUSOL_MODELOS_ROOT   raíz del registro (s3://calcusol-datos-eafit/modelos). Sin ella, solo el embebido.
  MODELO_RUTA             modelo embebido (respaldo), lo fija la imagen.

Todo el acceso al registro pasa por ml/registro.py: es la misma lógica que usa el entrenamiento.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from calcusol.ml.predict import CorrectorRadiacion

log = logging.getLogger("calcusol.activo")


def _sha256(datos: bytes) -> str:
    import hashlib
    return hashlib.sha256(datos.replace(b"\r\n", b"\n")).hexdigest()


def _info_embebido(ruta: str, origen: str, motivo: str | None = None) -> dict:
    p = Path(ruta)
    meta = {}
    if (p.parent / "metadatos.json").exists():
        meta = json.loads((p.parent / "metadatos.json").read_text(encoding="utf-8"))
    info = {"version": meta.get("version") or p.parent.name, "origen": origen,
            "gold_version": meta.get("gold_version"), "sha256": _sha256(p.read_bytes()),
            "promoted_at": None}
    if motivo:
        info["motivo_respaldo"] = motivo
    return info


def cargar_activo(ruta_embebida: str, raiz_registro: str | None = None) -> tuple[CorrectorRadiacion, dict]:
    """Devuelve (corrector, info) con el modelo del manifiesto o, si no se puede, el embebido."""
    if not raiz_registro:
        return CorrectorRadiacion(ruta_embebida), _info_embebido(ruta_embebida, "embebido")
    try:
        from calcusol.ml.registro import Registro

        registro = Registro(raiz_registro)
        m = registro.manifiesto()
        if m is None:
            return CorrectorRadiacion(ruta_embebida), _info_embebido(ruta_embebida, "embebido", "sin manifiesto")
        datos = registro.leer_bytes(m["model_key"]).replace(b"\r\n", b"\n")
        sha = _sha256(datos)
        if m.get("model_sha256") and m["model_sha256"] != sha:
            raise ValueError(f"sha256 distinto al del manifiesto ({m['model_version']})")
        destino = Path(tempfile.mkdtemp(prefix="calcusol-modelo-")) / "modelo_lgbm.txt"
        destino.write_bytes(datos)
        corrector = CorrectorRadiacion(str(destino))
        info = {"version": m["model_version"], "origen": "registro", "gold_version": m.get("gold_version"),
                "sha256": sha, "promoted_at": m.get("promoted_at")}
        log.info("Modelo activo del registro: %s", info["version"])
        return corrector, info
    except Exception as e:  # el respaldo evita que la API no arranque
        log.warning("No se pudo cargar el modelo del registro (%s: %s); se usa el embebido", type(e).__name__, e)
        return CorrectorRadiacion(ruta_embebida), _info_embebido(ruta_embebida, "embebido", type(e).__name__)
