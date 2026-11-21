"""
ML — Construcción de features (física solar con pvlib).

Portado del entrenamiento validado. Produce las 17 features del modelo de
corrección de radiación, SIN fuga (ninguna usa el target `Valor`):

  NASA (4):     ALLSKY_SFC_SW_DWN, CLRSKY_SFC_SW_DWN, ALLSKY_KT, T2M
  Ubicación (3): Latitud, Longitud, Altitud
  Físicas (5):  zenit, azimut, g0_horiz (extraterrestre horizontal),
                razon_cielo (ALLSKY/CLRSKY), kt_calc (ALLSKY/extraterrestre)
  Temporales (5): hora_sin, hora_cos, dia_sin, dia_cos, Mes

Target: residuo = Valor - ALLSKY_SFC_SW_DWN. La predicción final es
ALLSKY + residuo, recortada a >=0 (ver ml/predict.py).

Decisiones temporales (docs/contrato_datos.md): la posición solar se calcula a
la MITAD de la hora (Fecha + 30 min), con Fecha en hora local de Colombia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pvlib import irradiance, solarposition

FEATURES = [
    "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M",
    "Latitud", "Longitud", "Altitud",
    "zenit", "azimut", "g0_horiz", "razon_cielo", "kt_calc",
    "hora_sin", "hora_cos", "dia_sin", "dia_cos", "Mes",
]
OBJETIVO = "residuo"
BASELINE = "ALLSKY_SFC_SW_DWN"
TZ = "America/Bogota"


def _posicion_solar(d: pd.DataFrame) -> pd.DataFrame:
    """
    Añade zenit, azimut y g0_extra (extraterrestre) calculados a la mitad de la
    hora. Vectorizado por estación (lat/lon/alt fijas) para eficiencia.
    """
    t_mid = pd.to_datetime(d["Fecha"]) + pd.Timedelta(minutes=30)
    idx = pd.DatetimeIndex(t_mid.dt.tz_localize(TZ).dt.tz_convert("UTC"))

    zen = np.empty(len(d)); azi = np.empty(len(d)); g0 = np.empty(len(d))
    cod = d["CodigoEstacion"].to_numpy()
    pos = np.arange(len(d))
    for s in np.unique(cod):
        m = cod == s
        lat, lon, alt = d.loc[m, ["Latitud", "Longitud", "Altitud"]].iloc[0]
        sp = solarposition.get_solarposition(idx[m], lat, lon, altitude=alt)
        zen[pos[m]] = sp["apparent_zenith"].to_numpy()
        azi[pos[m]] = sp["azimuth"].to_numpy()
        g0[pos[m]] = irradiance.get_extra_radiation(idx[m]).to_numpy()
    d = d.copy()
    d["zenit"], d["azimut"], d["g0_extra"] = zen, azi, g0
    return d


def build_features(d: pd.DataFrame, con_target: bool = True) -> pd.DataFrame:
    """
    Construye las 17 features sobre un DataFrame con columnas crudas
    (NASA + ubicación + Fecha/Hora/Mes/DiaDelAnio + CodigoEstacion).

    Filtra a horas de sol (zenit < 90, CLRSKY>0, ALLSKY>0). Si `con_target`,
    añade la columna `residuo` (requiere `Valor`). Devuelve copia.
    """
    d = _posicion_solar(d)
    d = d[(d["zenit"] < 90) & (d["CLRSKY_SFC_SW_DWN"] > 0)
          & (d["ALLSKY_SFC_SW_DWN"] > 0)].copy()

    d["g0_horiz"] = d["g0_extra"] * np.cos(np.radians(d["zenit"])).clip(lower=0)
    d["razon_cielo"] = (d["ALLSKY_SFC_SW_DWN"] / d["CLRSKY_SFC_SW_DWN"]).clip(0, 1.5)
    d["kt_calc"] = (d["ALLSKY_SFC_SW_DWN"]
                    / d["g0_horiz"].replace(0, np.nan)).clip(0, 1.5)
    d["hora_sin"] = np.sin(2 * np.pi * d["Hora"] / 24)
    d["hora_cos"] = np.cos(2 * np.pi * d["Hora"] / 24)
    d["dia_sin"] = np.sin(2 * np.pi * d["DiaDelAnio"] / 365.25)
    d["dia_cos"] = np.cos(2 * np.pi * d["DiaDelAnio"] / 365.25)

    if con_target:
        d["residuo"] = d["Valor"] - d["ALLSKY_SFC_SW_DWN"]

    return d.dropna(subset=["kt_calc"])


def matriz_X(d: pd.DataFrame) -> pd.DataFrame:
    return d[FEATURES].astype("float32")
