"""API de Calcusol.

- /predict: corrige la radiación de NASA con el modelo de R1 (LightGBM).
- /v1/...: física y finanzas de R3 (producción, consumo, ahorro, VPN y payback).

Toda la lógica de cálculo vive en calcusol.ml y calcusol.fisica_finanzas (src/).
Aquí solo se valida la entrada y se devuelve la salida.
"""
import math
import os
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from calcusol.fisica_finanzas import calcusol_r3 as r3
from calcusol.ml import features as feat
from calcusol.ml.activo import cargar_activo

# Modelo embebido en la imagen (respaldo) y registro de versiones (S3). El modelo en servicio es el que dice
# el manifiesto del registro; sin registro, sin manifiesto o ante cualquier fallo se usa el embebido.
RUTA_MODELO = os.environ.get("MODELO_RUTA", "/app/models/radiacion/v1/modelo_lgbm.txt")
corrector, MODELO = cargar_activo(RUTA_MODELO, os.environ.get("CALCUSOL_MODELOS_ROOT"))
VERSION = f"radiacion-{MODELO['version']} (LightGBM, variante A0, 17 features)"

CFG = r3.cargar_config()  # ficha de supuestos empaquetada con calcusol

app = FastAPI(title="Calcusol · API", version="1.3.0")

# El dashboard se sirve desde otro puerto (por ejemplo http://localhost:8080): sin CORS el navegador
# bloquea sus llamadas. Por defecto se permite cualquier puerto de localhost; para otros
# orígenes, CORS_ORIGENES="https://mi-sitio.com,https://otro.com".
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGENES", "").split(",") if o],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


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
    return {"estado": "ok", "modelo": VERSION, "modelo_version": MODELO["version"], "modelo_origen": MODELO["origen"],
            "modelo_sha256": MODELO["sha256"], "gold_version": MODELO["gold_version"],
            "promovido": MODELO["promoted_at"]}


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
    tarifa_cop_kwh: float | None = Field(default=None, gt=0, description="Tarifa plena del operador, COP/kWh. "
                                         "Reescala la tarifa EPM del estrato (el subsidio se conserva)")
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


class PuntoEntrada(BaseModel):
    """Parámetros equivalentes al bloque 'Lugar nuevo' del notebook R3."""
    latitud: float = Field(default=2.19, ge=-5, le=14)
    longitud: float = Field(default=-75.63, ge=-82, le=-66)
    anios: list[int] = Field(default=[2023, 2024, 2025], min_length=1, max_length=20)
    estrato: int = Field(default=3, ge=1, le=6)
    kwh_mes: float = Field(default=157, gt=0)
    kwp: float = Field(default=1.5, gt=0)
    costo_kwp_cop: float = Field(default=5000000, gt=0)
    inclinacion_grados: float = Field(default=10, ge=0, le=90)
    azimut_grados: float = Field(default=180, ge=0, le=360)
    factor_tarifa: float = Field(default=1.0, gt=0)
    precio_excedente_cop: float = Field(default=331, ge=0)
    tasa_descuento: float = Field(default=0.09, gt=0, lt=1)


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


def _factor_tarifa(s: Sistema) -> float:
    """f_tarifa, combinado con tarifa_cop_kwh si se envió (se compara con la tarifa plena del estrato)."""
    if s.tarifa_cop_kwh is None:
        return s.f_tarifa
    estrato = s.estrato if s.estrato is not None else CFG["consumo"]["estrato"]
    plena = r3.v(CFG["finanzas"]["tarifas_epm_cop_kwh"])[estrato]["plena"]
    return s.f_tarifa * s.tarifa_cop_kwh / plena


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


@app.get("/v1/parametros")
def parametros():
    """Valores por defecto de la ficha de supuestos, ya ordenados para un cliente (el dashboard los usa para
    sus controles): hogar, sistema, finanzas, tarifas EPM por estrato, consumo de subsistencia y, de cada
    parámetro, su fuente y su estado (OK, PUBLICADO, DECISION, PENDIENTE)."""
    v = r3.v
    s, c, f = CFG["sistema"], CFG["consumo"], CFG["finanzas"]
    fuentes = {f"{grupo}.{k}": {"fuente": it.get("fuente", ""), "estado": it.get("estado", "")}
               for grupo in ("sistema", "consumo", "finanzas") for k, it in CFG[grupo].items()
               if isinstance(it, dict) and "valor" in it}
    return {
        "hogar": {"estrato": c["estrato"], "kwh_mes": v(c["kwh_mes_hogar"]), "kwp": v(s["kWp"])},
        "sistema": {"perdidas": v(s["perdidas_sistema"]), "eficiencia_inversor": v(s["eficiencia_inversor"]),
                    "degradacion_primer_anio": v(s["degradacion_primer_anio"]), "degradacion_anual": v(s["degradacion_anual"]),
                    "noct_c": v(s["noct_c"]), "coef_temp_potencia_por_c": v(s["coef_temp_potencia_por_c"]),
                    "inclinacion_grados": v(s["inclinacion_grados"]), "azimut_grados": v(s["azimut_grados"])},
        "finanzas": {"costo_por_kwp_cop": v(f["costo_por_kWp_cop"]), "mantenimiento_anual_pct_capex": v(f["mantenimiento_anual_pct_capex"]),
                     "anio_reemplazo_inversor": v(f["anio_reemplazo_inversor"]), "costo_reemplazo_inversor_cop": v(f["costo_reemplazo_inversor_cop"]),
                     "cargo_comercializacion_cop_kwh": v(f["cargo_comercializacion_cop_kwh"]),
                     "precio_excedente_cop_kwh": v(f["precio_excedente_tramo2_cop"]), "tasa_descuento": v(f["tasa_descuento"]),
                     "incremento_real_tarifa_anual": v(f["incremento_real_tarifa_anual"]), "horizonte_anios": v(f["horizonte_anios"]),
                     "factor_emision_kgco2_kwh": v(f["factor_emision_kgco2_kwh"])},
        "tarifas_cop_kwh": {str(k): t for k, t in v(f["tarifas_epm_cop_kwh"]).items()},
        "consumo_subsistencia_kwh_mes": v(c["consumo_subsistencia_kwh_mes"]),
        "sensibilidad": CFG.get("sensibilidad", {}),
        "fuentes": fuentes,
    }


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
    res = r3.ahorro_anual(h, CFG, estrato, u.altitud, _factor_tarifa(s), s.precio_excedente_cop)
    return _con_avisos(_limpio(res), len(e.serie.fechas))


@app.post("/v1/finanzas")
def finanzas(e: FinanzasEntrada):
    """VPN, TIR, payback simple y descontado, inversión y CO₂ evitado, a partir del ahorro de un año."""
    a = {"ahorro_autoconsumo_cop": e.ahorro_autoconsumo_cop,
         "valor_credito_energia_cop": e.valor_credito_energia_cop,
         "valor_excedente_vendido_cop": e.valor_excedente_vendido_cop,
         "kWh_produccion": e.kwh_produccion}
    return _con_avisos(_limpio(r3.finanzas(a, CFG, e.kwp, e.costo_kwp_cop, e.tasa_descuento)))


class Climatologia(BaseModel):
    """Año típico resumido: radiación y temperatura promedio de cada mes (fila) y de cada hora
    con sol (columna). 'horas' son las horas de cierre 0-23 de las columnas, como en la tabla gold.
    Las demás horas del día se toman como noche."""
    horas: list[int] = Field(min_length=1, max_length=24)
    ghi: list[list[float]] = Field(description="Wh/m², 12 meses x len(horas)")
    t_aire: list[list[float]] = Field(description="°C, 12 meses x len(horas)")

    @model_validator(mode="after")
    def forma(self):
        if len(set(self.horas)) != len(self.horas) or any(not 0 <= h <= 23 for h in self.horas):
            raise ValueError("horas debe tener valores distintos entre 0 y 23")
        for nombre in ("ghi", "t_aire"):
            m = getattr(self, nombre)
            if len(m) != 12 or any(len(f) != len(self.horas) for f in m):
                raise ValueError(f"{nombre} debe ser una matriz de 12 meses x {len(self.horas)} horas")
        if any(v < 0 for f in self.ghi for v in f):
            raise ValueError("ghi no puede ser negativa")
        return self


class Ajustes(BaseModel):
    """Parámetros de la ficha que se pueden cambiar en esta llamada. Si no se envían, se usa la ficha."""
    perdidas: float | None = Field(default=None, ge=0, le=0.6, description="Pérdidas del sistema, fracción")
    eficiencia_inversor: float | None = Field(default=None, gt=0.5, le=1, description="Eficiencia del inversor, fracción")
    degradacion_primer_anio: float | None = Field(default=None, ge=0, le=0.1)
    anio_reemplazo_inversor: int | None = Field(default=None, ge=1, le=40)
    costo_reemplazo_inversor_cop: float | None = Field(default=None, ge=0)
    tarifa_subsistencia_cop: float | None = Field(default=None, gt=0, description="COP/kWh hasta el consumo de subsistencia, del estrato de la llamada")
    tarifa_plena_cop: float | None = Field(default=None, gt=0, description="COP/kWh sobre el consumo de subsistencia, del estrato de la llamada")
    degradacion_anual: float | None = Field(default=None, ge=0, le=0.05)
    mantenimiento_pct: float | None = Field(default=None, ge=0, le=0.1, description="Fracción del costo por año")
    alza_tarifa: float | None = Field(default=None, ge=-0.1, le=0.3, description="Cambio real anual de la tarifa")
    horizonte_anios: int | None = Field(default=None, ge=5, le=40)
    comercializacion_cop: float | None = Field(default=None, ge=0, description="COP por kWh de excedente permutado")
    perfil_consumo: list[float] | None = Field(default=None, min_length=24, max_length=24,
                                               description="Reparto del consumo diario en 24 horas (se normaliza)")

    @model_validator(mode="after")
    def perfil_valido(self):
        if self.perfil_consumo is not None and (min(self.perfil_consumo) < 0 or sum(self.perfil_consumo) <= 0):
            raise ValueError("perfil_consumo debe tener valores no negativos y sumar más que cero")
        return self


class SimulacionClimaEntrada(BaseModel):
    ubicacion: Ubicacion
    climatologia: Climatologia
    sistema: Sistema = Field(default_factory=Sistema)
    ajustes: Ajustes = Field(default_factory=Ajustes)


@app.post("/v1/simulacion/climatologia")
def simulacion_climatologia(e: SimulacionClimaEntrada):
    """Simulación completa desde una climatología mes x hora (la que usa el dashboard). Arma el año de
    8.760 horas y devuelve, además de los totales, el balance de cada mes, el flujo de caja acumulado
    y la producción del día promedio de cada mes."""
    u, s, c = e.ubicacion, e.sistema, e.climatologia
    anio = r3.anio_desde_climatologia(c.ghi, c.t_aire, c.horas)
    a = e.ajustes
    ajustes = a.model_dump(exclude={"perfil_consumo", "tarifa_subsistencia_cop", "tarifa_plena_cop"})
    estrato = s.estrato if s.estrato is not None else CFG["consumo"]["estrato"]
    tarifas = {"estrato": estrato, "subsistencia": a.tarifa_subsistencia_cop, "plena": a.tarifa_plena_cop}
    cfg = r3.cfg_con_ajustes(CFG, ajustes, a.perfil_consumo, tarifas)
    res = r3.simulacion_detallada(
        pd.DatetimeIndex(anio["Fecha"]), anio["ghi"].to_numpy(), anio["T2M"].to_numpy(),
        u.latitud, u.longitud, u.altitud, cfg,
        estrato=s.estrato, kwp=s.kwp, costo_kwp=s.costo_kwp_cop, kwh_mes=s.kwh_mes,
        f_tarifa=_factor_tarifa(s), precio_exc2=s.precio_excedente_cop, tasa=s.tasa_descuento)
    return _con_avisos(_limpio(res))


@app.post("/v1/simulacion")
def simulacion(e: SimulacionEntrada):
    """Pipeline completo: producción, consumo, ahorro y finanzas a partir de una serie horaria."""
    u, s = e.ubicacion, e.sistema
    res = r3.flujo_desde_serie(
        _serie(e.serie), np.array(e.serie.ghi), np.array(e.serie.t_aire),
        u.latitud, u.longitud, u.altitud, CFG,
        estrato=s.estrato, kwp=s.kwp, costo_kwp=s.costo_kwp_cop, kwh_mes=s.kwh_mes,
        f_tarifa=_factor_tarifa(s), precio_exc2=s.precio_excedente_cop, tasa=s.tasa_descuento)
    return _con_avisos(_limpio(res), len(e.serie.fechas))


@app.post("/v1/punto")
def calcular_punto_nasa(e: PuntoEntrada):
    """Descarga NASA, corrige con LightGBM y arma el año típico de 8.760 horas.

    Esta ruta reproduce el bloque 'Lugar nuevo' del notebook R3.
    """
    try:
        res = r3.calcular_punto(
            e.latitud,
            e.longitud,
            CFG,
            estrato=e.estrato,
            kwh_mes=e.kwh_mes,
            kwp=e.kwp,
            costo_kwp=e.costo_kwp_cop,
            anios=e.anios,
            f_tarifa=e.factor_tarifa,
            precio_exc2=e.precio_excedente_cop,
            tasa=e.tasa_descuento,
            inclinacion=e.inclinacion_grados,
            azimut=e.azimut_grados,
            corrector=corrector,                 # el mismo modelo que sirve /predict (el activo del registro)
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo calcular el punto: {exc}") from exc

    return [_limpio(row) for row in res.to_dict(orient="records")]


class CasosEntrada(BaseModel):
    """Mismos parámetros del hogar y del sistema que /v1/punto, pero sin lugar ni años."""
    estrato: int = Field(default=3, ge=1, le=6)
    kwh_mes: float = Field(default=157, gt=0)
    kwp: float = Field(default=1.5, gt=0)
    costo_kwp_cop: float = Field(default=5000000, gt=0)
    inclinacion_grados: float = Field(default=10, ge=0, le=90)
    azimut_grados: float = Field(default=180, ge=0, le=360)
    factor_tarifa: float = Field(default=1.0, gt=0)
    precio_excedente_cop: float = Field(default=331, ge=0)
    tasa_descuento: float = Field(default=0.09, gt=0, lt=1)


@lru_cache(maxsize=1)
def _casos_r2():
    """Serie horaria y casos del Excel de evaluación de R2 (se leen una sola vez)."""
    df = r3.cargar_serie_r2(CFG)
    return df, r3.cargar_casos_r2(CFG, df)


@app.post("/v1/casos")
def calcular_casos(e: CasosEntrada):
    """Los 3 lugares del notebook R3 (mayor mejora, mediana y menor mejora), cada uno con el sol del IDEAM,
    de NASA y del modelo (fuera de pliegue). Es el bloque 'Tus valores para los 3 lugares' del notebook:
    mismos datos, así que los números coinciden con r3_mis_valores_3_lugares.csv."""
    try:
        df, casos = _casos_r2()
        res = r3.correr_casos(df, casos, CFG, inclinacion=e.inclinacion_grados, azimut=e.azimut_grados,
                              estrato=e.estrato, kwh_mes=e.kwh_mes, kwp=e.kwp, costo_kwp=e.costo_kwp_cop,
                              f_tarifa=e.factor_tarifa, precio_exc2=e.precio_excedente_cop, tasa=e.tasa_descuento)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudieron calcular los casos: {exc}") from exc
    res["estrato"], res["kWp"] = e.estrato, e.kwp
    return [_limpio(row) for row in res.to_dict(orient="records")]
