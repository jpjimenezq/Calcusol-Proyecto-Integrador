"""API de Calcusol.

- /predict: corrige la radiación de NASA con el modelo de R1 (LightGBM).
- /v1/...: física y finanzas de R3 (producción, consumo, ahorro, VPN y payback).

Toda la lógica de cálculo vive en calcusol.ml y calcusol.fisica_finanzas (src/).
Aquí solo se valida la entrada y se devuelve la salida.
"""
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from calcusol.fisica_finanzas import calcusol_r3 as r3
from calcusol.ml import features as feat
from calcusol.ml.predict import CorrectorRadiacion

RUTA_MODELO = os.environ.get("MODELO_RUTA", "/app/models/radiacion/v1/modelo_lgbm.txt")
corrector = CorrectorRadiacion(RUTA_MODELO)
VERSION = "radiacion-v1 (LightGBM, variante A0, 17 features)"

CFG = r3.cargar_config()  # ficha de supuestos empaquetada con calcusol

app = FastAPI(title="Calcusol · API", version="1.1.0")


# ---------------------------------------------------------------- radiación (R1)
class Entrada(BaseModel):
    latitud: float = Field(ge=-5, le=14, description="Colombia: -5 a 14")
    longitud: float = Field(ge=-82, le=-66, description="Colombia: -82 a -66")
    altitud: float = Field(ge=-50, le=6000, description="metros sobre el nivel del mar")
    # Final de la hora, hora local de Colombia, sin zona horaria.
    # Ejemplo: 2022-06-15T13:00:00 corresponde a la hora 12:00-13:00.
    fecha: datetime
    ALLSKY_SFC_SW_DWN: float = Field(ge=0, description="NASA, Wh/m²")
    CLRSKY_SFC_SW_DWN: float = Field(ge=0, description="NASA cielo despejado, Wh/m²")
    ALLSKY_KT: float = Field(ge=0, le=1)
    T2M: float = Field(description="Temperatura a 2 m, °C")


class Salida(BaseModel):
    radiacion_corregida: float = Field(description="Wh/m²")
    sin_sol: bool = Field(description="True si R1 descarta la hora (noche o radiación 0); en ese caso la respuesta es 0")
    modelo: str


@app.get("/health")
def health():
    return {"estado": "ok", "modelo": VERSION}


@app.post("/predict", response_model=Salida)
def predict(e: Entrada):
    if e.fecha.tzinfo is not None:
        raise HTTPException(status_code=422, detail="fecha debe ser hora local de Colombia, sin zona horaria")

    f = pd.Timestamp(e.fecha)
    fila = pd.DataFrame([{
        "Fecha": f,
        "Hora": f.hour,
        "Mes": f.month,
        "DiaDelAnio": f.dayofyear,
        "CodigoEstacion": 0,  # identificador fijo: solo agrupa la posición solar de una fila
        "Latitud": e.latitud,
        "Longitud": e.longitud,
        "Altitud": e.altitud,
        "ALLSKY_SFC_SW_DWN": e.ALLSKY_SFC_SW_DWN,
        "CLRSKY_SFC_SW_DWN": e.CLRSKY_SFC_SW_DWN,
        "ALLSKY_KT": e.ALLSKY_KT,
        "T2M": e.T2M,
    }])

    # R1 descarta las horas sin sol (zenit >= 90, CLRSKY o ALLSKY = 0). Si la fila
    # queda fuera, LightGBM no recibe datos vacíos: la respuesta es 0.
    if feat.build_features(fila, con_target=False).empty:
        return Salida(radiacion_corregida=0.0, sin_sol=True, modelo=VERSION)
    salida = corrector.predecir(fila)
    return Salida(radiacion_corregida=float(salida["radiacion_corregida"].iloc[0]),
                  sin_sol=False, modelo=VERSION)


# ---------------------------------------------------------------- física y finanzas (R3)
class Ubicacion(BaseModel):
    latitud: float = Field(ge=-5, le=14, description="Colombia: -5 a 14")
    longitud: float = Field(ge=-82, le=-66, description="Colombia: -82 a -66")
    altitud: float = Field(ge=-50, le=6000, description="metros sobre el nivel del mar")


class SerieSolar(BaseModel):
    """Una fila por hora. 'fechas' es hora local de Colombia, final de la hora, sin zona horaria
    (igual que la tabla de R3). 'ghi' es la radiación corregida en Wh/m² y 't_aire' la
    temperatura en °C. Las tres listas deben tener el mismo largo."""
    fechas: list[datetime] = Field(min_length=1, max_length=9000)
    ghi: list[float] = Field(min_length=1, max_length=9000, description="Wh/m²")
    t_aire: list[float] = Field(min_length=1, max_length=9000, description="°C")

    @model_validator(mode="after")
    def mismo_largo(self):
        if not (len(self.fechas) == len(self.ghi) == len(self.t_aire)):
            raise ValueError("fechas, ghi y t_aire deben tener el mismo número de horas")
        if any(f.tzinfo is not None for f in self.fechas):
            raise ValueError("fechas debe ser hora local de Colombia, sin zona horaria")
        return self


class Sistema(BaseModel):
    """Parámetros opcionales. Si no se envían, se usan los de la ficha config_r3.yaml."""
    kwp: float | None = Field(default=None, gt=0, description="Tamaño del sistema en kWp")
    kwh_mes: float | None = Field(default=None, gt=0, description="Consumo mensual del hogar, kWh")
    estrato: int | None = Field(default=None, ge=1, le=6)
    f_tarifa: float = Field(default=1.0, gt=0, description="Factor sobre la tarifa EPM")
    precio_excedente_cop: float | None = Field(default=None, ge=0, description="Precio del excedente vendido, COP/kWh")
    costo_kwp_cop: float | None = Field(default=None, gt=0)
    tasa_descuento: float | None = Field(default=None, gt=0, lt=1)


class ProduccionEntrada(BaseModel):
    ubicacion: Ubicacion
    serie: SerieSolar
    kwp: float | None = Field(default=None, gt=0)


class ConsumoEntrada(BaseModel):
    fechas: list[datetime] = Field(min_length=1, max_length=9000)
    kwh_mes: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def sin_zona_horaria(self):
        if any(f.tzinfo is not None for f in self.fechas):
            raise ValueError("fechas debe ser hora local de Colombia, sin zona horaria")
        return self


class AhorroEntrada(BaseModel):
    ubicacion: Ubicacion
    serie: SerieSolar
    sistema: Sistema = Field(default_factory=Sistema)


class FinanzasEntrada(BaseModel):
    """Entrada directa: los resultados de energía y ahorro de un año (salida de /v1/ahorro)."""
    kwp: float = Field(gt=0)
    kwh_produccion: float = Field(ge=0)
    ahorro_autoconsumo_cop: float
    valor_credito_energia_cop: float = 0.0
    valor_excedente_vendido_cop: float = 0.0
    costo_kwp_cop: float | None = Field(default=None, gt=0)
    tasa_descuento: float | None = Field(default=None, gt=0, lt=1)


class SimulacionEntrada(BaseModel):
    ubicacion: Ubicacion
    serie: SerieSolar
    sistema: Sistema = Field(default_factory=Sistema)


def _limpio(d: dict) -> dict:
    """Convierte numpy a tipos normales y cambia NaN/inf por None (JSON no admite NaN)."""
    salida = {}
    for k, v in d.items():
        if isinstance(v, (np.generic,)):
            v = v.item()
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        salida[k] = v
    return salida


def _serie(entrada: SerieSolar) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(entrada.fechas)


HORAS_MINIMAS_ANIO = 8000  # un año completo tiene 8.760 horas


def _con_avisos(res: dict, n_horas: int | None = None) -> dict:
    """Explica los null (payback y TIR sin valor) y avisa si la serie no cubre un año."""
    if "payback_simple_anios" in res:
        anios = int(r3.v(CFG["finanzas"]["horizonte_anios"]))
        if res["payback_simple_anios"] is None:
            res["payback_simple_mensaje"] = f"La inversión no se recupera en {anios} años."
        if res["payback_descontado_anios"] is None:
            res["payback_descontado_mensaje"] = f"La inversión no se recupera en {anios} años (con descuento)."
        if res["TIR"] is None:
            res["TIR_mensaje"] = "No existe una tasa que haga el VPN igual a 0: el flujo no recupera la inversión."
    if n_horas is not None and n_horas < HORAS_MINIMAS_ANIO:
        res["aviso_serie"] = (f"La serie tiene {n_horas} horas y un año completo son 8.760: los valores "
                              "anuales, el VPN y el payback no son representativos.")
    return res


@app.get("/v1/supuestos")
def supuestos():
    """Ficha de supuestos: cada parámetro con su valor, fuente y estado (OK, PUBLICADO, DECISION, PENDIENTE)."""
    return r3.ficha_supuestos(CFG).to_dict(orient="records")


@app.post("/v1/produccion")
def produccion(e: ProduccionEntrada):
    """kWh por hora que produce el panel, a partir de la radiación y la temperatura de cada hora."""
    u = e.ubicacion
    kwh = r3.produccion_panel(np.array(e.serie.ghi), np.array(e.serie.t_aire), _serie(e.serie),
                              u.latitud, u.longitud, u.altitud, CFG, e.kwp)
    return {"kwh_hora": [float(x) for x in kwh], "kwh_anual": float(np.sum(kwh))}


@app.post("/v1/consumo")
def consumo(e: ConsumoEntrada):
    """kWh por hora del hogar, repartidos con la forma diaria (laboral y fin de semana) de la ficha."""
    kwh = r3.consumo_hogar(pd.DatetimeIndex(e.fechas), CFG, e.kwh_mes)
    return {"kwh_hora": [float(x) for x in kwh], "kwh_anual": float(np.sum(kwh)),
            "aviso": "Las formas de consumo son PENDIENTE en la ficha (valores de ejemplo)."}


@app.post("/v1/ahorro")
def ahorro(e: AhorroEntrada):
    """Ahorro del primer año en la factura (autoconsumo, crédito de energía y excedente vendido)."""
    u, s = e.ubicacion, e.sistema
    kwp = s.kwp if s.kwp is not None else r3.v(CFG["sistema"]["kWp"])
    fechas = _serie(e.serie)
    prod = r3.produccion_panel(np.array(e.serie.ghi), np.array(e.serie.t_aire), fechas,
                               u.latitud, u.longitud, u.altitud, CFG, kwp)
    cons = r3.consumo_hogar(fechas, CFG, s.kwh_mes)
    h = r3.balance(prod, cons, fechas)
    estrato = s.estrato if s.estrato is not None else CFG["consumo"]["estrato"]
    res = r3.ahorro_anual(h, CFG, estrato, u.altitud, s.f_tarifa, s.precio_excedente_cop)
    return _con_avisos(_limpio(res), len(e.serie.fechas))


@app.post("/v1/finanzas")
def finanzas(e: FinanzasEntrada):
    """VPN, TIR, payback simple y descontado, inversión y CO₂ evitado, a partir del ahorro de un año."""
    a = {"ahorro_autoconsumo_cop": e.ahorro_autoconsumo_cop,
         "valor_credito_energia_cop": e.valor_credito_energia_cop,
         "valor_excedente_vendido_cop": e.valor_excedente_vendido_cop,
         "kWh_produccion": e.kwh_produccion}
    return _con_avisos(_limpio(r3.finanzas(a, CFG, e.kwp, e.costo_kwp_cop, e.tasa_descuento)))


@app.post("/v1/simulacion")
def simulacion(e: SimulacionEntrada):
    """Pipeline completo: producción, consumo, ahorro y finanzas a partir de una serie horaria."""
    u, s = e.ubicacion, e.sistema
    res = r3.flujo_desde_serie(
        _serie(e.serie), np.array(e.serie.ghi), np.array(e.serie.t_aire),
        u.latitud, u.longitud, u.altitud, CFG,
        estrato=s.estrato, kwp=s.kwp, costo_kwp=s.costo_kwp_cop, kwh_mes=s.kwh_mes,
        f_tarifa=s.f_tarifa, precio_exc2=s.precio_excedente_cop, tasa=s.tasa_descuento)
    return _con_avisos(_limpio(res), len(e.serie.fechas))
