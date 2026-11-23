"""Pruebas del registro, la decisión de promoción, la promoción y el rollback (datos sintéticos)."""

from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from calcusol.ml import features as feat
from calcusol.ml import promocion as prom
from calcusol.ml import registro as reg


# ------------------------------------------------------------------ utilidades
def _carpeta_modelo(tmp_path, nombre: str, semilla: int = 0, crlf: bool = False):
    rng = np.random.default_rng(semilla)
    X = rng.normal(size=(300, len(feat.FEATURES)))
    y = X[:, 0] * 2 + rng.normal(size=300)
    m = lgb.LGBMRegressor(n_estimators=5, verbose=-1, random_state=semilla).fit(X, y)
    c = tmp_path / nombre
    c.mkdir()
    m.booster_.save_model(str(c / "modelo_lgbm.txt"))
    if crlf:
        crudo = (c / "modelo_lgbm.txt").read_bytes().replace(b"\r\n", b"\n")   # en Windows LightGBM ya escribe CRLF
        (c / "modelo_lgbm.txt").write_bytes(crudo.replace(b"\n", b"\r\n"))
    (c / "metadatos.json").write_text(json.dumps({"gold_version": "gold-test"}), encoding="utf-8")
    return c


def _niveles(mae_mes, sesgo=0.0):
    n = {"MAE": mae_mes, "RMSE": mae_mes, "R2": 0.9, "sesgo_%": sesgo}
    return {"hora": n, "dia": n, "mes": n}


def _res(mae_mes, mae_nasa=100.0, sesgo=0.0):
    return {ex: {"modelo": _niveles(mae_mes, sesgo), "baseline": _niveles(mae_nasa), "filas": 10}
            for ex in ("test_temporal", "test_spatial", "test_spatiotemporal")}


def _mensual(n=400, mejora=0.10, semilla=1):
    """Tabla estación-mes donde el candidato reduce el error del activo en `mejora` (fracción)."""
    rng = np.random.default_rng(semilla)
    y = rng.uniform(150, 250, n)
    err = rng.normal(0, 15, n)
    t = pd.DataFrame({"y": y, "p_act": y + err, "p_cand": y + err * (1 - mejora)})
    return {"test_spatial": t}


FUGA_OK = {"veredicto": "OK: sin fuga"}
ARTEFACTO_OK = {"ok": True, "sha256": "x"}


@pytest.fixture
def registro(tmp_path):
    r = reg.Registro(str(tmp_path / "registro"))
    r.publicar_version("v1", _carpeta_modelo(tmp_path, "m1", 1))
    r.publicar_version("v2", _carpeta_modelo(tmp_path, "m2", 2))
    return r


# --------------------------------------------------------------------- registro
def test_publicar_no_sobrescribe(registro, tmp_path):
    with pytest.raises(reg.VersionExistente):
        registro.publicar_version("v1", _carpeta_modelo(tmp_path, "otra", 9))
    assert registro.versiones() == ["v1", "v2"]
    assert registro.siguiente_version() == "v3"


def test_modelo_crlf_se_guarda_con_lf_y_carga(tmp_path):
    r = reg.Registro(str(tmp_path / "r"))
    r.publicar_version("v1", _carpeta_modelo(tmp_path, "crlf", 3, crlf=True))
    assert b"\r\n" not in r.leer_bytes(r.clave("v1", "modelo_lgbm.txt"))
    assert prom.comprobar_artefacto(r, "v1")["ok"]


def test_version_invalida(registro, tmp_path):
    with pytest.raises(ValueError):
        registro.publicar_version("final", _carpeta_modelo(tmp_path, "x", 4))


def test_artefacto_corrupto_no_pasa(registro):
    ruta = registro._ruta(registro.clave("v2", "modelo_lgbm.txt"))
    open(ruta, "wb").write(b"no es un modelo")
    assert not prom.comprobar_artefacto(registro, "v2")["ok"]


def test_huella_gold_cambia_con_los_datos(tmp_path):
    g = tmp_path / "gold"
    g.mkdir()
    pd.DataFrame({"a": [1, 2]}).to_parquet(g / "p1.parquet")
    h1 = reg.huella_gold(str(g))
    pd.DataFrame({"a": [1, 2, 3]}).to_parquet(g / "p2.parquet")
    assert reg.huella_gold(str(g))["gold_version"] != h1["gold_version"]
    assert reg.huella_gold(str(g))["gold_version"] == reg.huella_gold(str(g))["gold_version"]


# ---------------------------------------------------------------------- decisión
def test_candidato_mejor_es_aprobado():
    d = prom.decidir(_res(40), _res(46), _mensual(), FUGA_OK, ARTEFACTO_OK, nombre_cand="cand", nombre_act="act")
    assert d["aprobado"], d["fallidos"]


def test_sin_mejora_suficiente_se_rechaza():
    d = prom.decidir(_res(45.9), _res(46), _mensual(mejora=0.0), FUGA_OK, ARTEFACTO_OK)
    assert not d["aprobado"]
    assert "mejora_mae_mes_espacial" in d["fallidos"]


def test_sesgo_peor_se_rechaza():
    cand = _res(40, sesgo=4.0)
    d = prom.decidir(cand, _res(46, sesgo=1.0), _mensual(), FUGA_OK, ARTEFACTO_OK)
    assert "sesgo_no_empeora" in d["fallidos"]


def test_empeorar_examen_temporal_se_rechaza():
    cand = _res(40)
    cand["test_temporal"]["modelo"]["mes"]["MAE"] = 60.0
    d = prom.decidir(cand, _res(46), _mensual(), FUGA_OK, ARTEFACTO_OK)
    assert "no_empeora:test_temporal" in d["fallidos"]


def test_fuga_o_artefacto_malo_se_rechaza():
    assert "sin_fuga" in prom.decidir(_res(40), _res(46), _mensual(), {"veredicto": "FUGA DETECTADA"},
                                      ARTEFACTO_OK)["fallidos"]
    assert "artefacto_carga" in prom.decidir(_res(40), _res(46), _mensual(), FUGA_OK, {"ok": False})["fallidos"]


def test_no_supera_a_nasa_se_rechaza():
    d = prom.decidir(_res(120), None, {}, FUGA_OK, ARTEFACTO_OK)
    assert not d["aprobado"] and "supera_a_nasa:test_spatial" in d["fallidos"]


def test_sin_activo_solo_exige_superar_a_nasa():
    assert prom.decidir(_res(40), None, {}, FUGA_OK, ARTEFACTO_OK)["aprobado"]


def test_umbrales_editables():
    d = prom.decidir(_res(40), _res(46), _mensual(), FUGA_OK, ARTEFACTO_OK, {"mejora_min_mae_mes_pct": 50})
    assert "mejora_mae_mes_espacial" in d["fallidos"]


# ------------------------------------------------------- promoción y rollback
def _decision(registro, version, aprobado=True, activo="v1"):
    registro.escribir_decision(version, {"aprobado": aprobado, "fallidos": [] if aprobado else ["x"], "activo_version": activo})


def test_v1_es_el_activo_implicito_sin_manifiesto(registro):
    assert registro.manifiesto() is None and prom.version_activa(registro) == "v1"


def test_promover_requiere_evaluacion_y_aprobacion(registro):
    with pytest.raises(prom.PromocionRechazada, match="evaluación"):
        prom.promover(registro, "v2", "ana")
    _decision(registro, "v2", aprobado=False)
    with pytest.raises(prom.PromocionRechazada, match="no cumple"):
        prom.promover(registro, "v2", "ana")
    assert registro.manifiesto() is None            # nada cambió


def test_promover_escribe_manifiesto_completo_y_no_toca_versiones(registro):
    antes = registro.leer_bytes(registro.clave("v1", "modelo_lgbm.txt"))
    _decision(registro, "v2")
    m = prom.promover(registro, "v2", "ana", motivo="mejora")
    for k in ("model_version", "model_key", "metadata_key", "metrics_key", "gold_version", "promoted_at", "promoted_by"):
        assert k in m
    assert m["model_version"] == "v2" and m["promoted_by"] == "ana" and m["gold_version"] == "gold-test"
    assert registro.version_activa() == "v2"
    assert registro.leer_bytes(registro.clave("v1", "modelo_lgbm.txt")) == antes
    assert [h["model_version"] for h in registro.historial()] == ["v1"]


def test_decision_vieja_exige_reevaluar(registro, tmp_path):
    registro.publicar_version("v3", _carpeta_modelo(tmp_path, "m3", 3))
    _decision(registro, "v2")
    _decision(registro, "v3")
    prom.promover(registro, "v2", "ana")
    with pytest.raises(prom.PromocionRechazada, match="vuelva a evaluar"):
        prom.promover(registro, "v3", "ana")        # v3 se evaluó contra v1, el activo ya es v2


def test_promocion_forzada_exige_motivo_y_queda_marcada(registro):
    with pytest.raises(prom.PromocionRechazada, match="motivo"):
        prom.promover(registro, "v2", "ana", forzar=True)
    m = prom.promover(registro, "v2", "ana", forzar=True, motivo="piloto")
    assert "forzado" in m["promoted_by"] and m["reason"].startswith("FORZADO")


def test_rollback_vuelve_al_anterior(registro):
    _decision(registro, "v2")
    prom.promover(registro, "v2", "ana")
    m = prom.rollback(registro, "ana", "falló el health")
    assert m["model_version"] == "v1" and registro.version_activa() == "v1"
    assert m["rollback_motivo"] == "falló el health"
    assert registro.versiones() == ["v1", "v2"]     # no se borra nada
    with pytest.raises(RuntimeError):
        prom.rollback(registro, "ana")              # ya no hay anterior


def test_no_se_promueve_la_version_activa(registro):
    with pytest.raises(prom.PromocionRechazada, match="ya es la versión activa"):
        prom.promover(registro, "v1", "ana")


def test_cli_estado(registro, capsys):
    assert prom.main(["estado", "--registro", registro.raiz]) == 0
    assert json.loads(capsys.readouterr().out)["activa"] == "v1"


def test_fijar_hace_explicito_el_activo_sin_cambiarlo(registro):
    m = prom.fijar(registro, "v1", "ana")
    assert m["model_version"] == "v1" and registro.version_activa() == "v1"
    with pytest.raises(prom.PromocionRechazada, match="Ya hay un manifiesto"):
        prom.fijar(registro, "v1", "ana")
    _decision(registro, "v2")
    prom.promover(registro, "v2", "ana")
    assert [h["model_version"] for h in registro.historial()] == ["v1"]       # sin duplicar a v1


def test_fijar_solo_la_version_activa(registro):
    with pytest.raises(prom.PromocionRechazada, match="versión activa actual"):
        prom.fijar(registro, "v2", "ana")
