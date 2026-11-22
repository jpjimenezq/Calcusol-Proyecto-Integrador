"""
Calcusol · R3 · Física y finanzas
=================================
Convierte la radiación (IDEAM, NASA o modelo) en:
  energía del panel -> autoconsumo / excedente -> ahorro, VPN y Payback.

Flujo (cada función es un paso):
  1. cargar_tabla / predecir_modelo   -> radiación de las 3 fuentes por hora
  2. tabla_mejora_por_estacion / elegir_casos -> los 4 lugares (D16)
  3. anio_tipico                      -> 8,760 horas con noche = 0 (D12)
  4. produccion_panel                 -> kWh por hora del panel (D13, pvlib)
  5. consumo_hogar                    -> kWh por hora de la casa (D15)
  6. balance / finanzas               -> ahorro, VPN, Payback (D14)
  7. correr_casos / sensibilidad      -> tablas en parquet (R3-5)

Todos los números salen de config_r3.yaml; aquí no hay valores escritos a mano.
"""
from __future__ import annotations

import copy
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pvlib
import yaml

FUENTES = {"IDEAM": "Valor", "NASA": "ALLSKY_SFC_SW_DWN", "MODELO": "Prediccion"}
ANIO_REF = 2021  # año no bisiesto: 8,760 horas


# ---------------------------------------------------------------- utilidades
def cargar_config(ruta="config_r3.yaml") -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def v(cfg_item):
    """Devuelve el 'valor' de un parámetro de la ficha."""
    return cfg_item["valor"] if isinstance(cfg_item, dict) and "valor" in cfg_item else cfg_item


# ---------------------------------------------------- 1. datos y modelo
def cargar_tabla(cfg, estaciones=None) -> pd.DataFrame:
    """Lee la tabla gold. Si en config hay anios_prueba, se queda con esos años;
    si se pasan estaciones, solo con esas (más rápido)."""
    df = pd.read_parquet(cfg["datos"]["tabla_gold"])
    if estaciones is not None:
        df = df[df.CodigoEstacion.isin(list(estaciones))]
    anios = cfg["datos"].get("anios_prueba")
    if anios:
        df = df[df["Fecha"].dt.year.isin(anios)]
    return df.copy()


def variables_modelo(df: pd.DataFrame, columnas: list, desfase_min: float = 30) -> pd.DataFrame:
    """Arma las variables con las que R1 entrenó el modelo, con los MISMOS nombres.
    Sirve para el modelo final (LightGBM, 17 variables) y para el de prueba (13).
    Variables del sol (zenit, azimut, g0_horiz) se calculan con pvlib a la hora
    Fecha + desfase_min, igual que R1 (verificado contra evaluacion_lightgbm.xlsx:
    MAE 103.18 frente a 103.10 reportado, MBE 3.72 igual)."""
    X = df[["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M",
            "Latitud", "Longitud", "Altitud"]].astype(float).reset_index(drop=True).copy()
    hora = df["Hora"].to_numpy(); mes = df["Mes"].to_numpy(); dia = df["DiaDelAnio"].to_numpy()
    # variables de tiempo (dos formas de escribir los nombres)
    X["hora_sin"] = X["Hora_sin"] = np.sin(2 * np.pi * hora / 24)
    X["hora_cos"] = X["Hora_cos"] = np.cos(2 * np.pi * hora / 24)
    X["dia_sin"] = X["Dia_sin"] = np.sin(2 * np.pi * dia / 365)
    X["dia_cos"] = X["Dia_cos"] = np.cos(2 * np.pi * dia / 365)
    X["Mes_sin"] = np.sin(2 * np.pi * mes / 12)
    X["Mes_cos"] = np.cos(2 * np.pi * mes / 12)
    X["Mes"] = mes
    if any(c in columnas for c in ("zenit", "azimut", "g0_horiz", "kt_calc")):
        t = pd.DatetimeIndex(df["Fecha"]).tz_localize("America/Bogota") + pd.Timedelta(minutes=desfase_min)
        zen = np.empty(len(df)); az = np.empty(len(df))
        grupos = pd.DataFrame({"a": X.Latitud, "b": X.Longitud, "c": X.Altitud}).groupby(["a", "b", "c"]).indices
        for (la, lo, al), idx in grupos.items():
            sp = pvlib.solarposition.get_solarposition(t[idx], la, lo, al)
            zen[idx] = sp["zenith"].to_numpy(); az[idx] = sp["azimuth"].to_numpy()
        X["zenit"], X["azimut"] = zen, az
        X["g0_horiz"] = pvlib.irradiance.get_extra_radiation(t).to_numpy() * np.clip(np.cos(np.radians(zen)), 0, None)
        X["kt_calc"] = np.where(X.g0_horiz > 0, X.ALLSKY_SFC_SW_DWN / X.g0_horiz.where(X.g0_horiz > 0), 0)
    clr = X.CLRSKY_SFC_SW_DWN
    X["razon_cielo"] = np.where(clr > 0, X.ALLSKY_SFC_SW_DWN / clr.where(clr > 0), 0)
    return X[list(columnas)]


def cargar_modelo(ruta: str):
    """Abre el modelo de R1. Acepta .joblib (LightGBM o XGBoost) o .json (XGBoost)."""
    if str(ruta).lower().endswith(".json"):
        import xgboost as xgb
        m = xgb.XGBRegressor()
        m.load_model(ruta)
        return m
    return joblib.load(ruta)


def columnas_modelo(modelo) -> list:
    for atributo in ("feature_name_", "feature_names_in_"):
        if hasattr(modelo, atributo):
            return list(getattr(modelo, atributo))
    return list(modelo.get_booster().feature_names)


def predecir_modelo(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Agrega la columna 'Prediccion' (radiación corregida, recortada a 0..1400, D3).
    El LightGBM final predice la DIFERENCIA con la NASA (IDEAM - NASA), así que
    Prediccion = NASA + lo que dice el modelo (config: modelo_predice).
    Si las predicciones ya vienen hechas, basta con que traigan esa columna."""
    if "Prediccion" in df.columns:
        return df
    d = cfg["datos"]
    modelo = cargar_modelo(d["modelo"])
    X = variables_modelo(df, columnas_modelo(modelo), d.get("desfase_variables_sol_min", 30))
    pred = modelo.predict(X)
    if d.get("modelo_predice", "radiacion") == "diferencia_con_nasa":
        pred = df["ALLSKY_SFC_SW_DWN"].to_numpy() + pred
    df = df.copy()
    df["Prediccion"] = np.clip(pred, 0, 1400)
    return df


# ---------------------------------------------------- 2. elegir los casos
def tabla_mejora_por_estacion(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """PROVISIONAL mientras llega la tabla oficial de R2 (R2-3).
    Mejora % = cuánto baja el error (MAE) del modelo frente a NASA."""
    g = df.assign(eN=(df.ALLSKY_SFC_SW_DWN - df.Valor).abs(),
                  eM=(df.Prediccion - df.Valor).abs())
    t = g.groupby(["CodigoEstacion", "NombreEstacion", "Departamento"]).agg(
        MAE_NASA=("eN", "mean"), MAE_MODELO=("eM", "mean"), horas=("eN", "size")
    ).reset_index()
    t["Mejora_pct"] = 100 * (1 - t.MAE_MODELO / t.MAE_NASA)
    # cobertura: horas por mes (para poder armar un año completo)
    cob = df.groupby(["CodigoEstacion", df.Fecha.dt.month]).size().unstack(fill_value=0)
    t["min_horas_mes"] = t.CodigoEstacion.map(cob.min(axis=1))
    t["apta"] = t.min_horas_mes >= cfg["casos"]["min_horas_por_mes"]
    return t.sort_values("Mejora_pct", ascending=False).reset_index(drop=True)


def cargar_tabla_mejora_r2(cfg, mejora_prov: pd.DataFrame) -> pd.DataFrame:
    """Si en config_r3.yaml hay una ruta en datos.tabla_mejora_r2, usa la tabla
    oficial de Manuela (R2). Si está vacía, devuelve la provisional.
    La tabla de R2 necesita dos columnas: el código de la estación y la mejora en %.
    Si sus columnas se llaman distinto, se indica en datos.columnas_r2."""
    ruta = cfg["datos"].get("tabla_mejora_r2")
    if not ruta:
        print("Usando la tabla de mejora PROVISIONAL (calculada aquí).")
        return mejora_prov
    ext = Path(ruta).suffix.lower()
    if ext == ".parquet":
        r2 = pd.read_parquet(ruta)
    elif ext in (".xlsx", ".xls"):
        r2 = pd.read_excel(ruta)
    else:
        r2 = pd.read_csv(ruta)
    col = cfg["datos"].get("columnas_r2", {}) or {}
    c_est = col.get("estacion", "CodigoEstacion")
    c_mej = col.get("mejora_pct", "Mejora_pct")
    faltan = [c for c in (c_est, c_mej) if c not in r2.columns]
    if faltan:
        raise ValueError(f"La tabla de R2 no tiene las columnas {faltan}. "
                         f"Sus columnas son: {list(r2.columns)}. "
                         "Escribe los nombres correctos en datos.columnas_r2 de config_r3.yaml")
    r2 = r2[[c_est, c_mej]].rename(columns={c_est: "CodigoEstacion", c_mej: "Mejora_pct"})
    # se conservan nombre, departamento y cobertura (para saber si se puede armar un año completo)
    t = mejora_prov.drop(columns=["Mejora_pct"]).merge(r2, on="CodigoEstacion", how="inner")
    print(f"Usando la tabla OFICIAL de R2: {ruta} ({len(t)} estaciones).")
    return t.sort_values("Mejora_pct", ascending=False).reset_index(drop=True)


def elegir_casos(mejora: pd.DataFrame, cfg) -> pd.DataFrame:
    """Regla D16 fijada de antemano: entre estaciones aptas, la de MAYOR mejora,
    la MEDIANA y la de MENOR mejora; más Villeta como caso narrativo."""
    aptas = mejora[mejora.apta].sort_values("Mejora_pct", ascending=False).reset_index(drop=True)
    idx = {"Mayor mejora": 0, "Mejora mediana": len(aptas) // 2, "Menor mejora": len(aptas) - 1}
    filas = [aptas.iloc[i].to_dict() | {"Caso": c} for c, i in idx.items()]
    vil = mejora[mejora.NombreEstacion.str.contains(cfg["casos"]["caso_narrativo"], case=False)]
    if len(vil):
        filas.append(vil.iloc[0].to_dict() | {"Caso": "Narrativo (Villeta)"})
    return pd.DataFrame(filas)


# ---------------------------------------------------- 2b. entrega de Manuela (R2)
COLUMNA_MODELO_R2 = {"XGBoost (completo)": "pred_XGBoost",
                     "XGBoost sin ubicación": "pred_XGB_sin_ubicacion"}


def usa_entrega_r2(cfg) -> bool:
    return bool(cfg["datos"].get("predicciones_r2"))


def cargar_predicciones_r2(cfg) -> pd.DataFrame:
    """Predicciones FUERA DE MUESTRA de Manuela (examen espacial): cada sitio se
    predijo con un modelo que nunca lo vio. Es lo que el plan pide para el Payback
    'con modelo' (evita que el modelo salga demasiado optimista).
    Su archivo trae IDEAM, NASA y las predicciones; la temperatura, las coordenadas
    y la altura se toman de la tabla gold (mismas estación y hora)."""
    d = cfg["datos"]
    col = COLUMNA_MODELO_R2[d.get("modelo_r2", "XGBoost (completo)")]
    pr = pd.read_parquet(d["predicciones_r2"])
    gold = pd.read_parquet(d["tabla_gold"], columns=["CodigoEstacion", "NombreEstacion", "Fecha",
                                                     "T2M", "Latitud", "Longitud", "Altitud"])
    gold = gold[gold.CodigoEstacion.isin(pr.CodigoEstacion.unique())]
    m = pr.merge(gold, on=["CodigoEstacion", "Fecha"], how="inner")
    if len(m) < len(pr):
        print(f"Aviso: {len(pr) - len(m)} horas de R2 no están en la tabla gold y se omiten.")
    out = pd.DataFrame({
        "CodigoEstacion": m.CodigoEstacion, "NombreEstacion": m.NombreEstacion,
        "Departamento": m.Departamento, "Fecha": m.Fecha, "Hora": m.Fecha.dt.hour,
        "Mes": m.Fecha.dt.month, "DiaDelAnio": m.Fecha.dt.dayofyear,
        "Valor": m.Valor_IDEAM, "ALLSKY_SFC_SW_DWN": m.NASA_sin_corregir,
        "Prediccion": np.clip(m[col], 0, 1400), "T2M": m.T2M,
        "Latitud": m.Latitud, "Longitud": m.Longitud, "Altitud": m.Altitud})
    print(f"Usando las predicciones de R2 ({d.get('modelo_r2', 'XGBoost (completo)')}): "
          f"{len(out):,} horas, {out.CodigoEstacion.nunique()} estaciones, "
          f"{out.Fecha.dt.year.min()}-{out.Fecha.dt.year.max()}.")
    return out


def usa_excel_casos(cfg) -> bool:
    return bool(cfg["datos"].get("casos_r2")) and not usa_entrega_r2(cfg)


def usa_serie_r2(cfg) -> bool:
    """True si el Excel de R2 trae la hoja serie_horaria (versión final): ahí vienen,
    hora por hora, la medición del IDEAM, NASA y el modelo FUERA DE PLIEGUE para cada caso."""
    d = cfg["datos"]
    return usa_excel_casos(cfg) and "serie_horaria" in pd.ExcelFile(d["casos_r2"]).sheet_names


def cargar_serie_r2(cfg) -> pd.DataFrame:
    """Lee la hoja serie_horaria de R2 y la deja con las columnas del motor.
    La predicción del modelo viene de un modelo que NO vio ese sitio (validación cruzada),
    por eso no se usa el .joblib aquí: el .joblib final sí vio estos sitios al entrenar."""
    d = cfg["datos"]
    s = pd.read_excel(d["casos_r2"], sheet_name="serie_horaria")
    m = cargar_mejora_r2(cfg).set_index("Sitio")
    col = d.get("columna_modelo_r2", "Modelo_principal")
    f = pd.to_datetime(s["Fecha_etiqueta_hora_local"])   # hora de cierre, igual que la tabla gold
    df = pd.DataFrame({
        "CodigoEstacion": s["CodigoEstacion"].astype(int), "NombreEstacion": s["Estacion"],
        "Fecha": f, "Hora": f.dt.hour, "Mes": f.dt.month, "DiaDelAnio": f.dt.dayofyear,
        "Valor": s["IDEAM_medicion"], "ALLSKY_SFC_SW_DWN": s["NASA_sin_corregir"],
        "Prediccion": s[col].clip(0, 1400), "T2M": s["T2M_NASA_temperatura_aire"],
        "CLRSKY_SFC_SW_DWN": s.get("CLRSKY_NASA_cielo_despejado"), "ALLSKY_KT": s.get("ALLSKY_KT_NASA"),
    })
    for c in ("Latitud", "Longitud", "Altitud", "Departamento"):
        df[c] = s["Sitio"].map(m[c]).values
    return df


def cargar_mejora_r2(cfg) -> pd.DataFrame:
    """La tabla completa de mejora por sitio (hoja mejora_por_sitio del Excel de
    evaluación: una fila por sitio que el modelo NO vio al entrenar)."""
    ruta = cfg["datos"]["casos_r2"]
    crudo = pd.read_excel(ruta, sheet_name="mejora_por_sitio", header=None)
    fila = crudo.index[crudo.apply(lambda r: r.astype(str).str.fullmatch("Sitio").any(), axis=1)][0]
    return pd.read_excel(ruta, sheet_name="mejora_por_sitio", header=fila)


def cargar_casos_r2(cfg, df_gold: pd.DataFrame | None = None) -> pd.DataFrame:
    """Los 4 casos de la regla D16 que vienen marcados en la columna caso_sugerido
    (mayor, mediana y menor mejora, y el más cercano a Villeta).
    Formato viejo (hoja casos_sugeridos) también se acepta."""
    d = cfg["datos"]
    hojas = pd.ExcelFile(d["casos_r2"]).sheet_names
    if "casos_sugeridos" in hojas:
        t = pd.read_excel(d["casos_r2"], sheet_name="casos_sugeridos", header=4)
        t = t[t.Modelo == d.get("modelo_r2", "XGBoost (completo)")].dropna(subset=["Sitio"])
        return pd.DataFrame({"Caso": t.Caso.values, "CodigoEstacion": t.Sitio.astype(int).values,
                             "NombreEstacion": t.Estaciones.values, "Departamento": t.Departamento.values,
                             "Mejora_pct": (t["Mejora vs B-0"] * 100).values}).reset_index(drop=True)
    m = cargar_mejora_r2(cfg)
    col = d.get("columna_mejora", "mejora_LGBM_vs_NASA")
    t = m.dropna(subset=["caso_sugerido"])
    orden = {"Mayor mejora": 0, "Mejora mediana": 1, "Mediana": 1, "Menor mejora": 2}
    t = t.assign(_o=t.caso_sugerido.map(orden).fillna(3)).sort_values("_o")
    casos = pd.DataFrame({"Caso": t.caso_sugerido.values, "CodigoEstacion": t.Sitio.astype(int).values,
                          "Departamento": t.Departamento.values, "Mejora_pct": (t[col] * 100).values})
    if df_gold is not None:
        nombres = df_gold.drop_duplicates("CodigoEstacion").set_index("CodigoEstacion").NombreEstacion
        casos["NombreEstacion"] = casos.CodigoEstacion.map(nombres)
    return casos.reset_index(drop=True)


# ---------------------------------------------------- 3. año típico de 8,760 h
def anio_tipico(df_est: pd.DataFrame) -> pd.DataFrame:
    """Arma un año de 8,760 horas para UNA estación.
    - Las 3 fuentes usan EXACTAMENTE las mismas horas (las que tiene el IDEAM),
      así la comparación es justa aunque el IDEAM tenga huecos.
    - Cada hora del año toma el promedio de su (mes, hora) en TODOS los años
      disponibles (gold v1: 2005-2022 según la estación). No rellena huecos: arma un
      año nuevo completo; todos los días de un mes quedan iguales a esa hora.
    - Las horas de noche no existen en la tabla -> producción 0 (D12)."""
    cols = [c for c in FUENTES.values() if c in df_est.columns]
    # MISMAS HORAS PARA LAS TRES FUENTES: solo se usan las filas (horas) que tienen
    # dato en TODAS las columnas. Si a una hora le falta el IDEAM (o NASA, o el
    # modelo, o la temperatura), esa hora se quita para todas las fuentes.
    df_est = df_est.dropna(subset=cols + ["T2M"])
    prom = df_est.groupby(["Mes", "Hora"])[cols + ["T2M"]].mean()
    t_media = df_est.groupby("Mes")["T2M"].mean()
    idx = pd.date_range(f"{ANIO_REF}-01-01 01:00", periods=8760, freq="h")  # hora de cierre
    base = pd.DataFrame({"Fecha": idx, "Mes": idx.month, "Hora": idx.hour})
    anio = base.merge(prom.reset_index(), on=["Mes", "Hora"], how="left")
    for col in cols:
        anio[col] = anio[col].fillna(0.0)
    anio["T2M"] = anio["T2M"].fillna(anio.Mes.map(t_media))
    return anio


def verificar_mismas_horas(df_est: pd.DataFrame) -> pd.DataFrame:
    """Comprobación: cuenta, para cada mes y hora, cuántas horas entran al promedio
    de cada fuente. Si las tres fuentes usan las mismas horas, los conteos son iguales.
    Devuelve la tabla de conteos y avisa si alguna fila no coincide."""
    cols = [c for c in FUENTES.values() if c in df_est.columns]
    d = df_est.dropna(subset=cols + ["T2M"])          # igual que en anio_tipico
    conteo = d.groupby(["Mes", "Hora"])[cols].count()
    conteo.columns = [k for k, c in FUENTES.items() if c in cols]
    iguales = (conteo.nunique(axis=1) == 1)
    print(f"Combinaciones mes-hora: {len(conteo)} | con el mismo número de horas en todas las "
          f"fuentes: {int(iguales.sum())} | horas quitadas por datos incompletos: {len(df_est) - len(d)}")
    if not iguales.all():
        print("ATENCIÓN: hay combinaciones con distinto número de horas por fuente.")
    return conteo


# ---------------------------------------------------- 4. motor solar (pvlib)
def produccion_panel(ghi_wh_m2: pd.Series, t_aire: pd.Series, fechas: pd.Series,
                     lat: float, lon: float, alt: float, cfg, kwp: float | None = None) -> np.ndarray:
    """kWh AC por hora. Radiación horaria en Wh/m² = irradiancia media de la hora en W/m²."""
    s = cfg["sistema"]
    kwp = v(s["kWp"]) if kwp is None else kwp
    # sol en el punto medio de la hora: el IDEAM marca el final de la hora (D0)
    t = (pd.DatetimeIndex(fechas) + pd.Timedelta(minutes=v(s["ajuste_minutos_hora"]))
         ).tz_localize(v(s["zona_horaria"]))
    ghi = pd.Series(np.asarray(ghi_wh_m2, float), index=t)
    sol = pvlib.solarposition.get_solarposition(t, lat, lon, altitude=alt)
    # separar directa y difusa: Erbs (D13)
    erbs = pvlib.irradiance.erbs(ghi, sol["zenith"], t)
    dni_extra = pvlib.irradiance.get_extra_radiation(t)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=v(s["inclinacion_grados"]), surface_azimuth=v(s["azimut_grados"]),
        solar_zenith=sol["apparent_zenith"], solar_azimuth=sol["azimuth"],
        dni=erbs["dni"], ghi=ghi, dhi=erbs["dhi"], dni_extra=dni_extra,
        albedo=v(s["albedo"]), model="haydavies",
    )["poa_global"].fillna(0).clip(lower=0)
    # temperatura de la celda: fórmula NOCT (D13)
    t_celda = np.asarray(t_aire, float) + (v(s["noct_c"]) - 20) / 800 * poa.values
    # potencia DC -> pérdidas -> inversor
    dc = kwp * poa.values / 1000 * (1 + v(s["coef_temp_potencia_por_c"]) * (t_celda - 25))
    ac = dc * (1 - v(s["perdidas_sistema"])) * v(s["eficiencia_inversor"])
    ac = np.where(ghi.values <= 0, 0.0, np.clip(ac, 0, None))  # noche = 0
    return ac  # kW medios en la hora = kWh en esa hora


# ---------------------------------------------------- 5. consumo del hogar
def consumo_hogar(fechas: pd.Series, cfg, kwh_mes: float | None = None) -> np.ndarray:
    """kWh por hora: consumo mensual del hogar repartido con la forma diaria
    (día laboral / fin de semana) (D15)."""
    c = cfg["consumo"]
    kwh_mes = v(c["kwh_mes_hogar"]) if kwh_mes is None else kwh_mes
    lab = np.array(v(c["forma_dia_laboral"]), float); lab /= lab.sum()
    fds = np.array(v(c["forma_fin_de_semana"]), float); fds /= fds.sum()
    f = pd.DatetimeIndex(fechas) - pd.Timedelta(hours=1)  # hora de inicio
    peso = np.where(f.dayofweek >= 5, fds[f.hour], lab[f.hour])
    kwh_dia = kwh_mes * 12 / 365  # mismo consumo diario promedio todo el año
    return peso * kwh_dia


# ---------------------------------------------------- 6. balance y finanzas
def balance(prod: np.ndarray, cons: np.ndarray, fechas: pd.Series) -> pd.DataFrame:
    """Hora a hora: autoconsumo = lo que la casa usa del panel; excedente = lo que sobra."""
    auto = np.minimum(prod, cons)
    exced = np.clip(prod - cons, 0, None)
    imp = np.clip(cons - prod, 0, None)
    h = pd.DataFrame({"Fecha": fechas, "prod": prod, "cons": cons,
                      "auto": auto, "exced": exced, "imp": imp})
    return h


def factura_energia(kwh, estrato: int, cs: float, cfg, f_tarifa=1.0):
    """Lo que cobra EPM por la energía de un mes: los primeros kWh (consumo de
    subsistencia) a la tarifa de subsistencia y el resto a la tarifa plena."""
    t = v(cfg["finanzas"]["tarifas_epm_cop_kwh"])[estrato]
    kwh = np.asarray(kwh, float)
    return (np.minimum(kwh, cs) * t["subsistencia"]
            + np.clip(kwh - cs, 0, None) * t["plena"]) * f_tarifa


def consumo_subsistencia(altitud: float, cfg) -> float:
    cs = v(cfg["consumo"]["consumo_subsistencia_kwh_mes"])
    return cs["desde_1000m"] if altitud >= 1000 else cs["bajo_1000m"]


def ahorro_anual(h: pd.DataFrame, cfg, estrato: int, altitud: float,
                 f_tarifa=1.0, precio_exc2=None) -> dict:
    """Ahorro del primer año = factura SIN paneles - factura CON paneles, mes a mes.
    Con paneles (CREG 174/2021, Concepto CREG 3023/2026):
      - la casa compra a la red solo lo que el panel no cubre (importación);
      - el excedente hasta lo importado en el mes se descuenta kWh por kWh
        (crédito de energía), pero se paga el cargo de comercialización Cv;
      - el excedente que sobra se le paga a la familia a precio de bolsa/MC."""
    fz = cfg["finanzas"]
    cs = consumo_subsistencia(altitud, cfg)
    cv = v(fz["cargo_comercializacion_cop_kwh"])
    p2 = v(fz["precio_excedente_tramo2_cop"]) if precio_exc2 is None else precio_exc2
    m = h.groupby(h.Fecha.dt.month)[["prod", "cons", "auto", "exced", "imp"]].sum()
    t1 = np.minimum(m.exced, m.imp)          # crédito de energía
    t2 = m.exced - t1                        # excedente vendido
    sin_panel = factura_energia(m.cons, estrato, cs, cfg, f_tarifa)
    solo_auto = factura_energia(m.imp, estrato, cs, cfg, f_tarifa)
    con_credito = factura_energia(m.imp - t1, estrato, cs, cfg, f_tarifa) + cv * t1
    ahorro_auto = (sin_panel - solo_auto).sum()
    valor_credito = (solo_auto - con_credito).sum()
    valor_t2 = (t2 * p2).sum()
    return {
        "kWh_produccion": m["prod"].sum(), "kWh_consumo": m.cons.sum(),
        "kWh_autoconsumo": m.auto.sum(), "kWh_excedente": m.exced.sum(),
        "kWh_credito_energia": t1.sum(), "kWh_excedente_vendido": t2.sum(),
        "consumo_subsistencia_kwh_mes": cs,
        "factura_anual_sin_panel_cop": sin_panel.sum(),
        "ahorro_autoconsumo_cop": ahorro_auto,
        "valor_credito_energia_cop": valor_credito,
        "valor_excedente_vendido_cop": valor_t2,
    }


def tir(capex: float, flujo: np.ndarray) -> float:
    """Tasa interna de retorno: la tasa a la que el VPN da 0 (búsqueda por bisección)."""
    def vpn(r):
        return -capex + (flujo / (1 + r) ** np.arange(1, len(flujo) + 1)).sum()
    lo, hi = -0.99, 1.0
    if vpn(lo) * vpn(hi) > 0:
        return np.nan  # no hay una tasa que haga el VPN igual a 0
    for _ in range(100):
        mid = (lo + hi) / 2
        if vpn(lo) * vpn(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def finanzas(a: dict, cfg, kwp: float, costo_kwp=None, tasa=None) -> dict:
    """Flujo de caja a N años -> VPN, Payback simple y Payback descontado."""
    fz, s = cfg["finanzas"], cfg["sistema"]
    costo_kwp = v(fz["costo_por_kWp_cop"]) if costo_kwp is None else costo_kwp
    tasa = v(fz["tasa_descuento"]) if tasa is None else tasa
    n = int(v(fz["horizonte_anios"]))
    capex = costo_kwp * kwp
    om = capex * v(fz["mantenimiento_anual_pct_capex"])
    inc = v(fz["incremento_real_tarifa_anual"])
    ahorro1 = a["ahorro_autoconsumo_cop"] + a["valor_credito_energia_cop"] + a["valor_excedente_vendido_cop"]
    anios = np.arange(1, n + 1)
    # producción relativa: año 1 pierde la degradación inicial, luego 0.4 %/año
    rel = (1 - v(s["degradacion_primer_anio"])) * (1 - v(s["degradacion_anual"])) ** (anios - 1)
    flujo = ahorro1 * rel * (1 + inc) ** (anios - 1) - om
    flujo = flujo - np.where(anios == int(v(fz["anio_reemplazo_inversor"])),
                             v(fz["costo_reemplazo_inversor_cop"]), 0)
    desc = flujo / (1 + tasa) ** anios
    vpn = -capex + desc.sum()

    def payback(f):
        acum = -capex + np.cumsum(f)
        if acum[-1] < 0:
            return np.nan  # no se recupera en el horizonte
        i = int(np.argmax(acum >= 0))
        previo = -capex if i == 0 else acum[i - 1]
        return i + (-previo) / f[i]

    return {"capex_cop": capex, "mantenimiento_anual_cop": om, "ahorro_anio1_cop": ahorro1 * rel[0],
            "VPN_cop": vpn, "TIR": tir(capex, flujo), "payback_simple_anios": payback(flujo),
            "payback_descontado_anios": payback(desc),
            "co2_evitado_t_anio": a["kWh_produccion"] * v(fz["factor_emision_kgco2_kwh"]) / 1000}


# ---------------------------------------------------- 7. casos y sensibilidad
def simular(anio: pd.DataFrame, info: dict, fuente: str, cfg, estrato=None,
            kwp=None, costo_kwp=None, f_tarifa=1.0, precio_exc2=None, tasa=None,
            kwh_mes=None) -> dict:
    estrato = cfg["consumo"]["estrato"] if estrato is None else estrato
    kwp = v(cfg["sistema"]["kWp"]) if kwp is None else kwp
    prod = produccion_panel(anio[FUENTES[fuente]], anio["T2M"], anio["Fecha"],
                            info["Latitud"], info["Longitud"], info["Altitud"], cfg, kwp)
    cons = consumo_hogar(anio["Fecha"], cfg, kwh_mes)
    h = balance(prod, cons, anio["Fecha"])
    a = ahorro_anual(h, cfg, estrato, info["Altitud"], f_tarifa, precio_exc2)
    return a | finanzas(a, cfg, kwp, costo_kwp, tasa) | {
        "kWh_por_kWp": a["kWh_produccion"] / kwp,
        "GHI_kWh_m2_anio": anio[FUENTES[fuente]].sum() / 1000}


def cfg_con(cfg, inclinacion=None, azimut=None):
    """Copia de la ficha con otra inclinación o azimut del panel (no cambia el archivo)."""
    if inclinacion is None and azimut is None:
        return cfg
    c = copy.deepcopy(cfg)
    if inclinacion is not None:
        c["sistema"]["inclinacion_grados"]["valor"] = float(inclinacion)
    if azimut is not None:
        c["sistema"]["azimut_grados"]["valor"] = float(azimut)
    return c


def correr_casos(df: pd.DataFrame, casos: pd.DataFrame, cfg, inclinacion=None, azimut=None,
                 **cambios) -> pd.DataFrame:
    """Los casos x 3 fuentes. 'cambios' son opcionales y se pasan a simular:
    estrato, kwp, costo_kwp, kwh_mes, f_tarifa, precio_exc2, tasa."""
    cfg = cfg_con(cfg, inclinacion, azimut)
    filas = []
    for _, c in casos.iterrows():
        de = df[df.CodigoEstacion == c.CodigoEstacion]
        info = de.iloc[0][["Latitud", "Longitud", "Altitud"]].to_dict()
        anio = anio_tipico(de)
        for fuente in FUENTES:
            r = simular(anio, info, fuente, cfg, **cambios)
            filas.append({"Caso": c.Caso, "CodigoEstacion": c.CodigoEstacion,
                          "NombreEstacion": c.NombreEstacion, "Departamento": c.Departamento,
                          "Mejora_pct_modelo": c.Mejora_pct, "Fuente": fuente, **info, **r})
    res = pd.DataFrame(filas)
    # error de cada fuente frente al IDEAM (la "verdad")
    ref = res[res.Fuente == "IDEAM"].set_index("CodigoEstacion")
    for col in ["payback_simple_anios", "kWh_produccion", "VPN_cop"]:
        res[f"dif_{col}_vs_IDEAM"] = res[col] - res.CodigoEstacion.map(ref[col])
    return res


def sensibilidad(df: pd.DataFrame, casos: pd.DataFrame, cfg) -> pd.DataFrame:
    """Mueve UN parámetro a la vez (los demás fijos) a su valor bajo y alto,
    y mide el cambio de Payback y VPN."""
    claves = {"costo_por_kWp_cop": "costo_kwp", "tarifa_factor": "f_tarifa",
              "precio_excedente_tramo2_cop": "precio_exc2", "tasa_descuento": "tasa", "kWp": "kwp"}
    filas = []
    for _, c in casos.iterrows():
        de = df[df.CodigoEstacion == c.CodigoEstacion]
        info = de.iloc[0][["Latitud", "Longitud", "Altitud"]].to_dict()
        anio = anio_tipico(de)
        for fuente in FUENTES:
            base = simular(anio, info, fuente, cfg)
            for par, valores in cfg["sensibilidad"].items():
                for val in valores:
                    r = simular(anio, info, fuente, cfg, **{claves[par]: val})
                    filas.append({"Caso": c.Caso, "NombreEstacion": c.NombreEstacion,
                                  "Fuente": fuente, "Parametro": par, "Valor": val,
                                  "Payback_base": base["payback_simple_anios"],
                                  "Payback_nuevo": r["payback_simple_anios"],
                                  "Cambio_payback_anios": r["payback_simple_anios"] - base["payback_simple_anios"],
                                  "VPN_base": base["VPN_cop"], "VPN_nuevo": r["VPN_cop"]})
    return pd.DataFrame(filas)


def ficha_supuestos(cfg) -> pd.DataFrame:
    """Lista todos los parámetros con su fuente y estado (para la sección 7)."""
    filas = []
    for grupo in ["sistema", "consumo", "finanzas"]:
        for k, it in cfg[grupo].items():
            if isinstance(it, dict) and "valor" in it:
                filas.append({"Grupo": grupo, "Parametro": k, "Valor": str(it["valor"]),
                              "Fuente": it.get("fuente", ""), "Estado": it.get("estado", "")})
    return pd.DataFrame(filas)


# ---------------------------------------------------- 8. un punto cualquiera de Colombia
PARAMETROS_NASA = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]


def descargar_nasa_punto(lat: float, lon: float, anios: list, cfg, desfase_horas=None) -> pd.DataFrame:
    """Pide a NASA POWER los datos horarios de un punto y los deja con la MISMA forma
    que la tabla del equipo (mismas columnas y misma forma de marcar la hora).
    NECESITA INTERNET (en Colab funciona; desde algunos servidores no).
    - NASA entrega la hora mundial (UTC) marcando el INICIO de cada hora.
    - Se pasa a hora de Colombia y se marca el FINAL de la hora, como el IDEAM (D0).
    - desfase_horas: ajuste extra para que coincida con la limpieza del equipo
      (se averigua con calibrar_desfase_nasa)."""
    from pvlib.iotools import get_nasa_power
    if desfase_horas is None:
        desfase_horas = cfg["datos"].get("desfase_nasa_horas", 0) or 0
    partes, altitud = [], None
    for anio in anios:  # una consulta por año
        d, meta = get_nasa_power(lat, lon, f"{anio}-01-01", f"{anio}-12-31",
                                 parameters=PARAMETROS_NASA, community="re", map_variables=False)
        partes.append(d)
        altitud = meta["altitude"]
    d = pd.concat(partes)
    local_inicio = d.index.tz_convert(v(cfg["sistema"]["zona_horaria"])).tz_localize(None)
    fecha = local_inicio + pd.Timedelta(hours=1 + desfase_horas)
    out = pd.DataFrame({"Fecha": fecha, **{c: d[c].values for c in PARAMETROS_NASA}})
    out = out.dropna()
    out = out[out.ALLSKY_SFC_SW_DWN > 0]          # solo horas con sol, como la tabla
    out["Hora"], out["Mes"] = out.Fecha.dt.hour, out.Fecha.dt.month
    out["DiaDelAnio"] = out.Fecha.dt.dayofyear
    out["Latitud"], out["Longitud"], out["Altitud"] = lat, lon, altitud
    return out.reset_index(drop=True)


def calibrar_desfase_nasa(df_tabla: pd.DataFrame, codigo_estacion: int, anio: int, cfg) -> pd.DataFrame:
    """Comprobación: descarga NASA en una estación que ya está en la tabla y busca qué
    desfase de horas hace que los datos descargados coincidan con los de la tabla.
    El desfase con menor diferencia es el que hay que poner en config (desfase_nasa_horas)."""
    est = df_tabla[(df_tabla.CodigoEstacion == codigo_estacion) & (df_tabla.Fecha.dt.year == anio)]
    lat, lon = est.Latitud.iloc[0], est.Longitud.iloc[0]
    base = descargar_nasa_punto(lat, lon, [anio], cfg, desfase_horas=0)
    filas = []
    for k in range(-3, 4):
        b = base.assign(Fecha=base.Fecha + pd.Timedelta(hours=k))
        m = est.merge(b, on="Fecha", suffixes=("_tabla", "_descarga"))
        if len(m):
            dif = (m.ALLSKY_SFC_SW_DWN_tabla - m.ALLSKY_SFC_SW_DWN_descarga).abs().mean()
            filas.append({"desfase_horas": k, "horas_comparadas": len(m), "diferencia_promedio_Wh_m2": dif})
    return pd.DataFrame(filas).sort_values("diferencia_promedio_Wh_m2").reset_index(drop=True)


def calcular_punto(lat: float, lon: float, cfg, estrato=None, kwh_mes=None, kwp=None,
                   costo_kwp=None, anios=None, datos_nasa: pd.DataFrame | None = None,
                   f_tarifa=1.0, precio_exc2=None, tasa=None,
                   inclinacion=None, azimut=None) -> pd.DataFrame:
    """Todo el flujo para un lugar de Colombia SIN estación del IDEAM:
    1) descarga NASA del punto -> 2) el modelo corrige -> 3) el motor calcula.
    Devuelve el resultado con el sol del MODELO (el que se usaría) y con NASA (referencia)."""
    anios = anios or cfg["datos"].get("anios_nasa_punto", [2023, 2024, 2025])
    cfg = cfg_con(cfg, inclinacion, azimut)
    d = datos_nasa if datos_nasa is not None else descargar_nasa_punto(lat, lon, anios, cfg)
    d = predecir_modelo(d.drop(columns=["Prediccion"], errors="ignore"), cfg)
    anio = anio_tipico(d)
    info = {"Latitud": lat, "Longitud": lon, "Altitud": float(d.Altitud.iloc[0])}
    filas = []
    for fuente in ["MODELO", "NASA"]:
        r = simular(anio, info, fuente, cfg, estrato=estrato, kwp=kwp, costo_kwp=costo_kwp,
                    kwh_mes=kwh_mes, f_tarifa=f_tarifa, precio_exc2=precio_exc2, tasa=tasa)
        filas.append({"Fuente": fuente, **info, "estrato": estrato or cfg["consumo"]["estrato"],
                      "kWp": kwp or v(cfg["sistema"]["kWp"]), **r})
    return pd.DataFrame(filas)


NOMBRES_LEGIBLES = {
    "NombreEstacion": "Lugar", "Caso": "Caso", "Fuente": "Fuente del sol", "estrato": "Estrato",
    "kWp": "Tamaño (kWp)", "Altitud": "Altura (m)",
    "kWh_produccion": "Energía producida (kWh/año)", "kWh_consumo": "Consumo casa (kWh/año)",
    "kWh_autoconsumo": "Usada directo en casa (kWh/año)", "kWh_excedente": "Sobra a la red (kWh/año)",
    "factura_anual_sin_panel_cop": "Factura sin paneles ($/año)", "ahorro_anio1_cop": "Ahorro año 1 ($)",
    "capex_cop": "Inversión ($)", "payback_simple_anios": "Payback simple (años)",
    "payback_descontado_anios": "Payback descontado (años)", "VPN_cop": "VPN ($)", "TIR": "TIR",
    "co2_evitado_t_anio": "CO2 evitado (t/año)", "kWh_por_kWp": "kWh por kWp al año",
}


def resumen_legible(res: pd.DataFrame) -> pd.DataFrame:
    """Tabla con nombres en español y números redondeados, para mostrar o exportar."""
    cols = [c for c in NOMBRES_LEGIBLES if c in res.columns]
    t = res[cols].rename(columns=NOMBRES_LEGIBLES).copy()
    for c in t.columns:
        if c == "TIR":
            t[c] = [f"{x * 100:.1f} %" if pd.notna(x) else "—" for x in t[c]]
        elif c.startswith("Payback"):
            t[c] = [round(x, 2) if pd.notna(x) and np.isfinite(x) else "no se recupera en 25 años"
                    for x in t[c]]
        elif pd.api.types.is_numeric_dtype(t[c]):
            t[c] = t[c].round(2)
    return t


def main(ruta_config="config_r3.yaml"):
    cfg = cargar_config(ruta_config)
    out = Path(cfg["datos"]["carpeta_resultados"]); out.mkdir(exist_ok=True)
    if usa_entrega_r2(cfg):                       # predicciones ya hechas por R2
        df = cargar_predicciones_r2(cfg)
        casos = cargar_casos_r2(cfg)
        mejora = cargar_mejora_r2(cfg)
    elif usa_serie_r2(cfg):                       # VERSIÓN FINAL: serie horaria de R2 (fuera de pliegue)
        df = cargar_serie_r2(cfg)
        casos = cargar_casos_r2(cfg, df)
        mejora = cargar_mejora_r2(cfg)
        print(f"Serie horaria de R2: {len(df):,} horas de {df.CodigoEstacion.nunique()} casos "
              f"(modelo fuera de pliegue, columna {cfg['datos'].get('columna_modelo_r2', 'Modelo_principal')}).")
    elif usa_excel_casos(cfg):                    # modelo final + casos del Excel de evaluación
        casos = cargar_casos_r2(cfg)
        df = predecir_modelo(cargar_tabla(cfg, casos.CodigoEstacion), cfg)
        casos = cargar_casos_r2(cfg, df)
        mejora = cargar_mejora_r2(cfg)
        print(f"Modelo {Path(cfg['datos']['modelo']).name} sobre {len(df):,} horas de "
              f"{df.CodigoEstacion.nunique()} estaciones ({df.Fecha.dt.year.min()}-{df.Fecha.dt.year.max()}).")
    else:
        df = predecir_modelo(cargar_tabla(cfg), cfg)
        mejora = cargar_tabla_mejora_r2(cfg, tabla_mejora_por_estacion(df, cfg))
        casos = elegir_casos(mejora, cfg)
    res = correr_casos(df, casos, cfg)
    sen = sensibilidad(df, casos, cfg)
    mejora.to_parquet(out / "r3_mejora_por_estacion.parquet", index=False)
    mejora.round(4).to_csv(out / "r3_mejora_por_estacion.csv", index=False)  # para abrir en Excel
    res.to_parquet(out / "r3_resultados_casos.parquet", index=False)
    res.round(3).to_csv(out / "r3_resultados_casos.csv", index=False)
    sen.to_parquet(out / "r3_sensibilidad.parquet", index=False)
    ficha_supuestos(cfg).to_csv(out / "r3_ficha_supuestos.csv", index=False)
    return mejora, casos, res, sen


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "config_r3.yaml")
