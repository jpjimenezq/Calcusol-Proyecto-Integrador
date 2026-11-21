import json
import time
import urllib.error
import urllib.request

import boto3
from botocore.exceptions import ClientError

BUCKET = "calcusol-datos-eafit"
PREFIJO = "bronze/nasa"
URL_NASA = "https://power.larc.nasa.gov/api/temporal/hourly/point"
PARAMETROS = "ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN,ALLSKY_KT,T2M"
PAUSA_SEGUNDOS = 3
INTENTOS = 5

s3 = boto3.client("s3", region_name="us-east-1")


def ya_existe(clave):
    try:
        s3.head_object(Bucket=BUCKET, Key=clave)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def pedir_nasa(latitud, longitud, anio):
    url = (
        f"{URL_NASA}?parameters={PARAMETROS}&community=RE"
        f"&longitude={longitud}&latitude={latitud}"
        f"&start={anio}0101&end={anio}1231&time-standard=UTC&format=JSON"
    )
    for intento in range(1, INTENTOS + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as respuesta:
                return respuesta.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or intento == INTENTOS:
                raise
            motivo = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            if intento == INTENTOS:
                raise
            motivo = str(e)
        espera = 2 ** intento * 5
        print(f"Reintento {intento} para {anio} ({motivo}); espero {espera} s")
        time.sleep(espera)


def lambda_handler(event, context):
    latitud = round(float(event["latitud"]), 4)
    longitud = round(float(event["longitud"]), 4)
    descargadas, ya_estaban = 0, 0

    for anio in event["anios"]:
        clave = f"{PREFIJO}/{latitud:.4f}_{longitud:.4f}_{anio}_UTC.json"
        if ya_existe(clave):
            ya_estaban += 1
            continue
        datos = pedir_nasa(latitud, longitud, anio)
        s3.put_object(Bucket=BUCKET, Key=clave, Body=datos, ContentType="application/json")
        descargadas += 1
        time.sleep(PAUSA_SEGUNDOS)

    resultado = {"latitud": latitud, "longitud": longitud,
                 "descargadas": descargadas, "ya_estaban": ya_estaban}
    print(json.dumps(resultado))
    return resultado