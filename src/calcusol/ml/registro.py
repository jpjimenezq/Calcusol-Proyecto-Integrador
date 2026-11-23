"""
ML — Registro de versiones del modelo de radiación (local o S3, misma lógica).

Estructura bajo la raíz del registro:

    radiacion/v1/modelo_lgbm.txt | metadatos.json | metricas.json
    radiacion/v2/...                       (cada versión es independiente e inmutable)
    radiacion/active/manifest.json         (puntero explícito al modelo en servicio)
    radiacion/active/historial.json        (manifiestos anteriores, para el rollback)

La raíz sale de la variable CALCUSOL_MODELOS_ROOT:
  local (por defecto)  <repo>/models
  AWS                  s3://calcusol-datos-eafit/modelos

Todo el acceso pasa por fsspec, así que local y S3 ejecutan exactamente el mismo
código. Una versión ya escrita NUNCA se sobrescribe; promover solo cambia el
manifiesto.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import fsspec

from calcusol.config import settings

ENV_RAIZ = "CALCUSOL_MODELOS_ROOT"
FAMILIA = "radiacion"
ARCHIVO_MODELO = "modelo_lgbm.txt"
ARCHIVO_METADATOS = "metadatos.json"
ARCHIVO_METRICAS = "metricas.json"
_PATRON_VERSION = re.compile(r"^v(\d+)$")


class VersionExistente(RuntimeError):
    """Se intentó escribir una versión que ya existe (no se sobrescribe)."""


def raiz_defecto() -> str:
    return os.environ.get(ENV_RAIZ, str(settings.REPO_ROOT / "models"))


def _normalizar(raiz: str) -> str:
    return ("s3://" + raiz[len("s3a://"):]) if raiz.startswith("s3a://") else raiz


def sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def ahora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registro:
    def __init__(self, raiz: str | None = None):
        self.raiz = _normalizar(raiz or raiz_defecto()).rstrip("/\\")
        self.fs, self._base = fsspec.core.url_to_fs(self.raiz)

    # ----------------------------------------------------------------- rutas
    def clave(self, *partes: str) -> str:
        """Clave relativa a la raíz (es la que se guarda en el manifiesto)."""
        return "/".join([FAMILIA, *partes])

    def _ruta(self, clave: str) -> str:
        return f"{self._base.rstrip('/')}/{clave}"

    def existe(self, clave: str) -> bool:
        return self.fs.exists(self._ruta(clave))

    # ------------------------------------------------------------ lectura/escritura
    def leer_bytes(self, clave: str) -> bytes:
        with self.fs.open(self._ruta(clave), "rb") as f:
            return f.read()

    def leer_json(self, clave: str) -> dict | list:
        return json.loads(self.leer_bytes(clave).decode("utf-8"))

    def _escribir_bytes(self, clave: str, datos: bytes) -> None:
        ruta = self._ruta(clave)
        if "://" not in self.raiz:
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        with self.fs.open(ruta, "wb") as f:
            f.write(datos)

    def _escribir_json(self, clave: str, obj) -> None:
        self._escribir_bytes(clave, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))

    # --------------------------------------------------------------- versiones
    def versiones(self) -> list[str]:
        base = self._ruta(FAMILIA)
        if not self.fs.exists(base):
            return []
        nombres = [Path(p.rstrip("/")).name for p in self.fs.ls(base, detail=False)]
        v = sorted((int(m.group(1)) for n in nombres if (m := _PATRON_VERSION.match(n))))
        return [f"v{n}" for n in v]

    def siguiente_version(self) -> str:
        v = self.versiones()
        return f"v{int(v[-1][1:]) + 1}" if v else "v1"

    def existe_version(self, version: str) -> bool:
        return self.existe(self.clave(version, ARCHIVO_MODELO))

    def publicar_version(self, version: str, carpeta_local: str | Path) -> dict:
        """
        Copia una carpeta local (modelo_lgbm.txt + metadatos.json [+ metricas.json])
        a la versión indicada. Falla si la versión ya existe: nunca sobrescribe.
        El modelo se normaliza a saltos de línea LF (LightGBM no lee CRLF).
        """
        if not _PATRON_VERSION.match(version):
            raise ValueError(f"Versión inválida: {version!r} (use v1, v2, ...)")
        if self.existe_version(version):
            raise VersionExistente(f"{version} ya existe en {self.raiz}; use una versión nueva.")
        carpeta = Path(carpeta_local)
        modelo = (carpeta / ARCHIVO_MODELO).read_bytes().replace(b"\r\n", b"\n")
        # los metadatos van primero y el modelo al final: la versión "existe" solo cuando está completa
        for nombre in (ARCHIVO_METADATOS, ARCHIVO_METRICAS):
            if (carpeta / nombre).exists():
                self._escribir_bytes(self.clave(version, nombre), (carpeta / nombre).read_bytes())
        self._escribir_bytes(self.clave(version, ARCHIVO_MODELO), modelo)
        return {"version": version, "sha256": sha256_bytes(modelo)}

    def escribir_metricas(self, version: str, metricas: dict) -> None:
        """Las métricas de una versión se escriben una sola vez."""
        clave = self.clave(version, ARCHIVO_METRICAS)
        if self.existe(clave):
            raise VersionExistente(f"{version} ya tiene metricas.json.")
        self._escribir_json(clave, metricas)

    def guardar_json(self, clave: str, obj) -> None:
        self._escribir_json(clave, obj)

    def escribir_decision(self, version: str, decision: dict) -> None:
        """Veredicto de promoción frente al activo de ese momento (puede repetirse)."""
        self._escribir_json(self.clave(version, "decision.json"), decision)

    def decision(self, version: str) -> dict | None:
        clave = self.clave(version, "decision.json")
        return self.leer_json(clave) if self.existe(clave) else None

    def metadatos(self, version: str) -> dict:
        return self.leer_json(self.clave(version, ARCHIVO_METADATOS))

    def metricas(self, version: str) -> dict | None:
        clave = self.clave(version, ARCHIVO_METRICAS)
        return self.leer_json(clave) if self.existe(clave) else None

    def descargar_modelo(self, version: str, destino: str | Path | None = None) -> str:
        """Deja el modelo de la versión en un archivo local y devuelve su ruta."""
        clave = self.clave(version, ARCHIVO_MODELO)
        if "://" not in self.raiz and destino is None:
            return self._ruta(clave)
        destino = Path(destino or tempfile.mkdtemp(prefix=f"calcusol-{version}-")) / ARCHIVO_MODELO
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(self.leer_bytes(clave))
        return str(destino)

    # ---------------------------------------------------------------- manifiesto
    @property
    def clave_manifiesto(self) -> str:
        return self.clave("active", "manifest.json")

    @property
    def clave_historial(self) -> str:
        return self.clave("active", "historial.json")

    def manifiesto(self) -> dict | None:
        """Manifiesto del modelo activo; None si todavía no se ha promovido ninguno."""
        return self.leer_json(self.clave_manifiesto) if self.existe(self.clave_manifiesto) else None

    def historial(self) -> list[dict]:
        return self.leer_json(self.clave_historial) if self.existe(self.clave_historial) else []

    def sha256_modelo(self, version: str) -> str:
        """sha256 del modelo con saltos LF (igual en Windows y Linux)."""
        return sha256_bytes(self.leer_bytes(self.clave(version, ARCHIVO_MODELO)).replace(b"\r\n", b"\n"))

    def construir_manifiesto(self, version: str, promovido_por: str, motivo: str = "") -> dict:
        meta = self.metadatos(version)
        return {
            "model_version": version,
            "model_key": self.clave(version, ARCHIVO_MODELO),
            "metadata_key": self.clave(version, ARCHIVO_METADATOS),
            "metrics_key": self.clave(version, ARCHIVO_METRICAS),
            "gold_version": meta.get("gold_version"),
            "model_sha256": meta.get("modelo_sha256") or self.sha256_modelo(version),
            "promoted_at": ahora_utc(),
            "promoted_by": promovido_por,
            "reason": motivo,
        }

    def escribir_manifiesto(self, manifiesto: dict) -> None:
        """Guarda el manifiesto activo y apila el anterior en el historial."""
        previo = self.manifiesto()
        if previo is not None:
            self._escribir_json(self.clave_historial, self.historial() + [previo])
        self._escribir_json(self.clave_manifiesto, manifiesto)

    def retirar_manifiesto_actual(self, extra: dict | None = None) -> dict:
        """Rollback: el manifiesto anterior del historial pasa a ser el activo."""
        h = self.historial()
        if not h:
            raise RuntimeError("No hay una versión anterior a la que volver.")
        anterior = dict(h[-1], restored_at=ahora_utc(), **(extra or {}))
        self._escribir_json(self.clave_historial, h[:-1])
        self._escribir_json(self.clave_manifiesto, anterior)
        return anterior

    def version_activa(self) -> str | None:
        m = self.manifiesto()
        return m["model_version"] if m else None


# ------------------------------------------------------------------ gold
def huella_gold(ruta: str) -> dict:
    """
    Identifica la versión de Gold usada (para metadatos/auditoría): hash de la lista
    de archivos con su tamaño (y ETag en S3). Cambia si llegan datos nuevos.
    """
    ruta = _normalizar(ruta)
    fs, base = fsspec.core.url_to_fs(ruta)
    archivos = sorted(p for p in fs.find(base) if p.endswith(".parquet"))
    h = hashlib.sha256()
    total = 0
    for p in archivos:
        info = fs.info(p)
        total += int(info.get("size", 0))
        h.update(f"{p.removeprefix(base)}|{info.get('size', 0)}|{info.get('ETag', '')}".encode())
    return {"gold_version": f"gold-{h.hexdigest()[:12]}", "gold_ruta": ruta,
            "gold_archivos": len(archivos), "gold_bytes": total}

