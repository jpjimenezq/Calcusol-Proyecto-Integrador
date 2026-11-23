"""Datos REALES de Colombia para el dashboard, desde la tabla gold de Athena.

1. Pide a Athena (calcusol.gold_v1) las horas de sol (7 a 18) de todas las estaciones y las deja en S3 como parquet.
2. Las descarga y les aplica el modelo de R1 (LightGBM, el mismo que sirve la API en /predict).
3. Promedia por estación, mes y hora la radiación medida (IDEAM), la satelital (NASA), la corregida (ML) y la
   temperatura, y escribe dashboard/data/colombia.json (el mismo formato que build_data.py y build_demo_data.py).

El sesgo y el error del modelo que muestra el dashboard se miden solo con los años que el modelo no vio al entrenar
(2021 y 2022 por defecto: el modelo se entrenó con datos definitivos hasta 2020, según models/radiacion/v1/metadatos.json), no con los de entrenamiento.

Requiere credenciales de AWS con permiso de Athena y del bucket de datos (perfil calcusol-despliegue) y, además de
las dependencias del proyecto, boto3 y pyarrow. Desde la raíz del repositorio:

    python dashboard/pipeline/build_data_aws.py                       # incluye los datos preliminares del IDEAM
    python dashboard/pipeline/build_data_aws.py --solo-validados      # solo los validados (menos estaciones)
    python dashboard/pipeline/build_data_aws.py --validacion 2021 2022

Los datos marcados EsPreliminar son mediciones que el IDEAM aún no ha validado. Se incluyen para cubrir todo el país
(11 estaciones, entre ellas Villeta y la única de Chocó, solo tienen datos preliminares); cada estación guarda en
"preliminar" qué fracción de sus registros lo es y el dashboard lo muestra.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import formato

REPO = formato.ROOT.parent
sys.path.insert(0, str(REPO / "src"))

from calcusol.ml.predict import CorrectorRadiacion  # noqa: E402

MODELO = REPO / "models" / "radiacion" / "v1" / "modelo_lgbm.txt"
COLUMNAS = ["CodigoEstacion", "NombreEstacion", "Departamento", "Fecha", "Hora", "Mes", "DiaDelAnio", "Latitud",
            "Longitud", "Altitud", "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M", "Valor", "EsPreliminar",
            "Anio"]


def consulta(destino: str, preliminares: bool) -> str:
    filtro_prelim = "" if preliminares else "AND NOT EsPreliminar"
    return f"""
UNLOAD (
  SELECT CodigoEstacion, NombreEstacion, Departamento, Fecha, Hora, Mes, DiaDelAnio, Latitud, Longitud, Altitud,
         ALLSKY_SFC_SW_DWN, CLRSKY_SFC_SW_DWN, ALLSKY_KT, T2M, Valor, EsPreliminar, anio AS Anio
  FROM calcusol.gold_v1
  WHERE Valor IS NOT NULL {filtro_prelim}
) TO '{destino}' WITH (format = 'PARQUET', compression = 'SNAPPY')"""


def ejecutar_athena(sesion, sql: str, resultados: str, espera: int = 900) -> str:
    athena = sesion.client("athena")
    qid = athena.start_query_execution(QueryString=sql, WorkGroup="primary",
                                       ResultConfiguration={"OutputLocation": resultados})["QueryExecutionId"]
    print(f"Athena: consulta {qid}", flush=True)
    t0 = time.time()
    while time.time() - t0 < espera:
        q = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        estado = q["Status"]["State"]
        if estado == "SUCCEEDED":
            stats = q.get("Statistics", {})
            print(f"Athena: lista en {stats.get('TotalExecutionTimeInMillis', 0) / 1000:.0f} s, "
                  f"{stats.get('DataScannedInBytes', 0) / 1e6:.0f} MB leídos")
            return qid
        if estado in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"La consulta de Athena terminó en {estado}: {q['Status'].get('StateChangeReason', '')}")
        time.sleep(3)
    raise TimeoutError("Athena tardó más de lo esperado")


def descargar(sesion, bucket: str, prefijo: str, carpeta: Path) -> int:
    s3 = sesion.client("s3")
    n = 0
    for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefijo):
        for obj in pagina.get("Contents", []):
            if obj["Size"] > 0:
                s3.download_file(bucket, obj["Key"], str(carpeta / f"parte-{n:04d}.parquet"))
                n += 1
    return n


def cargar_modelo() -> CorrectorRadiacion:
    """LightGBM no lee el modelo si trae saltos de línea de Windows (\\r\\n): se usa una copia con \\n."""
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "modelo_lgbm.txt"
        copia.write_bytes(MODELO.read_bytes().replace(b"\r\n", b"\n"))
        return CorrectorRadiacion(str(copia))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--perfil", default="calcusol-despliegue", help="perfil de AWS (por defecto calcusol-despliegue)")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--bucket", default="calcusol-datos-eafit")
    ap.add_argument("--solo-validados", action="store_true",
                    help="excluye las filas marcadas EsPreliminar (por defecto se incluyen, y cada estación "
                         "guarda qué proporción de sus datos es preliminar)")
    ap.add_argument("--validacion", nargs=2, type=int, default=[2021, 2022], metavar=("DESDE", "HASTA"),
                    help="años con los que se mide el error del modelo (los que no vio al entrenar)")
    a = ap.parse_args()

    import boto3  # solo hace falta con AWS
    sesion = boto3.Session(profile_name=a.perfil, region_name=a.region)
    ahora = datetime.now().strftime("%Y%m%d%H%M%S")
    prefijo = f"athena/dashboard/{ahora}/"
    preliminares = not a.solo_validados
    ejecutar_athena(sesion, consulta(f"s3://{a.bucket}/{prefijo}", preliminares), f"s3://{a.bucket}/athena/")

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        n = descargar(sesion, a.bucket, prefijo, carpeta)
        print(f"Descargadas {n} partes de s3://{a.bucket}/{prefijo}", flush=True)
        df = pd.concat([pd.read_parquet(p) for p in sorted(carpeta.glob("*.parquet"))], ignore_index=True)
    canon = {c.lower(): c for c in COLUMNAS}
    df.columns = [canon.get(c.lower(), c) for c in df.columns]
    registros_tabla = len(df)                                          # total de la tabla gold: es la cifra que se muestra
    print(f"{registros_tabla:,} filas de {df['CodigoEstacion'].nunique()} estaciones; aplicando el modelo…", flush=True)

    # El cálculo usa las horas con sol: se descartan las de la madrugada y la noche (6 y 19 h casi no tienen) y,
    # dentro del modelo, las filas con el sol bajo el horizonte.
    df = df[df["Hora"].between(formato.HORAS[0], formato.HORAS[-1])]
    d = cargar_modelo().predecir(df)                                   # radiacion_corregida (W/m2), solo horas de sol
    d = d.rename(columns={"Valor": "IDEAM", "ALLSKY_SFC_SW_DWN": "NASA", "radiacion_corregida": "ML"})

    deptos = formato.departamentos()
    estaciones = formato.estaciones_desde(d, deptos, tuple(a.validacion))
    sin_mapa = sorted({e["departamento"] for e in estaciones} - {x["n"] for x in deptos})
    if sin_mapa:
        print(f"AVISO: departamentos sin contorno en el mapa (revise nombre_departamento): {sin_mapa}")

    v = d[d["Anio"].between(*a.validacion)]
    v = v if len(v) else d
    meta = {
        "registros": int(registros_tabla), "registros_usados": int(len(d)),
        "desde": int(d["Anio"].min()), "hasta": int(d["Anio"].max()),
        "sesgo_nasa": round(float(v["NASA"].sum() / v["IDEAM"].sum() - 1), 4),
        "rmse_nasa": formato.rmse(v["NASA"], v["IDEAM"]), "rmse_ml": formato.rmse(v["ML"], v["IDEAM"]),
        "modelo": "LightGBM (R1)", "fuente": "Athena calcusol.gold_v1",
        "validacion": f"{a.validacion[0]}–{a.validacion[1]}", "preliminares": preliminares,
    }
    salida = formato.armar_salida(estaciones, deptos, meta)
    ruta = formato.escribir(salida)
    m = salida["meta"]
    print(f"\n{ruta.relative_to(REPO)}: {m['estaciones']} estaciones en {m['departamentos']} departamentos, "
          f"{m['registros']:,} registros en la tabla ({m['registros_usados']:,} con sol, {m['desde']}-{m['hasta']}), "
          f"{ruta.stat().st_size / 1024:.0f} kB")
    print(f"Con los años {m['validacion']}: sesgo de NASA {m['sesgo_nasa']:+.1%}, RMSE NASA {m['rmse_nasa']} -> "
          f"modelo {m['rmse_ml']} W/m2")


if __name__ == "__main__":
    main()
