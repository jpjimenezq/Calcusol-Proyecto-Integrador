"""Toma 5 horas con sol del Gold (2021-2022, años que el modelo no vio) y 1 hora de noche.

Ejecutar desde la raíz del proyecto, con los años 2021 y 2022 ya descargados a api/pruebas/gold/.
"""
import json
from pathlib import Path

import pandas as pd

CARPETA = Path(__file__).parent / "gold"
COLUMNAS = ["Fecha", "Latitud", "Longitud", "Altitud",
            "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M", "Valor"]

df = pd.read_parquet(CARPETA, columns=COLUMNAS)
df["Fecha"] = pd.to_datetime(df["Fecha"])
prueba = df[df["Fecha"].dt.year.isin([2021, 2022])]


def a_caso(r, valor_real):
    # La fecha va tal cual viene del Gold (final de la hora): así la API calcula la misma posición solar.
    return {
        "latitud": float(r["Latitud"]), "longitud": float(r["Longitud"]), "altitud": float(r["Altitud"]),
        "fecha": r["Fecha"].strftime("%Y-%m-%dT%H:%M:%S"),
        "ALLSKY_SFC_SW_DWN": float(r["ALLSKY_SFC_SW_DWN"]), "CLRSKY_SFC_SW_DWN": float(r["CLRSKY_SFC_SW_DWN"]),
        "ALLSKY_KT": float(r["ALLSKY_KT"]), "T2M": float(r["T2M"]),
        "valor_real": valor_real,
    }


# El Gold solo guarda horas de sol (CLRSKY > 0). El caso de noche se arma aparte.
con_sol = prueba.sample(5, random_state=42)
casos = [a_caso(r, float(r["Valor"])) for _, r in con_sol.iterrows()]

noche = a_caso(con_sol.iloc[0], 0.0)  # de noche la radiación medida es 0
noche.update({"fecha": "2022-06-15T02:00:00", "ALLSKY_SFC_SW_DWN": 0.0,
              "CLRSKY_SFC_SW_DWN": 0.0, "ALLSKY_KT": 0.0})
casos.append(noche)

(Path(__file__).parent / "casos.json").write_text(json.dumps(casos, indent=2), encoding="utf-8")
print(f"{len(casos)} casos guardados en api/pruebas/casos.json")
