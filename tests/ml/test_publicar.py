"""Publicación en la API: humo, redespliegue y rollback automático si falla (con una API simulada)."""

from __future__ import annotations

import pytest

from calcusol.ml import promocion as prom, publicar, registro as reg
from test_registro_promocion import _carpeta_modelo


@pytest.fixture
def registro(tmp_path):
    r = reg.Registro(str(tmp_path / "registro"))
    r.publicar_version("v1", _carpeta_modelo(tmp_path, "m1", 1))
    r.publicar_version("v2", _carpeta_modelo(tmp_path, "m2", 2))
    r.guardar_json(r.clave("v2", "decision.json"), {"aprobado": True, "fallidos": [], "activo_version": "v1"})
    return r


class ApiSimulada:
    """Sirve la versión que había al último redespliegue; `rota` hace que v2 falle en /predict."""

    def __init__(self, registro, rota=False):
        self.registro, self.rota, self.sirve, self.despliegues = registro, rota, "v1", 0

    def desplegar(self):
        self.despliegues += 1
        self.sirve = prom.version_activa(self.registro)

    def probar(self, url, esperada, http):
        ok = self.sirve == esperada and not (self.rota and self.sirve == "v2")
        return {"ok": ok, "comprobaciones": [{"prueba": "health", "ok": ok, "detalle": self.sirve}]}


def test_publicacion_correcta(registro):
    api = ApiSimulada(registro)
    r = publicar.publicar(registro, "http://api", api.desplegar, lambda: None, "v2", "ana", probar=api.probar)
    assert r["servida"] == "v2" and not r["rollback"] and r["promovido"] == "v2" and api.despliegues == 1


def test_si_falla_el_humo_revierte_y_redespliega(registro):
    api = ApiSimulada(registro, rota=True)
    r = publicar.publicar(registro, "http://api", api.desplegar, lambda: None, "v2", "ana", probar=api.probar)
    assert r["rollback"] and r["servida"] == "v1" and r["humo_tras_rollback"]["ok"]
    assert prom.version_activa(registro) == "v1" and api.despliegues == 2
    assert registro.versiones() == ["v1", "v2"]                       # nada se borra


def test_ya_promovido_con_humo_fallido_no_toca_el_manifiesto(registro):
    prom.promover(registro, "v2", "ana")
    api = ApiSimulada(registro, rota=True)
    r = publicar.publicar(registro, "http://api", api.desplegar, lambda: None, None, "ana", probar=api.probar)
    assert not r["humo"]["ok"] and not r["rollback"] and prom.version_activa(registro) == "v2"


def test_humo_detecta_respuestas_malas():
    def http(url, datos=None, timeout=60):
        if url.endswith("/health"):
            return 200, {"estado": "ok", "modelo_version": "v2"}
        if url.endswith("/predict"):
            return 200, {"radiacion_corregida": float("nan"), "sin_sol": False}
        return 200, {"x": 1}
    h = publicar.humo("http://api", "v2", http)
    assert not h["ok"] and [c["prueba"] for c in h["comprobaciones"] if not c["ok"]] == ["predict"]
