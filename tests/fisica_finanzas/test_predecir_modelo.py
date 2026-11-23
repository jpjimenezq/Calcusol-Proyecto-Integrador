"""R3 usa el mismo modelo que /predict: si recibe un corrector ya cargado, no abre el archivo de la ficha."""
import numpy as np
import pandas as pd

from calcusol.fisica_finanzas import calcusol_r3 as r3


class CorrectorFalso:
    """Devuelve la NASA más 100 W/m² (así se ve que se usó este modelo y no otro)."""

    def predecir(self, df):
        out = df.copy()
        out["radiacion_corregida"] = out["ALLSKY_SFC_SW_DWN"] + 100.0
        return out


def _df():
    return pd.DataFrame({"ALLSKY_SFC_SW_DWN": [200.0, 300.0], "Hora": [10, 11]})


def test_usa_el_corrector_recibido_sin_abrir_el_archivo_de_la_ficha():
    cfg = {"datos": {"modelo": "no/existe/modelo_lgbm.txt"}}      # si lo abriera, fallaría
    out = r3.predecir_modelo(_df(), cfg, corrector=CorrectorFalso())
    assert np.allclose(out["Prediccion"], [300.0, 400.0])
    assert "radiacion_corregida" not in out.columns


def test_recorta_a_1400():
    df = pd.DataFrame({"ALLSKY_SFC_SW_DWN": [1350.0], "Hora": [12]})
    out = r3.predecir_modelo(df, {"datos": {"modelo": "x.txt"}}, corrector=CorrectorFalso())
    assert out["Prediccion"].iloc[0] == 1400.0


def test_respeta_predicciones_ya_hechas():
    df = _df().assign(Prediccion=[1.0, 2.0])
    out = r3.predecir_modelo(df, {"datos": {"modelo": "x.txt"}}, corrector=CorrectorFalso())
    assert out["Prediccion"].tolist() == [1.0, 2.0]
