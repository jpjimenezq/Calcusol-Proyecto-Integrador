"""
ML — Publicación del modelo promovido en la API, con pruebas de humo y rollback automático.

Después de promover una versión (ml/promocion.py), la API todavía sirve el modelo con el que
arrancó. Este paso:

  1. (opcional) promueve la versión;
  2. redespliega la API (ECS) para que lea el manifiesto;
  3. espera a que el despliegue termine y corre las pruebas de humo (/health, /predict, /v1/...);
  4. si algo falla: rollback del manifiesto, redespliegue y nueva prueba de humo.

La API nunca se queda sin modelo: ante cualquier problema de lectura usa el embebido (ml/activo.py).

EJECUCIÓN:
  python -m calcusol.ml.publicar humo --api-url https://ca-....ecs.us-east-1.on.aws
  python -m calcusol.ml.publicar publicar --version v2 --api-url https://... --registro s3://calcusol-datos-eafit/modelos
  python -m calcusol.ml.publicar publicar --ya-promovido --api-url ...      # solo redespliega y verifica el activo
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import sys
import time
import urllib.error
import urllib.request
from typing import Callable

from calcusol.ml import promocion as prom
from calcusol.ml import registro as reg

CLIMATOLOGIA = {
    "ubicacion": {"latitud": 6.25, "longitud": -75.57, "altitud": 1495},
    "climatologia": {"horas": [10, 11, 12, 13, 14],
                     "ghi": [[500, 600, 650, 600, 500]] * 12, "t_aire": [[24, 25, 26, 26, 25]] * 12},
}
PREDICT = {"latitud": 6.25, "longitud": -75.57, "altitud": 1495, "fecha": "2022-06-15T13:00:00",
           "ALLSKY_SFC_SW_DWN": 620.0, "CLRSKY_SFC_SW_DWN": 900.0, "ALLSKY_KT": 0.55, "T2M": 24.0}


def _http(url: str, datos: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(url, data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Content-Type": "application/json"} if datos is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def humo(api_url: str, version_esperada: str | None = None, http: Callable = _http) -> dict:
    """Pruebas de humo: /health (y que sirva la versión esperada), /predict y los endpoints de R3."""
    api_url = api_url.rstrip("/")
    comprobaciones: list[dict] = []

    def prueba(nombre, fn):
        try:
            ok, detalle = fn()
        except Exception as e:
            ok, detalle = False, f"{type(e).__name__}: {e}"
        comprobaciones.append({"prueba": nombre, "ok": bool(ok), "detalle": detalle})

    def health():
        _, h = http(f"{api_url}/health")
        ok = h.get("estado") == "ok" and (version_esperada is None or h.get("modelo_version") == version_esperada)
        return ok, {k: h.get(k) for k in ("modelo_version", "modelo_origen", "gold_version")}

    def predict():
        _, p = http(f"{api_url}/predict", PREDICT)
        v = p.get("radiacion_corregida")
        return (isinstance(v, (int, float)) and math.isfinite(v) and 0 < v < 1500 and not p.get("sin_sol")), v

    def parametros():
        s, p = http(f"{api_url}/v1/parametros")
        return s == 200 and bool(p), f"HTTP {s}"

    def simulacion():
        s, p = http(f"{api_url}/v1/simulacion/climatologia", CLIMATOLOGIA)
        return s == 200 and bool(p), f"HTTP {s}"

    prueba("health", health)
    prueba("predict", predict)
    prueba("r3_parametros", parametros)
    prueba("r3_simulacion_climatologia", simulacion)
    return {"ok": all(c["ok"] for c in comprobaciones), "comprobaciones": comprobaciones}


def publicar(registro: reg.Registro, api_url: str, desplegar: Callable[[], None], esperar: Callable[[], None],
             version: str | None = None, por: str = "publicacion", http: Callable = _http,
             probar: Callable = humo) -> dict:
    """Promueve (si se da `version`), redespliega y verifica; si falla, vuelve al modelo anterior."""
    resultado: dict = {"promovido": None, "rollback": False}
    if version:
        resultado["promovido"] = prom.promover(registro, version, por, motivo="publicación en la API")["model_version"]
    esperada = prom.version_activa(registro)

    desplegar()
    esperar()
    h = probar(api_url, esperada, http)
    resultado["humo"] = h
    if h["ok"]:
        resultado["servida"] = esperada
        return resultado

    if not version:                                          # nada que revertir: esta publicación no promovió
        resultado["servida"] = esperada
        return resultado

    resultado["rollback"] = True
    prom.rollback(registro, por, "falló la prueba de humo tras publicar")
    anterior = prom.version_activa(registro)
    desplegar()
    esperar()
    resultado["humo_tras_rollback"] = probar(api_url, anterior, http)
    resultado["servida"] = anterior
    return resultado


# ---------------------------------------------------------------- ECS (boto3)
def _ecs(perfil: str | None, region: str):
    import boto3
    return boto3.Session(profile_name=perfil, region_name=region).client("ecs")


def redesplegar_ecs(cluster: str, servicio: str, perfil: str | None, region: str = "us-east-1") -> None:
    _ecs(perfil, region).update_service(cluster=cluster, service=servicio, forceNewDeployment=True)


def esperar_ecs(cluster: str, servicio: str, perfil: str | None, region: str = "us-east-1",
                maximo: int = 1200) -> None:
    ecs = _ecs(perfil, region)
    t0 = time.time()
    time.sleep(10)
    while time.time() - t0 < maximo:
        s = ecs.describe_services(cluster=cluster, services=[servicio])["services"][0]
        d = s["deployments"]
        if len(d) == 1 and d[0].get("rolloutState", "COMPLETED") == "COMPLETED" and s["runningCount"] >= s["desiredCount"] > 0:
            return
        if any(x.get("rolloutState") == "FAILED" for x in d):
            raise RuntimeError("El despliegue de ECS falló")
        time.sleep(15)
    raise TimeoutError("El despliegue de ECS tardó más de lo esperado")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publica el modelo activo en la API y verifica con pruebas de humo.")
    ap.add_argument("accion", choices=["humo", "publicar"])
    ap.add_argument("--api-url", required=True)
    ap.add_argument("--version", help="versión a promover y publicar")
    ap.add_argument("--ya-promovido", action="store_true", help="no promueve: solo redespliega y verifica el activo")
    ap.add_argument("--registro", help="raíz del registro (local o s3://...)")
    ap.add_argument("--cluster", default="default")
    ap.add_argument("--servicio", default="calcusol-api")
    ap.add_argument("--perfil", default="calcusol-despliegue")
    ap.add_argument("--por", default=getpass.getuser())
    a = ap.parse_args(argv)

    if a.accion == "humo":
        r = humo(a.api_url, a.version)
    else:
        if not (a.version or a.ya_promovido):
            ap.error("indique --version o --ya-promovido")
        r = publicar(reg.Registro(a.registro), a.api_url,
                     lambda: redesplegar_ecs(a.cluster, a.servicio, a.perfil),
                     lambda: esperar_ecs(a.cluster, a.servicio, a.perfil),
                     None if a.ya_promovido else a.version, a.por)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    ok = r["ok"] if a.accion == "humo" else (r["humo"]["ok"] and not r["rollback"])
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
