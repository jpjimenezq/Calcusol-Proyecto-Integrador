import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

BUCKET = "calcusol-datos-eafit"
PREFIJO = "bronze/nasa"
URL_NASA = "https://power.larc.nasa.gov/api/temporal/hourly/point"
VARIABLES = ["ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "ALLSKY_KT", "T2M"]
PAUSA_SEGUNDOS = 1.0          # PAUSA_S en bronze/nasa.py
TIMEOUT_SEGUNDOS = 300        # TIMEOUT_S en bronze/nasa.py
INTENTOS = 5                  # MAX_INTENTOS en bronze/nasa.py
CODIGOS_REINTENTO = {429, 500, 502, 503, 504}

s3 = boto3.client("s3", region_name="us-east-1")


def nombre_archivo(latitud, longitud, anio):
    return f"{latitud:.4f}_{longitud:.4f}_{anio}_UTC.json"


def clave_registro(codigo):
    return f"{PREFIJO}/_manifest/{codigo}.jsonl"


def leer_texto(clave):
    """Contenido de un objeto de S3 como texto, o None si no existe."""
    try:
        return s3.get_object(Bucket=BUCKET, Key=clave)["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise


def tamano_si_existe(clave):
    """Bytes del objeto de S3, o None si no existe."""
    try:
        return s3.head_object(Bucket=BUCKET, Key=clave)["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def pedir_nasa(latitud, longitud, anio):
    url = (
        f"{URL_NASA}?parameters={','.join(VARIABLES)}&community=RE"
        f"&latitude={latitud}&longitude={longitud}"
        f"&start={anio}0101&end={anio}1231&time-standard=UTC&format=JSON"
    )
    for intento in range(1, INTENTOS + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_SEGUNDOS) as respuesta:
                return respuesta.read()
        except urllib.error.HTTPError as e:
            if e.code not in CODIGOS_REINTENTO:
                raise RuntimeError(f"NASA respondió {e.code}")
            motivo = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            motivo = e.__class__.__name__
        if intento == INTENTOS:
            raise RuntimeError(f"NASA no respondió tras {INTENTOS} intentos ({motivo})")
        espera = 15 * intento
        print(f"Reintento {intento} para {anio} ({motivo}); espero {espera} s")
        time.sleep(espera)


def lambda_handler(event, context):
    codigo = str(int(event["codigo"]))
    latitud = round(float(event["latitud"]), 4)
    longitud = round(float(event["longitud"]), 4)

    previo = leer_texto(clave_registro(codigo)) or ""
    previas = [json.loads(linea) for linea in previo.splitlines() if linea.strip()]
    ok_previos = {e["archivo"] for e in previas if e.get("estado") == "ok"}

    nuevas = []
    descargadas, ya_estaban, fallidos = 0, 0, 0

    for anio in event["anios"]:
        archivo = nombre_archivo(latitud, longitud, anio)
        clave = f"{PREFIJO}/{archivo}"
        base = {
            "registrado_utc": datetime.now(timezone.utc).isoformat(),
            "fuente": "nasa", "archivo": archivo, "codigo_estacion": codigo,
            "latitud": latitud, "longitud": longitud, "anio": anio,
            "variables": VARIABLES, "time_standard": "UTC",
        }
        if archivo in ok_previos:
            ya_estaban += 1
            continue

        tamano = tamano_si_existe(clave)
        if tamano is not None:
            # Ya estaba en S3 antes de este registro: se anota sin recalcular el checksum.
            nuevas.append({**base, "estado": "ok", "bytes": tamano, "sha256": None,
                           "detalle": "existía antes del registro"})
            ya_estaban += 1
            continue

        try:
            datos = pedir_nasa(latitud, longitud, anio)
            s3.put_object(Bucket=BUCKET, Key=clave, Body=datos,
                          ContentType="application/json")
            nuevas.append({**base, "estado": "ok", "bytes": len(datos),
                           "sha256": hashlib.sha256(datos).hexdigest()})
            descargadas += 1
            time.sleep(PAUSA_SEGUNDOS)
        except RuntimeError as e:
            nuevas.append({**base, "estado": "error", "detalle": str(e)})
            fallidos += 1

    if nuevas:
        # Quita los errores anteriores de los archivos que se volvieron a intentar,
        # para que el registro refleje solo el último intento.
        reintentados = {e["archivo"] for e in nuevas}
        conservar = [e for e in previas
                     if not (e.get("estado") == "error" and e["archivo"] in reintentados)]
        texto = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in conservar + nuevas)
        s3.put_object(Bucket=BUCKET, Key=clave_registro(codigo), Body=texto.encode("utf-8"),
                      ContentType="application/x-ndjson")

    resultado = {"codigo": codigo, "latitud": latitud, "longitud": longitud,
                 "descargadas": descargadas, "ya_estaban": ya_estaban, "fallidos": fallidos}
    print(json.dumps(resultado))
    if fallidos:
        # Falla la ejecución para que Step Functions active Notificar (R4-5).
        raise RuntimeError(f"{fallidos} año(s) fallaron para la estación {codigo}. "
                           f"Revisa {clave_registro(codigo)}")
    return resultado