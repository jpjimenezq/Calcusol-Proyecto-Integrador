"""Flujo completo (entrenar -> evaluar -> decidir -> promover) sobre un Gold sintético pequeño."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from calcusol.ml import evaluate, promocion as prom, registro as reg, splits, train
from calcusol.ml.predict import CorrectorRadiacion


def _gold_sintetico(ruta, n_sitios: int = 15) -> str:
    """Horas de sol de 4 días por mes, 2019-2022, con un residuo que depende de la altitud."""
    rng = np.random.default_rng(0)
    filas = []
    fechas = [pd.Timestamp(a, m, d, h) for a in range(2019, 2023) for m in range(1, 13) for d in (3, 12, 21)
              for h in range(7, 18)]
    f = pd.Series(fechas)
    for i in range(n_sitios):
        alt = 100 + 250 * i
        allsky = rng.uniform(200, 800, len(f))
        t = pd.DataFrame({
            "CodigoEstacion": 1000 + i, "Sitio": 1000 + i, "NombreEstacion": f"E{i}", "Departamento": "X",
            "Latitud": 4.0 + i * 0.3, "Longitud": -75.0 + i * 0.1, "Altitud": float(alt),
            "Fecha": f, "Hora": f.dt.hour, "Mes": f.dt.month, "DiaDelAnio": f.dt.dayofyear,
            "ALLSKY_SFC_SW_DWN": allsky, "CLRSKY_SFC_SW_DWN": allsky * 1.3, "ALLSKY_KT": 0.5,
            "T2M": 20.0, "EsPreliminar": False, "Anio": f.dt.year})
        t["Valor"] = (allsky * (0.9 - alt / 20000) + rng.normal(0, 20, len(f))).clip(0)
        filas.append(t)
    carpeta = ruta / "gold"
    for anio, g in pd.concat(filas).groupby("Anio"):
        (carpeta / f"Anio={anio}").mkdir(parents=True, exist_ok=True)
        g.drop(columns="Anio").to_parquet(carpeta / f"Anio={anio}" / "part-0.parquet")
    return str(carpeta)


@pytest.fixture(scope="module")
def gold(tmp_path_factory):
    return _gold_sintetico(tmp_path_factory.mktemp("g"))


def test_entrenar_crea_version_nueva_con_metadatos_y_no_sobrescribe(gold, tmp_path, monkeypatch):
    monkeypatch.setattr(train, "HP", {**train.HP, "n_estimators": 20, "min_child_samples": 20, "num_leaves": 15})
    registro = reg.Registro(str(tmp_path / "modelos"))
    huella = reg.huella_gold(gold)
    modelo, tr, holdout = train.entrenar("cpu", gold)
    meta = train.guardar(modelo, tr, "v1", registro, huella, holdout)

    assert registro.versiones() == ["v1"]
    assert meta["gold_version"] == huella["gold_version"] and meta["modelo_sha256"] == registro.sha256_modelo("v1")
    assert meta["train"]["anio_max"] <= 2020 and not set(holdout) & set(tr["CodigoEstacion"].unique())
    with pytest.raises(reg.VersionExistente):
        train.guardar(modelo, tr, "v1", registro, huella, holdout)
    assert prom.comprobar_artefacto(registro, "v1")["ok"]


def test_evaluacion_pareada_y_decision_sobre_gold_sintetico(gold, tmp_path, monkeypatch):
    registro = reg.Registro(str(tmp_path / "modelos"))
    huella = reg.huella_gold(gold)
    base = {**train.HP, "min_child_samples": 20, "num_leaves": 15}
    for version, n in (("v1", 3), ("v2", 60)):                 # v1 flojo, v2 mejor
        monkeypatch.setattr(train, "HP", {**base, "n_estimators": n})
        modelo, tr, holdout = train.entrenar("cpu", gold)
        train.guardar(modelo, tr, version, registro, huella, holdout)

    d = prom.evaluar_candidato(registro, "v2", gold, {"remuestreos": 300})
    assert d["activo_version"] == "v1"
    assert registro.metricas("v2")["fuga"]["veredicto"] == "OK: sin fuga"
    assert set(registro.metricas("v2")["examenes"]) == set(evaluate.EXAMENES)
    assert d["aprobado"], d["fallidos"]

    nuevo = prom.promover(registro, "v2", "prueba")
    assert nuevo["model_version"] == "v2" and registro.version_activa() == "v2"
    assert prom.rollback(registro, "prueba")["model_version"] == "v1"

    # el candidato peor que el activo se rechaza
    d2 = prom.evaluar_candidato(registro, "v2", gold, {"remuestreos": 300})            # v2 vs v1 de nuevo: ok
    registro_inv = reg.Registro(str(tmp_path / "inv"))
    registro_inv.publicar_version("v1", registro._ruta(registro.clave("v2")))          # v2 como activo
    registro_inv.publicar_version("v2", registro._ruta(registro.clave("v1")))          # v1 como candidato
    d3 = prom.evaluar_candidato(registro_inv, "v2", gold, {"remuestreos": 300})
    assert d2["aprobado"] and not d3["aprobado"] and "mejora_mae_mes_espacial" in d3["fallidos"]


def test_los_splits_no_comparten_sitios(gold):
    assert splits.verificar_sin_fuga(gold)["sin_fuga_espacial"]
    st = splits.split_espacio_temporal(gold)
    assert not set(st["train"]["Sitio"]) & set(st["test_spatiotemporal"]["Sitio"])


def test_reentrenar_crea_version_evalua_y_promueve_solo_si_cumple(gold, tmp_path, monkeypatch):
    from calcusol.ml import reentrenar

    base = {**train.HP, "min_child_samples": 20, "num_leaves": 15}
    registro = reg.Registro(str(tmp_path / "modelos"))
    monkeypatch.setattr(train, "HP", {**base, "n_estimators": 3})            # v1 flojo, ya existente
    modelo, tr, hold = train.entrenar("cpu", gold)
    train.guardar(modelo, tr, "v1", registro, reg.huella_gold(gold), hold)

    monkeypatch.setattr(train, "HP", {**base, "n_estimators": 60})
    r = reentrenar.reentrenar(registro, gold, auto_promover=False, por="t", umbrales={"remuestreos": 200})
    assert r["version_candidata"] == "v2" and r["aprobado"] and not r["promovido"]
    assert prom.version_activa(registro) == "v1" and registro.manifiesto() is None     # solo evaluó

    assert reentrenar.main(["--gold", gold, "--salida", registro.raiz, "--auto-promover"]) == 0
    assert registro.versiones() == ["v1", "v2", "v3"]
    run = registro.leer_json(registro.clave("runs", "v3.json"))
    assert run["version_candidata"] == "v3" and run["activo_despues"] == prom.version_activa(registro)
