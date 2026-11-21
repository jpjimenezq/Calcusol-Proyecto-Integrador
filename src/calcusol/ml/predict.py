"""
ML — Carga del modelo y predicción.

Punto único que usan la API y los módulos de física/finanzas para obtener
radiación corregida. Encapsula el detalle de que el modelo predice un RESIDUO:
el consumidor solo recibe la radiación corregida (W/m²), no tiene que saber la
mecánica interna.

Flujo: features (pvlib) -> residuo predicho -> ALLSKY + residuo -> clip >=0.
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from calcusol.ml import features as feat

MODELO_DEFECTO = str(Path("models") / "radiacion" / "v1" / "modelo_lgbm.txt")


class CorrectorRadiacion:
    """Carga el modelo entrenado y corrige la radiación de NASA."""

    def __init__(self, ruta_modelo: str = MODELO_DEFECTO):
        p = Path(ruta_modelo)
        if not p.exists():
            raise FileNotFoundError(
                f"No existe el modelo en {p}. Entrénalo con "
                f"`python -m calcusol.ml.train`.")
        self.booster = lgb.Booster(model_file=str(p))

    def predecir(self, df_crudo: pd.DataFrame) -> pd.DataFrame:
        """
        Recibe un DataFrame con columnas crudas (NASA + ubicación + Fecha, Hora,
        Mes, DiaDelAnio, CodigoEstacion) y devuelve el mismo df (filtrado a horas
        de sol) con una columna `radiacion_corregida` en W/m².
        """
        d = feat.build_features(df_crudo, con_target=False)
        residuo = self.booster.predict(feat.matriz_X(d))
        d["radiacion_corregida"] = np.clip(
            d["ALLSKY_SFC_SW_DWN"].to_numpy() + residuo, 0, None)
        return d


def cargar(ruta_modelo: str = MODELO_DEFECTO) -> CorrectorRadiacion:
    return CorrectorRadiacion(ruta_modelo)
