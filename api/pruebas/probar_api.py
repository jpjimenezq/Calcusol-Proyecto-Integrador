"""Llama a /predict con api/pruebas/casos.json y compara contra la NASA cruda.

Uso (con la API corriendo):   python api/pruebas/probar_api.py
Guarda el resultado en api/pruebas/resultado_<fecha>.txt
"""
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
RAIZ = Path(__file__).parent
casos = json.loads((RAIZ / "casos.json").read_text(encoding="utf-8"))


def llamar(entrada):
    req = urllib.request.Request(URL + "/predict", data=json.dumps(entrada).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


filas = []
for c in casos:
    entrada = {k: v for k, v in c.items() if k != "valor_real"}
    salida = llamar(entrada)
    filas.append({"fecha": c["fecha"], "real": c["valor_real"], "nasa": c["ALLSKY_SFC_SW_DWN"],
                  "predicho": salida["radiacion_corregida"], "sin_sol": salida["sin_sol"]})

lineas = [f"Pruebas de la API el {date.today()} contra {URL}", ""]
for f in filas:
    lineas.append(f"{f['fecha']} | real {f['real']:.2f} | NASA cruda {f['nasa']:.2f} "
                  f"| predicho {f['predicho']:.2f} | sin_sol {f['sin_sol']}")

con_sol = [f for f in filas if not f["sin_sol"]]
err_modelo = np.mean([abs(f["predicho"] - f["real"]) for f in con_sol])
err_nasa = np.mean([abs(f["nasa"] - f["real"]) for f in con_sol])
lineas += [
    "",
    f"Horas con sol: {len(con_sol)} de {len(filas)}",
    f"Error absoluto medio del modelo (horas con sol): {err_modelo:.2f} Wh/m²",
    f"Error absoluto medio de la NASA cruda (horas con sol): {err_nasa:.2f} Wh/m²",
    "El modelo mejora a la NASA cruda" if err_modelo < err_nasa else "El modelo NO mejora a la NASA cruda: revisar",
]

ruta = RAIZ / f"resultado_{date.today()}.txt"
ruta.write_text("\n".join(lineas), encoding="utf-8")
print("\n".join(lineas))
print("Guardado en", ruta)
