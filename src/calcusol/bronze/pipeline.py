"""
Bronze — Orquestador.

Punto de entrada único para ejecutar la ingesta Bronze completa o una fuente
concreta, y la verificación de completitud al final.

EJECUCIÓN (desde la raíz, dentro de Docker):
  docker compose run --rm calcusol uv run python -m calcusol.bronze.pipeline
  docker compose run --rm calcusol uv run python -m calcusol.bronze.pipeline --source nasa
  docker compose run --rm calcusol uv run python -m calcusol.bronze.pipeline --source ideam
  docker compose run --rm calcusol uv run python -m calcusol.bronze.pipeline --no-verificar

Portabilidad: este módulo solo coordina llamadas a la lógica pura de cada
fuente; en Step Functions cada fuente sería un estado que invoca su entry point.
"""

from __future__ import annotations

import argparse

from calcusol.bronze import ideam, nasa, validation


def run(source: str = "all", verificar: bool = True,
        anio_inicio: int = nasa.ANIO_INICIO_DEFECTO,
        anio_fin: int = nasa.ANIO_FIN_DEFECTO) -> None:
    if source in ("all", "ideam"):
        print("\n==================== INGESTA IDEAM ====================")
        ideam.incorporar()

    if source in ("all", "nasa"):
        print("\n==================== INGESTA NASA =====================")
        nasa.ingestar(anio_inicio=anio_inicio, anio_fin=anio_fin)

    if verificar:
        print("\n================ VERIFICACIÓN BRONZE =================")
        validation.main()


def main() -> None:
    p = argparse.ArgumentParser(description="Orquestador de ingesta Bronze.")
    p.add_argument("--source", choices=["all", "nasa", "ideam"], default="all",
                   help="Fuente a ingerir (por defecto todas).")
    p.add_argument("--no-verificar", action="store_true",
                   help="Omite la verificación de completitud final.")
    p.add_argument("--anio-inicio", type=int, default=nasa.ANIO_INICIO_DEFECTO)
    p.add_argument("--anio-fin", type=int, default=nasa.ANIO_FIN_DEFECTO)
    args = p.parse_args()

    run(source=args.source, verificar=not args.no_verificar,
        anio_inicio=args.anio_inicio, anio_fin=args.anio_fin)


if __name__ == "__main__":
    main()
