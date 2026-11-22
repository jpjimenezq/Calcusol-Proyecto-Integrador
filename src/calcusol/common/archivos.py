"""
Archivos en el PC o en S3 con las mismas funciones.

Spark lee y escribe s3:// solo; esto es para lo que hace Python directamente:
listar carpetas, saber si algo existe y mirar el inicio de un archivo.
"""

from __future__ import annotations

from pathlib import Path


def es_s3(ruta) -> bool:
    return str(ruta).startswith(("s3://", "s3a://"))


def unir(base, *partes) -> str:
    if es_s3(base):
        return "/".join([str(base).rstrip("/"), *partes])
    return str(Path(base, *partes))


def nombre(ruta) -> str:
    """Último trozo de la ruta (nombre de archivo o carpeta)."""
    return str(ruta).replace("\\", "/").rstrip("/").split("/")[-1]


def _s3(ruta):
    import boto3                                    # viene instalado en Glue
    cuerpo = str(ruta).split("://", 1)[1]
    bucket, _, clave = cuerpo.partition("/")
    return boto3.client("s3"), bucket, clave


def _listar_s3(ruta):
    cliente, bucket, clave = _s3(ruta)
    esquema = str(ruta).split("://", 1)[0]
    prefijo = clave.rstrip("/") + "/" if clave else ""
    archivos, carpetas = [], []
    for pagina in cliente.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefijo, Delimiter="/"):
        archivos += [f"{esquema}://{bucket}/{o['Key']}" for o in pagina.get("Contents", [])
                     if not o["Key"].endswith("/")]
        carpetas += [f"{esquema}://{bucket}/{c['Prefix'].rstrip('/')}"
                     for c in pagina.get("CommonPrefixes", [])]
    return archivos, carpetas


def listar(ruta, sufijo: str = "") -> list[str]:
    """Archivos que están directamente en la carpeta y terminan en `sufijo`."""
    if es_s3(ruta):
        archivos, _ = _listar_s3(ruta)
    else:
        archivos = [str(a) for a in Path(ruta).iterdir() if a.is_file()]
    return sorted(a for a in archivos if a.lower().endswith(sufijo.lower()))


def listar_carpetas(ruta) -> list[str]:
    if es_s3(ruta):
        _, carpetas = _listar_s3(ruta)
    else:
        carpetas = [str(c) for c in Path(ruta).iterdir() if c.is_dir()]
    return sorted(carpetas)


def existe(ruta) -> bool:
    if es_s3(ruta):
        cliente, bucket, clave = _s3(ruta)
        r = cliente.list_objects_v2(Bucket=bucket, Prefix=clave.rstrip("/"), MaxKeys=1)
        return r.get("KeyCount", 0) > 0
    return Path(ruta).exists()


def leer_inicio(ruta, n: int = 65536) -> bytes:
    """Primeros n bytes de un archivo."""
    if es_s3(ruta):
        cliente, bucket, clave = _s3(ruta)
        return cliente.get_object(Bucket=bucket, Key=clave, Range=f"bytes=0-{n - 1}")["Body"].read()
    with open(ruta, "rb") as f:
        return f.read(n)
