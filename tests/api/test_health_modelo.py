"""/health informa qué modelo está sirviendo la API (usa el modelo v1 del repositorio)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

RAIZ = Path(__file__).resolve().parents[2]


def test_health_con_modelo_embebido(monkeypatch, tmp_path):
    origen = RAIZ / "models" / "radiacion" / "v1"
    carpeta = tmp_path / "v1"          # el metadatos.json de v1 no trae "version": se toma del nombre de la carpeta
    carpeta.mkdir()
    # En Windows el modelo puede traer \r\n y LightGBM no lo lee (la imagen de la API lo corrige con sed).
    (carpeta / "modelo_lgbm.txt").write_bytes((origen / "modelo_lgbm.txt").read_bytes().replace(b"\r\n", b"\n"))
    (carpeta / "metadatos.json").write_bytes((origen / "metadatos.json").read_bytes())
    monkeypatch.setenv("MODELO_RUTA", str(carpeta / "modelo_lgbm.txt"))
    monkeypatch.delenv("CALCUSOL_MODELOS_ROOT", raising=False)
    monkeypatch.syspath_prepend(str(RAIZ / "api"))
    sys.modules.pop("main", None)
    from fastapi.testclient import TestClient

    import main

    r = TestClient(main.app).get("/health").json()
    assert r["estado"] == "ok" and r["modelo"].startswith("radiacion-v1")
    assert r["modelo_version"] == "v1" and r["modelo_origen"] == "embebido" and len(r["modelo_sha256"]) == 64


def test_pruebas_de_humo_pasan_contra_la_api_real(monkeypatch, tmp_path):
    """Los payloads de humo (ml/publicar.py) deben ser válidos para la API real."""
    from fastapi.testclient import TestClient

    from calcusol.ml import publicar

    test_health_con_modelo_embebido(monkeypatch, tmp_path)
    import main

    cliente = TestClient(main.app)

    def http(url, datos=None, timeout=60):
        ruta = url.replace("http://api", "")
        r = cliente.post(ruta, json=datos) if datos is not None else cliente.get(ruta)
        r.raise_for_status()
        return r.status_code, r.json()

    h = publicar.humo("http://api", "v1", http)
    assert h["ok"], h
    assert not publicar.humo("http://api", "v9", http)["ok"]       # sirve v1, no v9
