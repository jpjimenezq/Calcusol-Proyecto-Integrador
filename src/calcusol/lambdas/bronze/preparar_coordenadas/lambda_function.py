import csv
import io
import re

import boto3

BUCKET = "calcusol-datos-eafit"
CATALOGO = "bronze/ideam/catalogo/CNE_IDEAM.csv"
MEDICIONES = "bronze/ideam/mediciones/"
PATRON = re.compile(r"ideam_radiacion_(\d+)_")
ANIOS_DEFECTO = list(range(2005, 2023))

s3 = boto3.client("s3", region_name="us-east-1")


def estaciones_con_radiacion():
    codigos = set()
    paginas = s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=MEDICIONES)
    for pagina in paginas:
        for obj in pagina.get("Contents", []):
            m = PATRON.search(obj["Key"])
            if m:
                codigos.add(str(int(m.group(1))))
    return codigos


def estaciones_a_descargar(codigos):
    """Una fila por estación (como R5, drop_duplicates por código), con coordenadas válidas."""
    texto = s3.get_object(Bucket=BUCKET, Key=CATALOGO)["Body"].read().decode("utf-8")
    vistas = {}
    for r in csv.DictReader(io.StringIO(texto)):
        try:
            codigo = str(int(r["CODIGO"].strip()))
            lat = round(float(r["LATITUD"].replace(",", ".")), 4)
            lon = round(float(r["LONGITUD"].replace(",", ".")), 4)
        except (ValueError, KeyError, AttributeError):
            continue
        if codigo in codigos and codigo not in vistas:
            vistas[codigo] = {"codigo": codigo, "latitud": lat, "longitud": lon}
    return [vistas[c] for c in sorted(vistas)]


def lambda_handler(event, context):
    anios = event.get("anios", ANIOS_DEFECTO)
    codigos = estaciones_con_radiacion()
    lista = estaciones_a_descargar(codigos)
    sin_coordenadas = len(codigos) - len(lista)
    if event.get("limite"):
        lista = lista[: int(event["limite"])]
    print(f"{len(codigos)} estaciones con radiacion; {len(lista)} en la salida; "
          f"{sin_coordenadas} sin coordenadas en el catalogo")
    return {"estaciones": lista, "anios": anios}
