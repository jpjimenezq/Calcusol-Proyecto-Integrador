"""
EVALUACIÓN DEL LIGHTGBM NUEVO (R2) + ENTREGA PARA R3 — UN SOLO ARCHIVO
Uso: deja este archivo junto a la carpeta 'datos' (con dataset_modelo.parquet y modelo_lgbm.joblib) y pulsa ▶ en VS Code.
Con MODO_PRUEBA = True corre en pocos minutos con datos y árboles reducidos (los números NO sirven, solo prueba que todo funciona).
"""
import sys
import os
import subprocess
import importlib

# ---- Instalar lo que falte, en el mismo Python que ejecuta este archivo ----
PAQUETES = [("numpy", "numpy"), ("pandas", "pandas"), ("pyarrow", "pyarrow"), ("scipy", "scipy"), ("sklearn", "scikit-learn"),
            ("joblib", "joblib"), ("lightgbm", "lightgbm"), ("pvlib", "pvlib"), ("openpyxl", "openpyxl")]
for modulo, paquete in PAQUETES:
    try:
        importlib.import_module(modulo)
    except ImportError:
        print(f"Instalando {paquete} (solo la primera vez)...", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", paquete])

import time
import types
from datetime import datetime
import joblib
from pathlib import Path
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter as L
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from sklearn.base import clone
from sklearn.linear_model import LinearRegression
from pvlib import solarposition, irradiance

# =====================================================================
# CONFIGURACIÓN
# =====================================================================
try:
    CARPETA = Path(__file__).resolve().parent
except NameError:
    CARPETA = Path.cwd()
RUTA_PARQUET = CARPETA / "datos" / "dataset_modelo.parquet"
RUTA_MODELO_NUEVO = CARPETA / "datos" / "modelo_lgbm.joblib"
RUTA_SALIDA = CARPETA / "resultados" / "evaluacion_lightgbm.xlsx"
REMUESTREOS = 2000          # remuestreos de los intervalos de confianza
SEMILLA = 42
MODO_PRUEBA = False         # True = prueba rápida de que todo funciona (números sin valor)

# --- Entrega a R3 ---
MODELO_R3 = "LGBM"          # modelo que se entrega a R3: "LGBM" (con ubicación) o "LGBM_sin_ub" (sin latitud/longitud/altitud)
METRICA_CASOS = "mes"       # métrica de la regla D16: "mes" (recomendada por R1: es la de negocio) o "hora"
VILLETA_CODIGO = 23065180   # código de la estación Villeta (caso narrativo)
MIN_FILAS_SITIO = 15000     # un sitio tiene "datos suficientes" si tiene al menos estas filas...
MIN_FILAS_ANIO = 3500       # ...y al menos estas en su mejor año (un año casi completo ronda las 4,300)
ANIOS_POR_CASO = 1          # años de serie horaria que se entregan por caso (los de más datos)

# --- Validación cruzada de R2 ---
CV_K = 5                    # pliegues
CV_CON_SIN_UBICACION = True # también evalúa el LightGBM sin latitud/longitud/altitud
CV_ESQUEMA_CELDA = True     # repite la validación agrupando por celda NASA (D10)
CV_CURVA = True             # curva de aprendizaje (10, 25, 50 y 100 % de los sitios)
ANIO_CORTE = 2020           # se entrena con años <= ANIO_CORTE; "futuro" = años posteriores (igual que el notebook v2)
FRACCIONES = (0.10, 0.25, 0.50)
HACER_AB_PRELIMINARES = True  # prueba de robustez: ¿los datos preliminares empeoran el modelo?

for ruta, nombre in ((RUTA_PARQUET, "la tabla de datos"), (RUTA_MODELO_NUEVO, "el modelo nuevo")):
    if not ruta.exists():
        sys.exit(f"\nNo encuentro {nombre}: {ruta}\nCopia el archivo '{ruta.name}' a la carpeta '{ruta.parent}' y vuelve a ejecutar.")
if MODELO_R3 == "LGBM_sin_ub" and not CV_CON_SIN_UBICACION:
    sys.exit("MODELO_R3 = 'LGBM_sin_ub' requiere CV_CON_SIN_UBICACION = True.")
(CARPETA / "resultados").mkdir(exist_ok=True)
C = types.SimpleNamespace(RAIZ=CARPETA, RUTA_PARQUET=RUTA_PARQUET, SEMILLA=SEMILLA, RADIO_SITIO_KM=1.0, CARPETA_RESULTADOS=CARPETA / "resultados")
args = types.SimpleNamespace(modelo=str(RUTA_MODELO_NUEVO), salida=str(RUTA_SALIDA), B=REMUESTREOS)

# =====================================================================
# FUNCIONES DE APOYO
# =====================================================================
FEATURES = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M", "Latitud", "Longitud", "Altitud",
            "Hora_sin", "Hora_cos", "Mes_sin", "Mes_cos", "Dia_sin", "Dia_cos"]
MIN_FILAS_SEGMENTO = 1000   # mínimo de filas para decir algo de un segmento


def agregar_ciclicas(df):
    """Hora, mes y día del año como seno/coseno (para que el modelo entienda que son cíclicos)."""
    df["Hora_sin"] = np.sin(2 * np.pi * df["Hora"] / 24.0)
    df["Hora_cos"] = np.cos(2 * np.pi * df["Hora"] / 24.0)
    df["Mes_sin"] = np.sin(2 * np.pi * df["Mes"] / 12.0)
    df["Mes_cos"] = np.cos(2 * np.pi * df["Mes"] / 12.0)
    df["Dia_sin"] = np.sin(2 * np.pi * df["DiaDelAnio"] / 365.25)
    df["Dia_cos"] = np.cos(2 * np.pi * df["DiaDelAnio"] / 365.25)
    return df


def calcular_sitio(df, km=C.RADIO_SITIO_KM):
    """Agrupa estaciones a <= km de distancia en un 'Sitio' (se nombra con el menor código del grupo)."""
    est = df.groupby("CodigoEstacion")[["Latitud", "Longitud"]].first().reset_index()
    la, lo = np.radians(est["Latitud"].values), np.radians(est["Longitud"].values)
    a = (np.sin((la[:, None] - la) / 2) ** 2 + np.cos(la[:, None]) * np.cos(la) * np.sin((lo[:, None] - lo) / 2) ** 2)
    dist = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    _, etiqueta = connected_components(csr_matrix(dist <= km))
    est["Sitio"] = est.groupby(etiqueta)["CodigoEstacion"].transform("min")
    return est[["CodigoEstacion", "Sitio"]]


def bandas_altura(alt):
    return pd.cut(alt, [-1, 500, 1000, 1500, 2000, 2500, 10000],
                  labels=["0-500 m", "500-1000 m", "1000-1500 m", "1500-2000 m", "2000-2500 m", "mas de 2500 m"])


def bandas_nubosidad(kt):
    return pd.cut(kt, [-0.01, 0.3, 0.4, 0.5, 0.6, 2],
                  labels=["muy nublado (<0.3)", "nublado (0.3-0.4)", "medio (0.4-0.5)", "poco nublado (0.5-0.6)", "despejado (>=0.6)"])


def agregar_errores(df, modelos):
    """e_<modelo> = error absoluto; s_<modelo> = error con signo (predicción - medición)."""
    for m, c in modelos.items():
        df["e_" + m] = (df[c] - df["Valor"]).abs()
        df["s_" + m] = df[c] - df["Valor"]
    return df


def escribir_hoja(wb, nombre, df, formulas=(), pct=(), dec1=(), signo=(), dec4=(), fechas=(), ancho=14, nota=None):
    """Escribe una tabla como hoja de Excel. 'formulas' = [(columna, plantilla)] con {r} = fila y {Columna} = letra de esa columna."""
    ws = wb.create_sheet(nombre[:31])
    cols = list(df.columns) + [n for n, _ in formulas]
    letra = {c: L(i + 1) for i, c in enumerate(cols)}
    fila0 = 1
    if nota:
        ws.cell(row=1, column=1, value=nota).font = Font(name="Arial", size=9, italic=True)
        fila0 = 3
    for j, c in enumerate(cols, 1):
        h = ws.cell(row=fila0, column=j, value=c)
        h.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        h.fill = PatternFill("solid", fgColor="1F3864")
        h.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        ws.column_dimensions[L(j)].width = ancho
    ws.row_dimensions[fila0].height = 36
    ws.column_dimensions["A"].width = max(ancho, 24)
    for i, fila in enumerate(df.itertuples(index=False), fila0 + 1):
        for j, v in enumerate(fila, 1):
            if isinstance(v, pd.Timestamp):
                v = v.to_pydatetime()
            elif hasattr(v, "item"):
                v = v.item()
            if isinstance(v, float) and v != v:
                v = None
            if not isinstance(v, (int, float, str, bool, type(None), datetime)):
                v = str(v)
            ws.cell(row=i, column=j, value=v)
        for j, (n, f) in enumerate(formulas, len(df.columns) + 1):
            ws.cell(row=i, column=j, value=f.format(r=i, **letra))
        for j, c in enumerate(cols, 1):
            cell = ws.cell(row=i, column=j)
            cell.font = Font(name="Arial", size=10)
            if c in pct:
                cell.number_format = "0.0%;-0.0%;0.0%"
            elif c in dec4:
                cell.number_format = "0.0000"
            elif c in fechas:
                cell.number_format = "yyyy-mm-dd hh:mm"
            elif c in dec1:
                cell.number_format = "0.0"
            elif c in signo:
                cell.number_format = "+0.0;-0.0;0.0"
            elif c in ("filas", "estaciones"):
                cell.number_format = "#,##0"
    ws.freeze_panes = ws.cell(row=fila0 + 1, column=2)
    ws.auto_filter.ref = f"A{fila0}:{L(len(cols))}{fila0 + len(df)}"
    return ws


def f_mejora(modelo, base="MAE_B0"):
    """Fórmula de Excel de la mejora: =1 - error del modelo / error de la referencia."""
    return "=1-{%s}{r}/{%s}{r}" % (modelo, base)


U = types.SimpleNamespace(FEATURES=FEATURES, MIN_FILAS_SEGMENTO=MIN_FILAS_SEGMENTO, agregar_ciclicas=agregar_ciclicas, calcular_sitio=calcular_sitio,
                          bandas_altura=bandas_altura, bandas_nubosidad=bandas_nubosidad, agregar_errores=agregar_errores, escribir_hoja=escribir_hoja)
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:5.0f}s] {m}", flush=True)


def sub(d):
    """En MODO_PRUEBA usa solo 1 de cada 8 filas (para probar rápido)."""
    return d.iloc[::8] if MODO_PRUEBA else d


FEAT_NUEVO = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M", "Latitud", "Longitud", "Altitud", "zenit",
              "azimut", "g0_horiz", "razon_cielo", "kt_calc", "hora_sin", "hora_cos", "dia_sin", "dia_cos", "Mes"]
SIN = [f for f in FEATURES if f not in ("Latitud", "Longitud", "Altitud")]
FEAT_LGB_SIN = [f for f in FEAT_NUEVO if f not in ("Latitud", "Longitud", "Altitud")]
EX_ESP, EX_TEM = "Espacial (24 estaciones nuevas)", "Temporal (2021 en adelante)"

# =====================================================================
# 1. PARTICIÓN EXACTA DEL NOTEBOOK v2 (train <= 2020 | temporal >= 2021 | espacial = 24 estaciones reservadas)
# =====================================================================
df = pd.read_parquet(C.RUTA_PARQUET)
faltan = [c for c in ("Valor", "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M", "Latitud", "Longitud", "Altitud", "Hora", "Mes",
                      "DiaDelAnio", "EsPreliminar", "CodigoEstacion", "Fecha") if c not in df.columns]
assert not faltan, f"A la tabla le faltan columnas: {faltan}"
if "Departamento" not in df.columns:
    print("Aviso: la tabla no tiene Departamento; se usará 'Sin dato'.")
    df["Departamento"] = "Sin dato"
if "NombreEstacion" not in df.columns:
    df["NombreEstacion"] = df["CodigoEstacion"].astype(str)
crudo = df.copy()                                    # copia con preliminares (para el A/B y el embudo)
df = df[~df["EsPreliminar"]].copy()                  # el modelo se entrena y evalúa solo con datos definitivos
for c in ["Valor", "ALLSKY_SFC_SW_DWN"]:
    df = df[(df[c] >= 0) & (df[c] <= 1500)]
df["ts"] = pd.to_datetime(df["Fecha"])
df["year"] = df["ts"].dt.year
df = df.drop_duplicates(["CodigoEstacion", "ts"]).reset_index(drop=True)
rng = np.random.default_rng(42)
est = df.groupby("CodigoEstacion")["Altitud"].first().reset_index()
est["alt_bin"] = pd.qcut(est["Altitud"], 4, labels=False, duplicates="drop")
hold = []                                            # 20 % de las estaciones de cada cuartil de altitud
for _, g in est.groupby("alt_bin"):
    hold += list(rng.choice(g["CodigoEstacion"].values, size=max(1, round(len(g) * 0.2)), replace=False))
is_hold, is_recent = df["CodigoEstacion"].isin(hold), df["year"] >= 2021
split = {"train": df[~is_hold & ~is_recent].copy(), "temporal": df[~is_hold & is_recent].copy(), "espacial": df[is_hold].copy()}
assert (len(split["train"]), len(split["temporal"]), len(split["espacial"])) == (2250748, 151597, 828775), "la partición no coincide con el notebook"
log("Partición idéntica a la del notebook v2: " + ", ".join(f"{k} {len(v):,}" for k, v in split.items()))
ruta_sitio = C.CARPETA_RESULTADOS / "estacion_sitio.csv"
if ruta_sitio.exists():
    sitio = pd.read_csv(ruta_sitio).set_index("CodigoEstacion")["Sitio"]
else:
    sitio = calcular_sitio(df).set_index("CodigoEstacion")["Sitio"]
sitio = sitio.reindex(sorted(df["CodigoEstacion"].unique()))
sitio = sitio.fillna(pd.Series(sitio.index, index=sitio.index)).astype(int)   # estaciones sin Sitio: su propio código
df["Sitio"] = df["CodigoEstacion"].map(sitio)
for v in split.values():
    v["Sitio"] = v["CodigoEstacion"].map(sitio)


# =====================================================================
# 2. VARIABLES DEL MODELO NUEVO (geometría solar con pvlib; igual que el notebook v2)
# =====================================================================
def build_features(d):
    d = d.copy()
    t_mid = d["ts"] + pd.Timedelta(minutes=30)   # (igual que el notebook v2; ver nota en LEEME_R3 sobre la convención de hora)
    idx = pd.DatetimeIndex(t_mid.dt.tz_localize("America/Bogota").dt.tz_convert("UTC"))
    zen = np.empty(len(d)); azi = np.empty(len(d)); g0 = np.empty(len(d))
    st = d["CodigoEstacion"].values; pos = np.arange(len(d))
    for s in np.unique(st):
        m = st == s
        lat, lon, alt = d.loc[m, ["Latitud", "Longitud", "Altitud"]].iloc[0]
        sp = solarposition.get_solarposition(idx[m], lat, lon, altitude=alt)
        zen[pos[m]] = sp["apparent_zenith"].values
        azi[pos[m]] = sp["azimuth"].values
        g0[pos[m]] = irradiance.get_extra_radiation(idx[m]).values
    d["zenit"], d["azimut"], d["g0_extra"] = zen, azi, g0
    d = d[(d["zenit"] < 90) & (d["CLRSKY_SFC_SW_DWN"] > 0) & (d["ALLSKY_SFC_SW_DWN"] > 0)].copy()
    d["g0_horiz"] = d["g0_extra"] * np.cos(np.radians(d["zenit"])).clip(0)
    d["razon_cielo"] = (d["ALLSKY_SFC_SW_DWN"] / d["CLRSKY_SFC_SW_DWN"]).clip(0, 1.5)
    d["kt_calc"] = (d["ALLSKY_SFC_SW_DWN"] / d["g0_horiz"].replace(0, np.nan)).clip(0, 1.5)
    d["hora_sin"] = np.sin(2 * np.pi * d["Hora"] / 24)
    d["hora_cos"] = np.cos(2 * np.pi * d["Hora"] / 24)
    d["dia_sin"] = np.sin(2 * np.pi * d["DiaDelAnio"] / 365.25)
    d["dia_cos"] = np.cos(2 * np.pi * d["DiaDelAnio"] / 365.25)
    return d.dropna(subset=["kt_calc"])


ev = {}
for n in ("temporal", "espacial"):
    ev[n] = build_features(split[n])
    log(f"variables listas: {n} {len(split[n]):,} -> {len(ev[n]):,} filas de sol")
assert (len(ev["temporal"]), len(ev["espacial"])) == (138808, 755769), "las filas de sol no coinciden con el notebook"

# =====================================================================
# 3. MODELOS Y PREDICCIONES EN LAS MISMAS FILAS DE EXAMEN
# =====================================================================
lgbm = joblib.load(args.modelo)                      # modelo nuevo: predice el RESIDUO (IDEAM - NASA); la predicción final es NASA + residuo, >= 0


def lgbm_nuevo():
    """Copia sin entrenar del modelo nuevo (mismos ajustes), forzada a CPU."""
    m = clone(lgbm).set_params(device="cpu")
    return m.set_params(n_estimators=5) if MODO_PRUEBA else m


tr = split["train"]
tr = sub(tr[(tr["CLRSKY_SFC_SW_DWN"] > 0) & (tr["ALLSKY_SFC_SW_DWN"] > 0)].copy())
tr = agregar_ciclicas(tr)
ftr = sub(build_features(split["train"]))
log(f"variables de entrenamiento listas: {len(ftr):,} filas")
residuo = (ftr["Valor"] - ftr["ALLSKY_SFC_SW_DWN"]).astype("float32")
lgbm_sin = lgbm_nuevo().fit(ftr[FEAT_LGB_SIN].astype("float32"), residuo)
log("LightGBM sin ubicación reentrenado con los mismos ajustes (14 variables)")
lin = LinearRegression().fit(tr[["ALLSKY_SFC_SW_DWN"]], tr["Valor"])
for n, d in ev.items():
    d = agregar_ciclicas(d)
    base = d["ALLSKY_SFC_SW_DWN"].values
    d["pred_B1"] = np.clip(lin.predict(d[["ALLSKY_SFC_SW_DWN"]]), 0, 1400)
    d["pred_LGBM"] = np.clip(base + lgbm.predict(d[FEAT_NUEVO].astype("float32")), 0, None)
    d["pred_LGBM_sin"] = np.clip(base + lgbm_sin.predict(d[FEAT_LGB_SIN].astype("float32")), 0, None)
    d["Mes"] = d["Mes"].astype(int)
    ev[n] = d
log("predicciones listas")
MOD = {"B0": "ALLSKY_SFC_SW_DWN", "B1": "pred_B1", "LGBM_nuevo": "pred_LGBM", "LGBM_sin_ubicacion": "pred_LGBM_sin"}
MOD_TODOS = dict(MOD)
NOMBRES = {"B0": "B-0 NASA sin corregir", "B1": "B-1 recta", "LGBM_nuevo": "LightGBM nuevo", "LGBM_sin_ubicacion": "LightGBM sin ubicación (reentrenado)"}


# =====================================================================
# 4. MÉTRICAS (hora, día y MES; el mes es la métrica de negocio según R1)
# =====================================================================
def m_nb(y, p):
    e = y - p
    return {"MAE": np.mean(np.abs(e)), "RMSE": np.sqrt(np.mean(e ** 2)), "MBE": np.mean(p - y),
            "R2": 1 - np.sum(e ** 2) / np.sum((y - y.mean()) ** 2), "sesgo": 100 * (p.mean() - y.mean()) / y.mean()}


def niveles(d, col):
    d = d.assign(pred=d[col])
    o = {"hora": m_nb(d["Valor"].values, d["pred"].values)}
    for niv, per in (("dia", d["ts"].dt.date), ("mes", d["ts"].dt.to_period("M"))):
        g = d.groupby(["CodigoEstacion", per]).agg(y=("Valor", "mean"), p=("pred", "mean"))
        o[niv] = m_nb(g["y"].values, g["p"].values)
    return o


filas = []
for n, d in ev.items():
    nombre_ex = EX_ESP if n == "espacial" else EX_TEM
    niv_b0, niv_b1 = niveles(d, MOD["B0"]), niveles(d, MOD["B1"])
    for k, col in MOD_TODOS.items():
        nv = niveles(d, col)
        valido, nota = "Sí", ""
        filas.append({"examen": nombre_ex, "uso_recomendado": "Cifra defendible (lugares nuevos)" if n == "espacial" else "Optimista: estaciones ya vistas",
                      "modelo": NOMBRES[k], "valido_como_examen": valido, "filas": len(d), "estaciones": d["CodigoEstacion"].nunique(),
                      "MAE_hora": nv["hora"]["MAE"], "MAE_hora_B0": niv_b0["hora"]["MAE"], "MAE_hora_B1": niv_b1["hora"]["MAE"],
                      "RMSE_hora": nv["hora"]["RMSE"], "MBE_hora": nv["hora"]["MBE"], "sesgo_pct": nv["hora"]["sesgo"], "R2_hora": nv["hora"]["R2"],
                      "MAE_dia": nv["dia"]["MAE"], "MAE_dia_B0": niv_b0["dia"]["MAE"], "MAE_mes": nv["mes"]["MAE"], "MAE_mes_B0": niv_b0["mes"]["MAE"],
                      "MAE_mes_B1": niv_b1["mes"]["MAE"], "nota": nota})
resumen = pd.DataFrame(filas)
chk = resumen[(resumen.examen == EX_ESP) & (resumen.modelo == NOMBRES["LGBM_nuevo"])].iloc[0]
log(f"Control contra el notebook (LightGBM, espacial, hora): MAE {chk.MAE_hora:.1f} (nb 115.0) | RMSE {chk.RMSE_hora:.1f} (154.9) | "
    f"R2 {chk.R2_hora:.3f} (0.634) | mes {chk.MAE_mes:.1f} (47.2)")


# =====================================================================
# 5. INTERVALOS DE CONFIANZA (remuestreo de Sitios completos)
# =====================================================================
def bootstrap(d, mod, pares, B):
    d = agregar_errores(d.copy(), mod)
    g = d.groupby("Sitio")
    nom = list(mod)
    S = g[["e_" + m for m in nom]].sum().values
    n = g.size().values
    idx = np.random.default_rng(C.SEMILLA).integers(0, len(n), size=(B, len(n)))
    N = n[idx].sum(axis=1)
    mb = {m: S[idx, j].sum(axis=1) / N for j, m in enumerate(nom)}
    m0 = {m: S[:, j].sum() / n.sum() for j, m in enumerate(nom)}
    out = []

    def add(q, p, dist, f):
        lo, hi = np.percentile(dist, [2.5, 97.5])
        out.append({"cantidad": q, "estimacion": p, "IC95_inferior": lo, "IC95_superior": hi, "pct_remuestreos_a_favor": float(f(dist).mean() * 100)})

    for m in nom:
        add(f"MAE hora {NOMBRES[m]}", m0[m], mb[m], lambda x: x > -1)
    for m in nom:
        if m != "B0":
            add(f"Mejora vs NASA: {NOMBRES[m]}", 1 - m0[m] / m0["B0"], 1 - mb[m] / mb["B0"], lambda x: x > 0)
    for m in nom:
        if m not in ("B0", "B1"):
            add(f"Mejora vs recta B-1: {NOMBRES[m]}", 1 - m0[m] / m0["B1"], 1 - mb[m] / mb["B1"], lambda x: x > 0)
    for a, b in pares:
        add(f"Diferencia de MAE: {NOMBRES[a]} menos {NOMBRES[b]} (negativo = el primero es mejor)", m0[a] - m0[b], mb[a] - mb[b], lambda x: x < 0)
    return pd.DataFrame(out)


pares = [("LGBM_nuevo", "LGBM_sin_ubicacion")]
ic = []
for n, d in ev.items():
    t = bootstrap(d, MOD, pares, args.B)
    t.insert(0, "examen", EX_ESP if n == "espacial" else EX_TEM)
    ic.append(t)
ic = pd.concat(ic, ignore_index=True)
log("intervalos de confianza listos")

# =====================================================================
# 6. ERRORES POR ESTACIÓN Y POR SEGMENTO (horario y mensual) + CELDAS NASA
# =====================================================================
for n, d in ev.items():
    agregar_errores(d, MOD)
    d["Altura"] = bandas_altura(d["Altitud"])
    d["Nubosidad"] = bandas_nubosidad(d["ALLSKY_KT"])
    d["Departamento_"] = d["Departamento"]
    d["ym"] = d["ts"].dt.to_period("M")


def seg(d, col):
    g = d.groupby(col, observed=True)
    t = g.agg(filas=("Valor", "size"), estaciones=("CodigoEstacion", "nunique"), MAE_B0=("e_B0", "mean"), MAE_B1=("e_B1", "mean"),
              MAE_LGBM=("e_LGBM_nuevo", "mean"), MAE_LGBM_sin_ub=("e_LGBM_sin_ubicacion", "mean"),
              MBE_B0=("s_B0", "mean"), MBE_LGBM=("s_LGBM_nuevo", "mean"))
    t = t.reset_index()
    t[col] = t[col].astype(str)
    return t


def seg_mes(d, col):
    """Error MENSUAL por segmento (solo tiene sentido para segmentos fijos por estación: departamento y altura)."""
    m = d.groupby(["CodigoEstacion", "ym", col], observed=True).agg(
        y=("Valor", "mean"), b=("ALLSKY_SFC_SW_DWN", "mean"), l=("pred_LGBM", "mean"), ls=("pred_LGBM_sin", "mean")).reset_index()
    for k in ("b", "l", "ls"):
        m["e" + k] = (m[k] - m["y"]).abs()
    t = m.groupby(col, observed=True).agg(MAE_mes_B0=("eb", "mean"), MAE_mes_LGBM=("el", "mean"), MAE_mes_LGBM_sin_ub=("els", "mean")).reset_index()
    t[col] = t[col].astype(str)
    return t


def hav(la1, lo1, la2, lo2):
    a = np.sin(np.radians(la2 - la1) / 2) ** 2 + np.cos(np.radians(la1)) * np.cos(np.radians(la2)) * np.sin(np.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(a))


coord = df.groupby("CodigoEstacion")[["Latitud", "Longitud"]].first()
alt_est = df.groupby("CodigoEstacion")["Altitud"].first()
nombres_est = df.groupby("CodigoEstacion")["NombreEstacion"].first().astype(str).str.replace(r"\s*\[\d+\]", "", regex=True).str.strip()
tr_est = sorted(split["train"]["CodigoEstacion"].unique())
todas = sorted(df["CodigoEstacion"].unique())
# Celda NASA: estaciones que reciben EXACTAMENTE la misma serie de la NASA (>= 500 horas en común y diferencia < 0.01)
sidx = {c: i for i, c in enumerate(todas)}
codes, _ = pd.factorize(df["ts"])
M = np.full((codes.max() + 1, len(todas)), np.nan, np.float32)
M[codes, df["CodigoEstacion"].map(sidx).values] = df["ALLSKY_SFC_SW_DWN"].values
mismo = np.zeros((len(todas), len(todas)), bool)
for i in range(len(todas)):
    ci = M[:, [i]]
    both = ~np.isnan(ci) & ~np.isnan(M)
    dif = np.abs(ci - M)
    dif[~both] = 0
    mismo[i] = (both.sum(axis=0) >= 500) & (dif.max(axis=0) < 0.01)
    mismo[i, i] = False
_, lab = connected_components(csr_matrix(mismo | mismo.T))
celda = pd.Series(lab, index=todas)
celdas = celda.groupby(celda).size()
dmax = 0.0
for c_id in celdas[celdas > 1].index:
    mi = [todas[j] for j in np.where(lab == c_id)[0]]
    for a in range(len(mi)):
        for b in range(a + 1, len(mi)):
            dmax = max(dmax, float(hav(*coord.loc[mi[a]], *coord.loc[mi[b]])))
del M
log(f"celdas NASA: {len(celdas)} celdas para {len(todas)} estaciones; {int((celdas > 1).sum())} con más de una estación; distancia máxima dentro de una celda {dmax:.1f} km")


def info_est(c):
    """Distancia al sensor de entrenamiento más cercano y si comparte celda NASA con alguno."""
    if c in tr_est:
        return 0.0, "es de entrenamiento"
    dd = [hav(*coord.loc[c], *coord.loc[t]) for t in tr_est]
    comp = [t for t in tr_est if celda[t] == celda[c]]
    return float(np.min(dd)), ("Sí (" + str(len(comp)) + ")" if comp else "No")


por_est = []
for n, d in ev.items():
    mm = d.groupby(["CodigoEstacion", "ym"]).agg(y=("Valor", "mean"), b=("ALLSKY_SFC_SW_DWN", "mean"), l=("pred_LGBM", "mean")).reset_index()
    mm["eb"] = (mm.y - mm.b).abs()
    mm["el"] = (mm.y - mm.l).abs()
    mm = mm.groupby("CodigoEstacion")[["eb", "el"]].mean()
    for c, gg in d.groupby("CodigoEstacion"):
        dist, comp = info_est(c) if n == "espacial" else (np.nan, "")
        por_est.append({"examen": EX_ESP if n == "espacial" else EX_TEM, "CodigoEstacion": int(c), "Sitio": int(gg["Sitio"].iloc[0]),
                        "Departamento": gg["Departamento"].iloc[0], "Altitud": float(gg["Altitud"].iloc[0]), "filas": len(gg),
                        "anios": f"{gg['year'].min()}-{gg['year'].max()}", "MAE_B0": gg["e_B0"].mean(), "MAE_B1": gg["e_B1"].mean(),
                        "MAE_LGBM": gg["e_LGBM_nuevo"].mean(), "MAE_LGBM_sin_ub": gg["e_LGBM_sin_ubicacion"].mean(),
                        "MAE_mes_B0": mm.loc[c, "eb"], "MAE_mes_LGBM": mm.loc[c, "el"],
                        "km_al_sensor_de_entrenamiento_mas_cercano": dist, "comparte_celda_NASA_con_entrenamiento": comp})
por_est = pd.DataFrame(por_est)

segs = {}
for dim, col in (("departamento", "Departamento_"), ("hora", "Hora"), ("altura", "Altura"), ("nubosidad", "Nubosidad")):
    partes = []
    for n, d in ev.items():
        t = seg(d, col)
        if dim in ("departamento", "altura"):
            t = t.merge(seg_mes(d, col), on=col, how="left")
        t.insert(0, "examen", EX_ESP if n == "espacial" else EX_TEM)
        partes.append(t.rename(columns={col: "segmento"}))
    segs[dim] = pd.concat(partes, ignore_index=True)
nosup = []
for dim, t in segs.items():
    x = t[(t.filas >= MIN_FILAS_SEGMENTO) & (t.MAE_LGBM >= t.MAE_B0)].copy()
    x.insert(1, "dimension", dim)
    nosup.append(x)
x = por_est[(por_est.filas >= MIN_FILAS_SEGMENTO) & (por_est.MAE_LGBM >= por_est.MAE_B0)].copy().rename(columns={"CodigoEstacion": "segmento"})
x["segmento"] = x["segmento"].astype(str)
x.insert(1, "dimension", "estacion")
nosup.append(x[["examen", "dimension", "segmento", "filas", "MAE_B0", "MAE_LGBM", "MAE_LGBM_sin_ub"]])
nosup = pd.concat(nosup, ignore_index=True)[["examen", "dimension", "segmento", "filas", "estaciones", "MAE_B0", "MAE_LGBM", "MAE_LGBM_sin_ub"]]

pe = por_est[por_est.examen == EX_ESP].copy()
for c in ("LGBM", "LGBM_sin_ub"):
    pe["mej_" + c] = 1 - pe["MAE_" + c] / pe["MAE_B0"]
pe["banda"] = pd.cut(pe["km_al_sensor_de_entrenamiento_mas_cercano"], [0, 10, 25, 50, 1e4], labels=["menos de 10 km", "10 a 25 km", "25 a 50 km", "mas de 50 km"])
dist_tab = pe.groupby("banda", observed=True).agg(estaciones=("CodigoEstacion", "size"), filas=("filas", "sum"), mejora_media_LGBM=("mej_LGBM", "mean"),
                                                 mejora_media_LGBM_sin_ub=("mej_LGBM_sin_ub", "mean")).reset_index()
rho = {c: float(pe["km_al_sensor_de_entrenamiento_mas_cercano"].corr(pe["mej_" + c], method="spearman")) for c in ("LGBM", "LGBM_sin_ub")}
n_comp = int(pe["comparte_celda_NASA_con_entrenamiento"].str.startswith("Sí").sum())
log(f"estaciones nuevas que comparten celda NASA con entrenamiento: {n_comp} de {len(pe)}")

# =====================================================================
# 7. VALIDACIÓN CRUZADA DE R2: 5 pliegues por Sitio (y por celda NASA). Cada sitio se predice SIN haber sido visto.
#    Es la base de la entrega a R3 (mejora por sitio para TODOS los sitios, fuera de muestra).
# =====================================================================
log("CV: construyendo variables de toda la tabla...")
fa = build_features(sub(df)).reset_index(drop=True)
y_res = (fa["Valor"] - fa["ALLSKY_SFC_SW_DWN"]).astype("float32").values
base_f, val_f, anio_f = fa["ALLSKY_SFC_SW_DWN"].values, fa["Valor"].values, fa["year"].values
est_f, sit_f = fa["CodigoEstacion"].values, fa["Sitio"].values
ym_f = fa["ts"].dt.to_period("M").astype(str).values
cel_f = pd.Series(est_f).map(celda).values
sit_est = sitio.reindex(todas).values
_, lab_cmb = connected_components(csr_matrix(mismo | mismo.T | (sit_est[:, None] == sit_est[None, :])))
cmb_f = pd.Series(est_f).map(pd.Series(lab_cmb, index=todas)).values       # grupo = celda NASA unida con Sitio
VARIANTES = {"LGBM": FEAT_NUEVO}
if CV_CON_SIN_UBICACION:
    VARIANTES["LGBM_sin_ub"] = FEAT_LGB_SIN
NOMBRES_CV = {"LGBM": "LightGBM (con ubicación)", "LGBM_sin_ub": "LightGBM sin ubicación"}
X32 = {v: fa[f].astype("float32") for v, f in VARIANTES.items()}


def cv_por_grupo(grupo_f, exige_celda, con_fracciones):
    """Reparte los grupos en CV_K pliegues (equilibrados por altitud), entrena con los otros pliegues (años <= ANIO_CORTE)
    y predice el pliegue completo. Devuelve predicciones, pliegue de cada fila, prueba de no fuga y conteos de la curva."""
    tg = pd.DataFrame({"g": grupo_f, "Altitud": fa["Altitud"].values}).groupby("g")["Altitud"].mean().sort_values().reset_index()
    rg = np.random.default_rng(SEMILLA)
    pl = np.zeros(len(tg), int)
    for i in range(0, len(tg), CV_K):
        n_i = min(CV_K, len(tg) - i)
        pl[i:i + n_i] = rg.permutation(CV_K)[:n_i]
    fila_pl = pd.Series(grupo_f).map(dict(zip(tg["g"], pl))).values
    pred = {v: np.full(len(fa), np.nan) for v in VARIANTES}
    pred_fr = {fr: np.full(len(fa), np.nan) for fr in FRACCIONES} if con_fracciones else {}
    n_sit = {fr: [] for fr in tuple(FRACCIONES) + (1.0,)}
    fugas = []
    for k in range(CV_K):
        te = fila_pl == k
        tr_k = (~te) & (anio_f <= ANIO_CORTE)
        for v in VARIANTES:
            m = lgbm_nuevo().fit(X32[v][tr_k], y_res[tr_k])
            pred[v][te] = np.clip(base_f[te] + m.predict(X32[v][te]), 0, None)
        if con_fracciones:
            sit_tr = np.unique(sit_f[tr_k])
            n_sit[1.0].append(len(sit_tr))
            for fr in FRACCIONES:
                ele = np.random.default_rng(SEMILLA + k).choice(sit_tr, size=max(1, round(len(sit_tr) * fr)), replace=False)
                n_sit[fr].append(len(ele))
                mk = tr_k & np.isin(sit_f, ele)
                m = lgbm_nuevo().fit(X32["LGBM"][mk], y_res[mk])
                pred_fr[fr][te] = np.clip(base_f[te] + m.predict(X32["LGBM"][te]), 0, None)
        f = {"pliegue": k + 1, "filas_entrenamiento": int(tr_k.sum()), "filas_prueba": int(te.sum()),
             "estaciones_entrenamiento": len(np.unique(est_f[tr_k])), "estaciones_prueba": len(np.unique(est_f[te])),
             "estaciones_en_ambos": len(np.intersect1d(est_f[tr_k], est_f[te])), "sitios_en_ambos": len(np.intersect1d(sit_f[tr_k], sit_f[te])),
             "celdas_NASA_en_ambos": len(np.intersect1d(cel_f[tr_k], cel_f[te]))}
        claves = ["estaciones_en_ambos", "sitios_en_ambos"] + (["celdas_NASA_en_ambos"] if exige_celda else [])
        f["veredicto"] = "OK: sin fuga" if sum(f[c] for c in claves) == 0 else "FUGA"
        fugas.append(f)
        log(f"   pliegue {k + 1}/{CV_K} listo ({f['veredicto']})")
    return pred, pred_fr, fila_pl, pd.DataFrame(fugas), n_sit


def boot_mejora(S0, S1, B=REMUESTREOS):
    """Mejora (1 - error del modelo / error de la NASA) con IC 95 % remuestreando grupos completos."""
    idx = np.random.default_rng(SEMILLA).integers(0, len(S0), size=(B, len(S0)))
    mej = 1 - S1[idx].sum(axis=1) / S0[idx].sum(axis=1)
    lo, hi = np.percentile(mej, [2.5, 97.5])
    return float(1 - S1.sum() / S0.sum()), float(lo), float(hi), float((mej > 0).mean() * 100)


def metricas_cv(mask, p, grupo):
    """Métricas de un examen (hora y mes) con intervalos de confianza por grupos completos."""
    y, pm = val_f[mask], p[mask]
    t = pd.DataFrame({"g": grupo[mask], "e0": np.abs(base_f[mask] - y), "e1": np.abs(pm - y)}).groupby("g")
    S0, S1 = t["e0"].sum().values, t["e1"].sum().values
    mh = boot_mejora(S0, S1)
    q = pd.DataFrame({"g": grupo[mask], "est": est_f[mask], "ym": ym_f[mask], "y": y, "b": base_f[mask], "p": pm}).groupby(["g", "est", "ym"]).mean()
    q["e0"], q["e1"] = (q["b"] - q["y"]).abs(), (q["p"] - q["y"]).abs()
    q = q.groupby("g")[["e0", "e1"]].sum()
    mm = boot_mejora(q["e0"].values, q["e1"].values)
    nmes = len(pd.DataFrame({"est": est_f[mask], "ym": ym_f[mask]}).drop_duplicates())
    e = y - pm
    return {"filas": int(mask.sum()), "estaciones": len(np.unique(est_f[mask])), "sitios": len(np.unique(sit_f[mask])),
            "MAE_hora_NASA": np.abs(y - base_f[mask]).mean(), "MAE_hora_modelo": np.abs(e).mean(), "RMSE_modelo": np.sqrt((e ** 2).mean()),
            "MBE_modelo": (pm - y).mean(), "mejora_hora": mh[0], "IC95_hora_inf": mh[1], "IC95_hora_sup": mh[2],
            "MAE_mes_NASA": q["e0"].sum() / nmes, "MAE_mes_modelo": q["e1"].sum() / nmes,
            "mejora_mes": mm[0], "IC95_mes_inf": mm[1], "IC95_mes_sup": mm[2], "pct_remuestreos_mes_a_favor": mm[3]}


todos, futuro = np.ones(len(fa), bool), anio_f > ANIO_CORTE
EX = {"Espacial (todos los años)": todos, f"Espacio-temporal (años > {ANIO_CORTE})": futuro}
cv_res, cv_plg, cv_fuga, cv_curva = [], [], [], None
pred_sitio = None
esquemas = [("Pliegues por Sitio", sit_f, False)] + ([("Pliegues por celda NASA (+Sitio)", cmb_f, True)] if CV_ESQUEMA_CELDA else [])
guardado = {}
for esquema, grupo_f, exige_cel in esquemas:
    log(f"CV: {esquema}...")
    con_fr = CV_CURVA and esquema == "Pliegues por Sitio"
    pred, pred_fr, fila_pl, fg, n_sit = cv_por_grupo(grupo_f, exige_cel, con_fr)
    guardado[esquema] = (pred, fila_pl)
    fg.insert(0, "esquema", esquema)
    cv_fuga.append(fg)
    for en, mk in EX.items():
        for v in VARIANTES:
            cv_res.append({"esquema": esquema, "examen": en, "modelo": NOMBRES_CV[v], **metricas_cv(mk, pred[v], grupo_f)})
    for k in range(CV_K):
        te = fila_pl == k
        fila = {"esquema": esquema, "pliegue": k + 1, "estaciones": len(np.unique(est_f[te])), "sitios": len(np.unique(sit_f[te])), "filas": int(te.sum())}
        for v in VARIANTES:
            e0, e1 = np.abs(base_f[te] - val_f[te]).mean(), np.abs(pred[v][te] - val_f[te]).mean()
            fila["MAE_NASA"], fila["MAE_" + v], fila["mejora_hora_" + v] = e0, e1, 1 - e1 / e0
        cv_plg.append(fila)
    if con_fr:
        filas_c = []
        for fr in tuple(FRACCIONES) + (1.0,):
            p_ = pred["LGBM"] if fr == 1.0 else pred_fr[fr]
            m_ = metricas_cv(todos, p_, grupo_f)
            filas_c.append({"fraccion_de_Sitios_de_entrenamiento": f"{int(round(fr * 100))} %", "sitios_de_entrenamiento_por_pliegue": float(np.mean(n_sit[fr])),
                            **{k_: m_[k_] for k_ in ("filas", "MAE_hora_NASA", "MAE_hora_modelo", "RMSE_modelo", "mejora_hora", "IC95_hora_inf", "IC95_hora_sup",
                                                    "mejora_mes", "IC95_mes_inf", "IC95_mes_sup")}})
        cv_curva = pd.DataFrame(filas_c)
cv_res, cv_plg, cv_fuga = pd.DataFrame(cv_res), pd.DataFrame(cv_plg), pd.concat(cv_fuga, ignore_index=True)
log("validación cruzada lista")

# =====================================================================
# 8. ROBUSTEZ: A/B DE PRELIMINARES (regla D0: se conservan si NO empeoran el error más de 2 %)
# =====================================================================
ab = None
if HACER_AB_PRELIMINARES:
    log("A/B preliminares...")
    pre = crudo[crudo["EsPreliminar"] & ~crudo["CodigoEstacion"].isin(hold)].copy()
    for c in ["Valor", "ALLSKY_SFC_SW_DWN"]:
        pre = pre[(pre[c] >= 0) & (pre[c] <= 1500)]
    pre["ts"] = pd.to_datetime(pre["Fecha"])
    pre["year"] = pre["ts"].dt.year
    pre = pre[pre["year"] < 2021].drop_duplicates(["CodigoEstacion", "ts"])
    clave_def = pd.MultiIndex.from_frame(split["train"][["CodigoEstacion", "ts"]])
    pre = pre[~pd.MultiIndex.from_frame(pre[["CodigoEstacion", "ts"]]).isin(clave_def)]      # solo preliminares que no repiten una hora definitiva
    fpre = sub(build_features(pre))
    f_con = pd.concat([ftr, fpre], ignore_index=True)
    m_con = lgbm_nuevo().fit(f_con[FEAT_NUEVO].astype("float32"), (f_con["Valor"] - f_con["ALLSKY_SFC_SW_DWN"]).astype("float32"))
    m_def = lgbm_nuevo().fit(ftr[FEAT_NUEVO].astype("float32"), residuo)
    filas_ab = []
    for n, d in ev.items():
        d = d.copy()
        base = d["ALLSKY_SFC_SW_DWN"].values
        d["p_def"] = np.clip(base + m_def.predict(d[FEAT_NUEVO].astype("float32")), 0, None)
        d["p_con"] = np.clip(base + m_con.predict(d[FEAT_NUEVO].astype("float32")), 0, None)
        nd, nc = niveles(d, "p_def"), niveles(d, "p_con")
        filas_ab.append({"examen": EX_ESP if n == "espacial" else EX_TEM, "filas_entrenamiento_solo_definitivos": len(ftr),
                         "filas_entrenamiento_con_preliminares": len(f_con), "preliminares_agregados": len(fpre),
                         "MAE_hora_solo_definitivos": nd["hora"]["MAE"], "MAE_hora_con_preliminares": nc["hora"]["MAE"],
                         "MAE_mes_solo_definitivos": nd["mes"]["MAE"], "MAE_mes_con_preliminares": nc["mes"]["MAE"]})
    ab = pd.DataFrame(filas_ab)
    del f_con, fpre
del ftr, residuo

# =====================================================================
# 9. D19: EMBUDO DE ESTACIONES
# =====================================================================
log("D19: embudo de estaciones...")
raw = crudo[["CodigoEstacion", "Fecha", "Valor", "ALLSKY_SFC_SW_DWN", "EsPreliminar", "Latitud", "Longitud"]].copy()
raw["ts"] = pd.to_datetime(raw["Fecha"])
t1 = raw[~raw["EsPreliminar"]]
t2 = t1[(t1["Valor"] >= 0) & (t1["Valor"] <= 1500)]
t3 = t2[(t2["ALLSKY_SFC_SW_DWN"] >= 0) & (t2["ALLSKY_SFC_SW_DWN"] <= 1500)]
t4 = t3.drop_duplicates(["CodigoEstacion", "ts"])
t5 = df[(df["CLRSKY_SFC_SW_DWN"] > 0) & (df["ALLSKY_SFC_SW_DWN"] > 0)]
pasos = [("0. Tabla original (sin filtrar)", raw), ("1. Sin filas preliminares", t1), ("2. Valor medido entre 0 y 1500", t2),
         ("3. Radiación NASA entre 0 y 1500", t3), ("4. Sin duplicados estación-hora", t4), ("5. Con radiación NASA > 0 (horas de sol aprox.)", t5)]
emb, prev = [], None
for nombre, t in pasos:
    e_ = set(t["CodigoEstacion"].unique())
    emb.append({"paso": nombre, "filas": len(t), "estaciones": len(e_), "filas_perdidas": (emb[-1]["filas"] - len(t)) if emb else 0,
                "estaciones_perdidas": len(prev - e_) if prev is not None else 0,
                "codigos_de_estaciones_que_desaparecen": ", ".join(str(int(c)) for c in sorted(prev - e_)) if prev is not None else ""})
    prev = e_
sin_coord = raw.groupby("CodigoEstacion")[["Latitud", "Longitud"]].first().isna().any(axis=1)
emb.append({"paso": "Informativo: estaciones sin coordenadas en la tabla original (D11)", "filas": None, "estaciones": int(sin_coord.sum()), "filas_perdidas": None,
            "estaciones_perdidas": None, "codigos_de_estaciones_que_desaparecen": ", ".join(str(int(c)) for c in sin_coord[sin_coord].index)})
cv_emb = pd.DataFrame(emb)

# =====================================================================
# 10. ENTREGA PARA R3: mejora por sitio (todos los sitios, fuera de muestra), casos (D16) y serie horaria
# =====================================================================
pred_s, fila_pl_s = guardado["Pliegues por Sitio"]


def tabla_sitio(pred, fila_pl):
    """Una fila por Sitio con TODAS sus horas predichas por modelos que no vieron ese Sitio (errores por hora y por mes)."""
    t = pd.DataFrame({"Sitio": sit_f, "est": est_f, "year": anio_f, "ym": ym_f, "pliegue": fila_pl + 1, "Valor": val_f, "NASA": base_f, "LGBM": pred["LGBM"]})
    mods = ["NASA", "LGBM"]
    if "LGBM_sin_ub" in pred:
        t["LGBM_sin_ub"] = pred["LGBM_sin_ub"]
        mods.append("LGBM_sin_ub")
    for k in mods:
        t["e_" + k] = (t[k] - t["Valor"]).abs()
    out = t.groupby("Sitio").agg(pliegue=("pliegue", "first"), filas=("Valor", "size"), anio_desde=("year", "min"), anio_hasta=("year", "max"),
                                 radiacion_media_IDEAM=("Valor", "mean"), radiacion_media_NASA=("NASA", "mean"), radiacion_media_LGBM=("LGBM", "mean"),
                                 **{"MAE_hora_" + k: ("e_" + k, "mean") for k in mods})
    out["sesgo_pct_NASA"] = 100 * (out["radiacion_media_NASA"] / out["radiacion_media_IDEAM"] - 1)
    out["sesgo_pct_LGBM"] = 100 * (out["radiacion_media_LGBM"] / out["radiacion_media_IDEAM"] - 1)
    m = t.groupby(["Sitio", "est", "ym"]).agg(y=("Valor", "mean"), **{k: (k, "mean") for k in mods})
    for k in mods:
        m["em_" + k] = (m[k] - m["y"]).abs()
    out = out.join(m.groupby("Sitio")[["em_" + k for k in mods]].mean().rename(columns=lambda c: c.replace("em_", "MAE_mes_")))
    return out.reset_index()


r3 = tabla_sitio(pred_s, fila_pl_s)
mods_r3 = ["LGBM"] + (["LGBM_sin_ub"] if CV_CON_SIN_UBICACION else [])
if CV_ESQUEMA_CELDA:      # mejora con el esquema más exigente (sin celda NASA compartida): mide cuánto depende el resultado de los vecinos de la misma celda
    tc = tabla_sitio(*guardado["Pliegues por celda NASA (+Sitio)"])
    tc["mejora_mes_LGBM_esquema_celda"] = 1 - tc["MAE_mes_LGBM"] / tc["MAE_mes_NASA"]
    r3 = r3.merge(tc[["Sitio", "mejora_mes_LGBM_esquema_celda"]], on="Sitio", how="left")
else:
    r3["mejora_mes_LGBM_esquema_celda"] = np.nan
# datos del sitio: estaciones, departamento, coordenadas
ed = df.groupby("CodigoEstacion").agg(Departamento=("Departamento", "first"), Latitud=("Latitud", "first"), Longitud=("Longitud", "first"), Altitud=("Altitud", "first"), Sitio=("Sitio", "first"))
ed["Nombre"] = nombres_est
sd = ed.groupby("Sitio").agg(Estaciones=("Nombre", lambda s: " + ".join(sorted(s))), Departamento=("Departamento", "first"), Latitud=("Latitud", "mean"),
                             Longitud=("Longitud", "mean"), Altitud=("Altitud", "mean"))
# estación principal del sitio (la de más filas) y su año con más horas medidas
cnt = pd.DataFrame({"Sitio": sit_f, "est": est_f, "year": anio_f}).groupby(["Sitio", "est", "year"]).size().rename("n").reset_index()
tot = cnt.groupby(["Sitio", "est"])["n"].sum().reset_index()
princ = tot.loc[tot.groupby("Sitio")["n"].idxmax()].set_index("Sitio")["est"]
cp = cnt.merge(princ.rename("est_ppal"), left_on="Sitio", right_index=True)
cp = cp[cp["est"] == cp["est_ppal"]]
mejor = cp.loc[cp.groupby("Sitio")["n"].idxmax()].set_index("Sitio")


def horas_con_sol(codigo, anio):
    """Horas del año con el sol sobre el horizonte en esa estación. La etiqueta de la tabla marca el FINAL de la hora; el punto medio es etiqueta - 30 min."""
    etiquetas = pd.date_range(f"{anio}-01-01 01:00", f"{anio + 1}-01-01 00:00", freq="h")
    t = pd.DatetimeIndex((etiquetas - pd.Timedelta(minutes=30)).tz_localize("America/Bogota").tz_convert("UTC"))
    lat, lon = coord.loc[codigo, ["Latitud", "Longitud"]]
    return int((solarposition.get_solarposition(t, lat, lon, altitude=float(alt_est[codigo]))["apparent_elevation"] > 0).sum())


r3 = r3.merge(sd.reset_index(), on="Sitio", how="left")
r3["CodigoEstacion_principal"] = r3["Sitio"].map(princ).astype(int)
r3["anio_mas_datos"] = r3["Sitio"].map(mejor["year"]).astype(int)
r3["filas_ese_anio"] = r3["Sitio"].map(mejor["n"]).astype(int)
r3["horas_con_sol_ese_anio"] = [horas_con_sol(e, a) for e, a in zip(r3["CodigoEstacion_principal"], r3["anio_mas_datos"])]
r3["anios_con_datos"] = r3["anio_desde"].astype(str) + "-" + r3["anio_hasta"].astype(str)
r3["otras_estaciones_en_la_misma_celda_NASA"] = [int(celdas[celda[e]]) - 1 for e in r3["CodigoEstacion_principal"]]
r3["Sitio"] = r3["Sitio"].astype(int)
# regla D16 (casos): mayor, mediana y menor mejora entre sitios con datos suficientes, con la métrica y el modelo elegidos
mc = f"MAE_{METRICA_CASOS}"
r3["mejora_para_regla_D16"] = 1 - r3[f"{mc}_{MODELO_R3}"] / r3[f"{mc}_NASA"]
suf = (r3["filas"] >= MIN_FILAS_SITIO) & (r3["filas_ese_anio"] >= MIN_FILAS_ANIO)
el = r3[suf]
r3["caso_sugerido"] = ""
casos = []
if len(el) >= 3:
    med = el["mejora_para_regla_D16"].median()
    casos = [("Mayor mejora", int(el.loc[el["mejora_para_regla_D16"].idxmax(), "Sitio"])),
             ("Mediana", int(el.loc[(el["mejora_para_regla_D16"] - med).abs().idxmin(), "Sitio"])),
             ("Menor mejora", int(el.loc[el["mejora_para_regla_D16"].idxmin(), "Sitio"]))]
else:
    print("Aviso: hay menos de 3 sitios con datos suficientes; baja MIN_FILAS_SITIO / MIN_FILAS_ANIO.")
if VILLETA_CODIGO in sitio.index:
    casos.append(("Villeta (narrativo)", int(sitio[VILLETA_CODIGO])))
else:
    print("Aviso: no encuentro la estación de Villeta; no se agrega como caso.")
for caso, s_ in casos:
    ya = r3.loc[r3["Sitio"] == s_, "caso_sugerido"].iloc[0]
    r3.loc[r3["Sitio"] == s_, "caso_sugerido"] = (ya + " + " if ya else "") + caso
cols_r3 = ["Sitio", "Estaciones", "Departamento", "Latitud", "Longitud", "Altitud", "CodigoEstacion_principal", "pliegue", "anios_con_datos", "filas",
           "anio_mas_datos", "filas_ese_anio", "horas_con_sol_ese_anio", "MAE_hora_NASA", "MAE_hora_LGBM"] + (["MAE_hora_LGBM_sin_ub"] if CV_CON_SIN_UBICACION else []) + \
          ["MAE_mes_NASA", "MAE_mes_LGBM"] + (["MAE_mes_LGBM_sin_ub"] if CV_CON_SIN_UBICACION else []) + \
          ["sesgo_pct_NASA", "sesgo_pct_LGBM", "radiacion_media_IDEAM", "radiacion_media_NASA", "radiacion_media_LGBM", "mejora_mes_LGBM_esquema_celda",
           "otras_estaciones_en_la_misma_celda_NASA", "caso_sugerido"]
r3 = r3[cols_r3].sort_values("Sitio").reset_index(drop=True)
r3_casos = pd.DataFrame(casos, columns=["caso", "Sitio"])
# serie horaria de cada caso: las tres fuentes lado a lado (IDEAM, NASA y modelo), fuera de muestra
series = []
for caso, s_ in casos:
    e_p = int(princ[s_])
    anios = cp[cp["Sitio"] == s_].sort_values("n", ascending=False)["year"].head(ANIOS_POR_CASO).values
    mk = (est_f == e_p) & np.isin(anio_f, anios)
    d = fa.loc[mk].sort_values("ts")
    ix = d.index.values
    s_df = pd.DataFrame({"caso": caso, "Sitio": s_, "CodigoEstacion": e_p, "Estacion": nombres_est.get(e_p, str(e_p)),
                         "Fecha_etiqueta_hora_local": d["ts"].values, "Fecha_punto_medio": (d["ts"] - pd.Timedelta(minutes=30)).values, "Hora": d["Hora"].values,
                         "IDEAM_medicion": d["Valor"].values, "NASA_sin_corregir": d["ALLSKY_SFC_SW_DWN"].values,
                         "Modelo_principal": pred_s[MODELO_R3][ix], "Modelo_LGBM": pred_s["LGBM"][ix]})
    if CV_CON_SIN_UBICACION:
        s_df["Modelo_LGBM_sin_ubicacion"] = pred_s["LGBM_sin_ub"][ix]
    s_df["T2M_NASA_temperatura_aire"] = d["T2M"].values
    s_df["CLRSKY_NASA_cielo_despejado"] = d["CLRSKY_SFC_SW_DWN"].values
    s_df["ALLSKY_KT_NASA"] = d["ALLSKY_KT"].values
    series.append(s_df)
serie_r3 = pd.concat(series, ignore_index=True) if series else pd.DataFrame()
del X32, fa
log(f"entrega para R3 lista: {len(r3)} sitios, {len(casos)} casos, {len(serie_r3):,} horas de serie")

# =====================================================================
# 11. EXCEL (primero las hojas para R3, luego la evaluación del modelo)
# =====================================================================
wb = Workbook()
wb.remove(wb.active)
escribir = escribir_hoja

# ---- R3_mejora_por_sitio ----
f_sitio = [("cobertura_ese_anio", "=MIN(1,{filas_ese_anio}{r}/{horas_con_sol_ese_anio}{r})"),
           ("mejora_hora_LGBM", "=1-{MAE_hora_LGBM}{r}/{MAE_hora_NASA}{r}"), ("mejora_mes_LGBM", "=1-{MAE_mes_LGBM}{r}/{MAE_mes_NASA}{r}")]
if CV_CON_SIN_UBICACION:
    f_sitio += [("mejora_hora_LGBM_sin_ub", "=1-{MAE_hora_LGBM_sin_ub}{r}/{MAE_hora_NASA}{r}"), ("mejora_mes_LGBM_sin_ub", "=1-{MAE_mes_LGBM_sin_ub}{r}/{MAE_mes_NASA}{r}")]
f_sitio += [("mejora_para_regla_D16", f"=1-{{{mc}_{MODELO_R3}}}{{r}}/{{{mc}_NASA}}{{r}}"),
            ("datos_suficientes", '=IF(AND({filas}{r}>=15000,{filas_ese_anio}{r}>=3500),"Sí","No")')]
cols_s = list(r3.columns) + [n for n, _ in f_sitio]
ls = {c: L(i + 1) for i, c in enumerate(cols_s)}
ult = len(r3) + 1
rng_s = lambda col: f"R3_mejora_por_sitio!${ls[col]}$2:${ls[col]}${ult}"


def buscar(col):
    """Trae un dato de R3_mejora_por_sitio buscando el Sitio del caso (columna B de R3_casos)."""
    return "=INDEX(R3_mejora_por_sitio!$%s$2:$%s$%d,MATCH($B{r},R3_mejora_por_sitio!$A$2:$A$%d,0))" % (ls[col], ls[col], ult, ult)


f_casos = [(c, buscar(c)) for c in ("Estaciones", "Departamento", "Latitud", "Longitud", "Altitud", "CodigoEstacion_principal", "anio_mas_datos", "filas_ese_anio",
                                    "cobertura_ese_anio", "mejora_hora_LGBM", "mejora_mes_LGBM", "sesgo_pct_NASA", "sesgo_pct_LGBM", "mejora_mes_LGBM_esquema_celda",
                                    "mejora_para_regla_D16", "datos_suficientes")]
escribir(wb, "R3_casos", r3_casos, formulas=f_casos,
         pct=("cobertura_ese_anio", "mejora_hora_LGBM", "mejora_mes_LGBM", "mejora_mes_LGBM_esquema_celda", "mejora_para_regla_D16"),
         dec1=("Altitud", "sesgo_pct_NASA", "sesgo_pct_LGBM"), dec4=("Latitud", "Longitud"), ancho=15,
         nota="Casos seleccionados para R3: mayor mejora, mediana, menor mejora y Villeta. Las columnas calculadas consultan la hoja de resultados por sitio.")
pct_s = tuple(n for n, _ in f_sitio if n.startswith("mejora") or n.startswith("cobertura")) + ("mejora_mes_LGBM_esquema_celda",)
escribir(wb, "R3_mejora_por_sitio", r3, formulas=f_sitio, pct=pct_s,
         dec1=tuple(c for c in r3.columns if c.startswith("MAE") or c.startswith("radiacion") or c in ("Altitud", "sesgo_pct_NASA", "sesgo_pct_LGBM")),
         dec4=("Latitud", "Longitud"), ancho=14,
         nota="Evaluación fuera de muestra de todos los sitios. Incluye errores, sesgos, mejoras frente a NASA y disponibilidad de datos para seleccionar casos.")
if len(serie_r3):
    dec_serie = tuple(c for c in serie_r3.columns if c.startswith(("IDEAM", "NASA", "Modelo", "T2M", "CLRSKY")))
    escribir(wb, "R3_serie_horaria", serie_r3, dec1=dec_serie, dec4=("ALLSKY_KT_NASA",), fechas=("Fecha_etiqueta_hora_local", "Fecha_punto_medio"), ancho=15,
             nota="Serie horaria de los casos seleccionados: medición IDEAM, NASA sin corregir y radiación corregida por el modelo, junto con variables auxiliares.")

# ---- Evaluación del modelo ----
escribir(wb, "resumen_modelos", resumen,
         formulas=[("mejora_hora_vs_NASA", "=1-{MAE_hora}{r}/{MAE_hora_B0}{r}"), ("mejora_hora_vs_recta", "=1-{MAE_hora}{r}/{MAE_hora_B1}{r}"),
                   ("mejora_dia_vs_NASA", "=1-{MAE_dia}{r}/{MAE_dia_B0}{r}"), ("mejora_mes_vs_NASA", "=1-{MAE_mes}{r}/{MAE_mes_B0}{r}"),
                   ("mejora_mes_vs_recta", "=1-{MAE_mes}{r}/{MAE_mes_B1}{r}")],
         pct=("mejora_hora_vs_NASA", "mejora_hora_vs_recta", "mejora_dia_vs_NASA", "mejora_mes_vs_NASA", "mejora_mes_vs_recta"),
         dec1=("MAE_hora", "MAE_hora_B0", "MAE_hora_B1", "RMSE_hora", "MAE_dia", "MAE_dia_B0", "MAE_mes", "MAE_mes_B0", "MAE_mes_B1", "sesgo_pct"), signo=("MBE_hora",), ancho=15,
         nota="Comparación principal de NASA sin corregir, corrección lineal y LightGBM en los exámenes temporal y espacial. Menor MAE/RMSE es mejor; mejora positiva favorece al modelo.")
escribir(wb, "intervalos_confianza", ic, ancho=18,
         nota="Incertidumbre de los errores y de las mejoras, calculada mediante remuestreo de sitios completos. El IC 95 % de una mejora que incluye cero no confirma una mejora.")
pct_cv = ("mejora_hora", "IC95_hora_inf", "IC95_hora_sup", "mejora_mes", "IC95_mes_inf", "IC95_mes_sup")
escribir(wb, "cv_resumen", cv_res, pct=pct_cv,
         dec1=("MAE_hora_NASA", "MAE_hora_modelo", "RMSE_modelo", "MAE_mes_NASA", "MAE_mes_modelo", "pct_remuestreos_mes_a_favor"),
         signo=("MBE_modelo",), ancho=15,
         nota="Validación cruzada agrupada: cada sitio se predice sin haber sido usado para entrenar. Resume el desempeño espacial y espacio-temporal; la mejora mensual es la métrica principal de negocio.")
escribir(wb, "cv_no_fuga", cv_fuga, ancho=16,
         nota="Control de separación entre entrenamiento y prueba. Comprueba si estaciones, sitios o celdas NASA se repiten entre ambos conjuntos; el veredicto esperado es OK: sin fuga.")

wb.save(args.salida)
log("terminado: " + args.salida)

# =====================================================================
# RESUMEN EN PANTALLA
# =====================================================================
print("\n" + "=" * 78 + "\nRESUMEN (MAE por hora en Wh/m2; mejora frente a la NASA sin corregir)\n" + "=" * 78)
for ex in resumen["examen"].unique():
    print("\n" + ex)
    for _, x in resumen[resumen["examen"] == ex].iterrows():
        marca = "   <-- NO válido como examen" if x["valido_como_examen"] == "NO" else ""
        print(f"  {x['modelo']:<42} MAE {x['MAE_hora']:6.1f}  hora {100 * (1 - x['MAE_hora'] / x['MAE_hora_B0']):5.1f} %  mes {100 * (1 - x['MAE_mes'] / x['MAE_mes_B0']):5.1f} %{marca}")
print("\nVALIDACIÓN CRUZADA (mejora frente a la NASA):")
for _, x in cv_res.iterrows():
    print(f"  {x['esquema']:<33} {x['examen']:<30} {x['modelo']:<24} hora {100 * x['mejora_hora']:5.1f} %  mes {100 * x['mejora_mes']:5.1f} % (IC95 {100 * x['IC95_mes_inf']:5.1f} a {100 * x['IC95_mes_sup']:5.1f})")
print("No fuga:", cv_fuga.groupby("esquema")["veredicto"].agg(lambda s: "; ".join(sorted(set(s)))).to_dict())
if ab is not None:
    print("\nA/B preliminares (cambio del error al agregarlos):", {r_["examen"][:8]: (f"hora {100 * (r_['MAE_hora_con_preliminares'] / r_['MAE_hora_solo_definitivos'] - 1):+.1f} %", f"mes {100 * (r_['MAE_mes_con_preliminares'] / r_['MAE_mes_solo_definitivos'] - 1):+.1f} %") for _, r_ in ab.iterrows()})
print("\nCASOS PARA R3 (regla D16, métrica " + METRICA_CASOS + "):")
for caso, s_ in casos:
    x = r3[r3["Sitio"] == s_].iloc[0]
    print(f"  {caso:<22} Sitio {s_}  {x['Estaciones'][:34]:<34} {x['Departamento']:<14} año {x['anio_mas_datos']}  filas {x['filas_ese_anio']:,}  mejora regla {100 * x['mejora_para_regla_D16']:6.1f} %")
print("\nExcel guardado en: " + args.salida)
if sys.platform.startswith("win"):
    try:
        os.startfile(args.salida)
    except Exception:
        pass
