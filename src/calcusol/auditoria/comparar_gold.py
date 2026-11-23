"""
COMPARAR DOS VERSIONES DE GOLD — Calcusol (R5)

Compara la tabla del compañero (dataset_modelo.parquet) con gold v1 (gold_v1_unico.parquet):
  1. Tamaño de cada una (filas, estaciones, departamentos, fechas).
  2. Qué estaciones están en una y no en la otra, y cuántas filas aporta cada una.
  3. Si la del compañero tiene copias: estaciones en el mismo punto con el mismo valor a la misma hora.
  4. En las filas que están en las dos: si los datos del IDEAM y de la NASA coinciden, y una muestra.
  5. Resumen de por qué gold v1 tiene menos filas.

EJECUCIÓN:
  python -m calcusol.auditoria.comparar_gold --companero dataset_modelo.parquet --mio gold_v1_unico.parquet
"""

import sys
from pathlib import Path

import pandas as pd


LLAVE = ["CodigoEstacion", "Fecha"]
IDEAM = ["Valor", "EsPreliminar"]
NASA = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]
LUGAR = ["Latitud", "Longitud", "Altitud"]


def cargar(ruta, nombre):
    if not ruta.exists():
        sys.exit(f"No encuentro {ruta}")
    df = pd.read_parquet(ruta)
    faltan = [c for c in LLAVE if c not in df.columns]
    if faltan:
        sys.exit(f"{nombre} no tiene las columnas {faltan}. Columnas: {list(df.columns)}")
    df["CodigoEstacion"] = pd.to_numeric(df["CodigoEstacion"]).astype("int64")
    fecha = pd.to_datetime(df["Fecha"])
    if getattr(fecha.dt, "tz", None) is not None:      # sin zona horaria, sin mover la hora
        fecha = fecha.dt.tz_localize(None)
    df["Fecha"] = fecha
    return df


def resumen(df, nombre):
    print(f"  {nombre:<14} {len(df):>11,} filas | {df['CodigoEstacion'].nunique():>4} estaciones | "
          f"{df['Departamento'].nunique() if 'Departamento' in df else '?':>3} departamentos | "
          f"{df['Fecha'].min():%Y-%m-%d} a {df['Fecha'].max():%Y-%m-%d}")


def copias_exactas(df):
    """Filas de estaciones que repiten exactamente el Valor de otra estación del mismo punto a la misma hora."""
    est = df.groupby("CodigoEstacion")[["Latitud", "Longitud"]].first().round(3)
    punto = est.groupby(["Latitud", "Longitud"]).filter(lambda g: len(g) > 1)
    if punto.empty:
        return pd.DataFrame()
    grupos = punto.reset_index().merge(est.reset_index(), on=["Latitud", "Longitud"], suffixes=("", "_otra"))
    grupos = grupos[grupos["CodigoEstacion"] < grupos["CodigoEstacion_otra"]]
    filas = []
    for _, g in grupos.iterrows():
        a = df.loc[df["CodigoEstacion"] == g["CodigoEstacion"], ["Fecha", "Valor"]]
        b = df.loc[df["CodigoEstacion"] == g["CodigoEstacion_otra"], ["Fecha", "Valor"]]
        cruce = a.merge(b, on="Fecha", suffixes=("_a", "_b"))
        iguales = int(((cruce["Valor_a"] - cruce["Valor_b"]).abs() < 0.01).sum())
        filas.append({"estacion": int(g["CodigoEstacion"]), "copia_de": int(g["CodigoEstacion_otra"]),
                      "horas_en_comun": len(cruce), "valores_identicos": iguales,
                      "filas_estacion": len(a)})
    return pd.DataFrame(filas)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Compara dos versiones de gold (pandas)")
    p.add_argument("--companero", type=Path, default=Path("dataset_modelo.parquet"))
    p.add_argument("--mio", type=Path, default=Path("gold_v1_unico.parquet"))
    args = p.parse_args()
    COMPANERO, MIO = args.companero, args.mio
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    comp = cargar(COMPANERO, "dataset_modelo")
    mio = cargar(MIO, "gold_v1_unico")

    print("\n=== 1. Tamaño de cada tabla ===")
    resumen(comp, "compañero")
    resumen(mio, "gold v1 (mío)")
    print(f"  Diferencia: {len(comp) - len(mio):,} filas más en la del compañero")
    rep_c = int(comp.duplicated(LLAVE).sum())
    rep_m = int(mio.duplicated(LLAVE).sum())
    print(f"  Filas repetidas (misma estación y hora): compañero {rep_c:,} | mío {rep_m:,}")

    print("\n=== 2. Estaciones que están en una sola tabla ===")
    ec, em = set(comp["CodigoEstacion"]), set(mio["CodigoEstacion"])
    nombre = (comp.groupby("CodigoEstacion")["NombreEstacion"].first()
              if "NombreEstacion" in comp else pd.Series(dtype=str))
    solo_comp = sorted(ec - em)
    filas_solo_comp = 0
    print(f"  Solo en la del compañero: {len(solo_comp)}")
    for c in solo_comp:
        n = int((comp["CodigoEstacion"] == c).sum())
        filas_solo_comp += n
        print(f"     {c}  {str(nombre.get(c, ''))[:45]:<45} {n:>9,} filas")
    print(f"     Total: {filas_solo_comp:,} filas")
    solo_mio = sorted(em - ec)
    print(f"  Solo en gold v1: {len(solo_mio)} {solo_mio if solo_mio else ''}")

    print("\n=== 3. ¿La del compañero tiene estaciones copiadas? ===")
    if all(c in comp for c in ["Latitud", "Longitud", "Valor"]):
        copias = copias_exactas(comp)
        if copias.empty:
            print("  No hay estaciones en el mismo punto.")
        else:
            copias["% identicos"] = (100 * copias["valores_identicos"] / copias["horas_en_comun"]).round(1)
            print("  Parejas de estaciones en el mismo punto (coordenadas iguales a 3 decimales):")
            print(copias.to_string(index=False))
    else:
        print("  No se puede revisar: faltan Latitud, Longitud o Valor.")

    print("\n=== 4. Filas que están en las dos tablas ===")
    comunes = [c for c in IDEAM + NASA + LUGAR if c in comp.columns and c in mio.columns]
    cruce = comp[LLAVE + comunes].merge(mio[LLAVE + comunes], on=LLAVE, suffixes=("_comp", "_mio"))
    print(f"  Filas en común: {len(cruce):,}")
    print(f"  Filas solo en la del compañero: {len(comp) - len(cruce):,} | solo en gold v1: {len(mio) - len(cruce):,}")
    print("  Filas con valor distinto, por columna:")
    for c in comunes:
        a, b = cruce[f"{c}_comp"], cruce[f"{c}_mio"]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b) and c != "EsPreliminar":
            dif = ~(((a - b).abs() < 0.01) | (a.isna() & b.isna()))
        else:
            dif = ~((a.astype(str) == b.astype(str)))
        print(f"     {c:<20} {int(dif.sum()):>9,}")

    muestra = cruce.sample(min(10, len(cruce)), random_state=1).sort_values(LLAVE)
    cols = LLAVE + [f"{c}_{lado}" for c in ["Valor", "ALLSKY_SFC_SW_DWN", "T2M"] if c in comunes
                    for lado in ("comp", "mio")]
    print("\n  Muestra de 10 filas (IDEAM = Valor; NASA = ALLSKY y T2M):")
    print(muestra[cols].to_string(index=False))

    if len(comp) - len(cruce):
        print("\n  Filas solo en la del compañero, por estación (las 15 con más filas):")
        solo = comp.merge(mio[LLAVE], on=LLAVE, how="left", indicator=True)
        solo = solo[solo["_merge"] == "left_only"]
        por_est = solo.groupby("CodigoEstacion").size().sort_values(ascending=False)
        print(por_est.head(15).to_string())

    print("\n=== 5. Resumen ===")
    print(f"  La del compañero tiene {len(comp) - len(mio):,} filas más.")
    print(f"  De esas, {filas_solo_comp:,} son de las {len(solo_comp)} estaciones que gold v1 no tiene.")
    resto = (len(comp) - len(cruce)) - filas_solo_comp
    print(f"  Otras {resto:,} filas de la del compañero no están en gold v1 (ver la tabla de arriba).")


if __name__ == "__main__":
    main()
