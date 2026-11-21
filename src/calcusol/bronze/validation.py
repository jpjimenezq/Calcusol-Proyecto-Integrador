"""
Bronze — Verificación post-ingesta (completitud).

Lee los manifests de NASA e IDEAM y comprueba que lo ingerido está completo y
sano, SIN descargar ni abrir los datos crudos:

  NASA   - ¿todas las estaciones con radiación tienen su rango de años esperado?
         - ¿hay entradas en estado 'error'?
  IDEAM  - ¿se registraron los 212 CSV de mediciones y el catálogo?
         - ¿hay archivos 'invalido'?

Es el paso final del pipeline Bronze: confirma que se puede pasar a Silver.
Devuelve un reporte y un indicador global ok/incompleto.

EJECUCIÓN:
  docker compose run --rm calcusol uv run python -m calcusol.bronze.validation
"""

from __future__ import annotations

from pathlib import Path

from calcusol.bronze import ideam as ideam_mod
from calcusol.bronze import manifest
from calcusol.bronze import nasa as nasa_mod
from calcusol.config import settings


def verificar_nasa(anio_inicio: int = nasa_mod.ANIO_INICIO_DEFECTO,
                   anio_fin: int = nasa_mod.ANIO_FIN_DEFECTO) -> dict:
    """Completitud de NASA: cada estación con radiación × cada año esperado."""
    anio_inicio = max(anio_inicio, nasa_mod.PISO_NASA)
    coords = nasa_mod.leer_coordenadas()           # 136 estaciones con coords
    anios = list(range(anio_inicio, anio_fin + 1))

    entradas = manifest.leer("nasa")
    ok = {Path(e["archivo"]).name for e in entradas if e.get("estado") == "ok"}
    errores = [e for e in entradas if e.get("estado") == "error"]

    esperados, faltantes = 0, []
    for codigo, (lat, lon) in coords.items():
        for anio in anios:
            esperados += 1
            archivo = nasa_mod.nombre_archivo(lat, lon, anio)
            if archivo not in ok:
                faltantes.append((codigo, anio, archivo))

    return {
        "estaciones": len(coords),
        "anios": f"{anio_inicio}-{anio_fin}",
        "esperados": esperados,
        "presentes_ok": esperados - len(faltantes),
        "faltantes": len(faltantes),
        "con_error": len(errores),
        "ejemplos_faltantes": faltantes[:10],
        "completo": len(faltantes) == 0 and len(errores) == 0,
    }


def verificar_ideam() -> dict:
    """Completitud de IDEAM: CSV de mediciones en disco vs registrados OK."""
    base_med = Path(ideam_mod.MEDICIONES)
    csv_en_disco = sorted(base_med.rglob("*.csv"))

    entradas = manifest.leer("ideam")
    ok = {e["archivo"] for e in entradas if e.get("estado") == "ok"}
    invalidos = [e for e in entradas if e.get("estado") == "invalido"]

    base_ideam = Path(settings.bronze_path("ideam"))
    faltan_registro = [
        str(a.relative_to(base_ideam)) for a in csv_en_disco
        if str(a.relative_to(base_ideam)) not in ok
    ]

    catalogo_ok = any(
        e.get("subfuente") == "catalogo" and e.get("estado") == "ok"
        for e in entradas
    )

    return {
        "csv_en_disco": len(csv_en_disco),
        "registrados_ok": len([e for e in entradas
                               if e.get("subfuente") == "mediciones"
                               and e.get("estado") == "ok"]),
        "sin_registrar": len(faltan_registro),
        "invalidos": len(invalidos),
        "catalogo_ok": catalogo_ok,
        "ejemplos_sin_registrar": faltan_registro[:10],
        "completo": not faltan_registro and not invalidos and catalogo_ok,
    }


def _imprimir(titulo: str, rep: dict) -> None:
    print(f"\n--- {titulo} ---")
    for k, v in rep.items():
        if k.startswith("ejemplos") and not v:
            continue
        print(f"  {k}: {v}")


def main() -> None:
    rep_nasa = verificar_nasa()
    rep_ideam = verificar_ideam()

    _imprimir("NASA", rep_nasa)
    _imprimir("IDEAM", rep_ideam)

    global_ok = rep_nasa["completo"] and rep_ideam["completo"]
    print("\n" + "=" * 50)
    print(f"BRONZE {'COMPLETO ✓' if global_ok else 'INCOMPLETO ✗'}")
    print("=" * 50)
    if not global_ok:
        print("Revisa los faltantes antes de pasar a Silver.")


if __name__ == "__main__":
    main()
