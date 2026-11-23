"""
ML — Reentrenamiento completo: entrenar -> evaluar contra el activo -> decidir -> (promover).

Es el punto de entrada del contenedor de AWS (Fargate) y también corre igual en local:
une los pasos de ml/train.py y ml/promocion.py sin duplicar su lógica.

Nunca cambia el modelo activo salvo que el candidato cumpla los criterios Y se pida
--auto-promover. Un candidato rechazado NO es un error: la corrida termina bien, el resultado
queda en radiacion/runs/<version>.json y el modelo activo sigue igual. Solo un fallo real
(datos, memoria, permisos) devuelve un código distinto de 0.

EJECUCIÓN:
  python -m calcusol.ml.reentrenar --gold s3://calcusol-datos-eafit/gold/dataset \\
      --salida s3://calcusol-datos-eafit/modelos --auto-promover
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from calcusol.ml import promocion as prom
from calcusol.ml import registro as reg
from calcusol.ml import splits, train


def reentrenar(registro: reg.Registro, ruta_gold: str, auto_promover: bool, por: str,
               version: str | None = None, device: str = "cpu", umbrales: dict | None = None) -> dict:
    version = version or registro.siguiente_version()
    if registro.existe_version(version):
        raise reg.VersionExistente(f"{version} ya existe en {registro.raiz}; no se sobrescribe.")
    gold = reg.huella_gold(ruta_gold)
    print(f"Gold {gold['gold_version']} ({gold['gold_archivos']} archivos) -> entrenando {version}", flush=True)
    modelo, tr, holdout = train.entrenar(device, ruta_gold)
    train.guardar(modelo, tr, version, registro, gold, holdout)

    decision = prom.evaluar_candidato(registro, version, ruta_gold, umbrales)
    resumen = {
        "version_candidata": version, "gold_version": gold["gold_version"],
        "activo_antes": decision["activo_version"], "aprobado": decision["aprobado"],
        "criterios_fallidos": decision["fallidos"], "promovido": False,
        "evaluado_utc": decision["evaluado_utc"],
    }
    if decision["aprobado"] and auto_promover:
        manifiesto = prom.promover(registro, version, por, motivo="reentrenamiento automático")
        resumen.update(promovido=True, manifiesto=manifiesto)
    resumen["activo_despues"] = prom.version_activa(registro)
    registro.guardar_json(registro.clave("runs", f"{version}.json"), resumen)
    return resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reentrena, evalúa y (si cumple) promueve el modelo de radiación.")
    ap.add_argument("--gold", default=os.environ.get("CALCUSOL_GOLD_RUTA") or splits.DATASET)
    ap.add_argument("--salida", default=None, help="raíz del registro (por defecto CALCUSOL_MODELOS_ROOT o ./models)")
    ap.add_argument("--version")
    ap.add_argument("--device", default="cpu", choices=["cpu", "gpu"])
    ap.add_argument("--auto-promover", action="store_true",
                    help="promueve el candidato si cumple los criterios (si no, solo evalúa)")
    ap.add_argument("--por", default="reentrenamiento-automatico")
    ap.add_argument("--umbrales", help="JSON con umbrales que reemplazan a los de por defecto")
    a = ap.parse_args(argv)
    umbrales = json.load(open(a.umbrales, encoding="utf-8")) if a.umbrales else None
    resumen = reentrenar(reg.Registro(a.salida), a.gold, a.auto_promover, a.por, a.version, a.device, umbrales)
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    estado = "PROMOVIDO" if resumen["promovido"] else ("APROBADO (sin promover)" if resumen["aprobado"] else "RECHAZADO")
    print(f"\n{resumen['version_candidata']}: {estado}. Modelo activo: {resumen['activo_despues']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
