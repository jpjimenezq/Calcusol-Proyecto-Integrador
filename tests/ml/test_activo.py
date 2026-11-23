"""El modelo que sirve la API: el del manifiesto, con respaldo embebido ante cualquier fallo."""

from __future__ import annotations

import json

import pytest

from calcusol.ml import activo, promocion as prom, registro as reg
from test_registro_promocion import _carpeta_modelo


@pytest.fixture
def entorno(tmp_path):
    emb = _carpeta_modelo(tmp_path, "embebido", 1)
    (emb / "metadatos.json").write_text(json.dumps({"version": "v1", "gold_version": "g0"}), encoding="utf-8")
    r = reg.Registro(str(tmp_path / "registro"))
    r.publicar_version("v1", emb)
    r.publicar_version("v2", _carpeta_modelo(tmp_path, "m2", 2))
    return emb / "modelo_lgbm.txt", r


def test_sin_registro_usa_el_embebido(entorno):
    emb, _ = entorno
    _, info = activo.cargar_activo(str(emb))
    assert info["version"] == "v1" and info["origen"] == "embebido"


def test_sin_manifiesto_usa_el_embebido(entorno):
    emb, r = entorno
    _, info = activo.cargar_activo(str(emb), r.raiz)
    assert info["origen"] == "embebido" and info["motivo_respaldo"] == "sin manifiesto"


def test_con_manifiesto_sirve_el_modelo_promovido(entorno):
    emb, r = entorno
    r.guardar_json(r.clave("v2", "decision.json"), {"aprobado": True, "fallidos": [], "activo_version": "v1"})
    prom.promover(r, "v2", "ana")
    _, info = activo.cargar_activo(str(emb), r.raiz)
    assert info["version"] == "v2" and info["origen"] == "registro" and info["promoted_at"]
    assert info["sha256"] == r.sha256_modelo("v2")


def test_rollback_vuelve_a_servir_v1(entorno):
    emb, r = entorno
    r.guardar_json(r.clave("v2", "decision.json"), {"aprobado": True, "fallidos": [], "activo_version": "v1"})
    prom.promover(r, "v2", "ana")
    prom.rollback(r, "ana", "prueba")
    assert activo.cargar_activo(str(emb), r.raiz)[1]["version"] == "v1"


def test_sha256_distinto_cae_al_embebido(entorno):
    emb, r = entorno
    r.guardar_json(r.clave("v2", "decision.json"), {"aprobado": True, "fallidos": [], "activo_version": "v1"})
    prom.promover(r, "v2", "ana")
    m = r.manifiesto()
    m["model_sha256"] = "0" * 64
    r.guardar_json(r.clave_manifiesto, m)
    _, info = activo.cargar_activo(str(emb), r.raiz)
    assert info["origen"] == "embebido" and info["version"] == "v1" and info["motivo_respaldo"] == "ValueError"


def test_registro_inaccesible_cae_al_embebido(entorno, tmp_path):
    emb, _ = entorno
    r = reg.Registro(str(tmp_path / "registro_malo"))
    (tmp_path / "registro_malo" / "radiacion" / "active").mkdir(parents=True)
    (tmp_path / "registro_malo" / "radiacion" / "active" / "manifest.json").write_text("{no es json", encoding="utf-8")
    assert activo.cargar_activo(str(emb), r.raiz)[1]["origen"] == "embebido"
