"""
ML — Decisión de promoción, promoción y rollback del modelo de radiación.

Un modelo candidato (v2, v3...) solo reemplaza al activo si cumple TODOS los criterios
(basados en la evaluación R2, validacion/evaluar_modelo.py). La métrica de negocio es el
MAE mensual: el Payback depende de la energía acumulada.

  1. Sin fuga: veredicto "OK: sin fuga" (ningún sitio/estación de prueba en el train).
  2. Mejora el MAE mensual del examen ESPACIAL (estaciones nunca vistas) frente al activo
     al menos `mejora_min_mae_mes_pct`, y el bootstrap pareado (2.000 remuestreos,
     semilla 42) la confirma en >= `confianza_min` de los remuestreos (IC95).
  3. El sesgo mensual espacial no empeora más de `sesgo_max_empeora_pp` puntos.
  4. Pasa el examen temporal y el espacio-temporal: no empeora el MAE mensual del activo
     más de `tolerancia_examenes_pct`.
  5. Supera a NASA sin corregir (B0) en MAE mensual en los tres exámenes.
  6. El artefacto carga con LightGBM, coincide con su sha256 y predice valores finitos.

Los umbrales son valores por defecto editables con --umbrales archivo.json. Promover
solo cambia el manifiesto (radiacion/active/manifest.json); las versiones no se tocan.
Funciona igual en local y en S3 (ver ml/registro.py).

EJECUCIÓN:
  python -m calcusol.ml.promocion evaluar  --version v2 [--gold ...] [--registro ...]
  python -m calcusol.ml.promocion promover --version v2 [--por NOMBRE] [--forzar --motivo "..."]
  python -m calcusol.ml.promocion rollback [--por NOMBRE] [--motivo "..."]
  python -m calcusol.ml.promocion estado
  python -m calcusol.ml.promocion fijar    --version v1 [--por NOMBRE]     (hace explícito el activo; sin manifiesto previo)
  python -m calcusol.ml.promocion sembrar  --version v1 --origen models/radiacion/v1 --registro s3://...   (primera vez)
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

import lightgbm as lgb
import numpy as np

from calcusol.ml import evaluate
from calcusol.ml import features as feat
from calcusol.ml import registro as reg
from calcusol.ml.predict import CorrectorRadiacion

UMBRALES = {
    "mejora_min_mae_mes_pct": 0.5,     # mejora mínima del MAE mensual espacial frente al activo
    "confianza_min": 0.95,             # fracción de remuestreos a favor (IC95 no cruza cero)
    "sesgo_max_empeora_pp": 1.0,       # puntos porcentuales de sesgo mensual espacial
    "tolerancia_examenes_pct": 1.0,    # empeoramiento admitido en temporal / espacio-temporal
    "remuestreos": 2000,
    "semilla": 42,
}


class PromocionRechazada(RuntimeError):
    pass


# --------------------------------------------------------------------------- activo
def version_activa(registro: reg.Registro) -> str | None:
    """Versión en servicio: la del manifiesto o, si aún no hay manifiesto, v1 (modelo embebido)."""
    return registro.version_activa() or ("v1" if registro.existe_version("v1") else None)


# ------------------------------------------------------------------- estadística
def bootstrap_mejora(tabla, col_cand: str, col_act: str, remuestreos: int, semilla: int) -> dict:
    """Bootstrap pareado sobre estación-mes: mejora % del MAE del candidato frente al activo."""
    y = tabla["y"].to_numpy()
    e_c = np.abs(y - tabla[col_cand].to_numpy())
    e_a = np.abs(y - tabla[col_act].to_numpy())
    rng = np.random.default_rng(semilla)
    n = len(y)
    mejoras = np.empty(remuestreos)
    for i in range(remuestreos):
        idx = rng.integers(0, n, n)
        mejoras[i] = 100 * (1 - e_c[idx].mean() / e_a[idx].mean())
    return {"mejora_pct": float(100 * (1 - e_c.mean() / e_a.mean())),
            "ic95": [float(np.percentile(mejoras, 2.5)), float(np.percentile(mejoras, 97.5))],
            "pct_remuestreos_a_favor": float((mejoras > 0).mean())}


def comprobar_artefacto(registro: reg.Registro, version: str) -> dict:
    """El modelo carga con LightGBM, coincide con su sha256 y predice valores finitos."""
    try:
        meta = registro.metadatos(version)
        sha = registro.sha256_modelo(version)
        esperado = meta.get("modelo_sha256")
        booster = lgb.Booster(model_file=registro.descargar_modelo(version))
        ok_forma = booster.num_feature() == len(feat.FEATURES)
        pred = booster.predict(np.zeros((3, len(feat.FEATURES))))
        ok = bool(ok_forma and np.isfinite(pred).all() and (esperado is None or esperado == sha))
        return {"ok": ok, "sha256": sha, "sha256_coincide": esperado in (None, sha), "n_features": booster.num_feature()}
    except Exception as e:  # el motivo queda en la decisión
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ----------------------------------------------------------------------- decisión
def decidir(res_cand: dict, res_act: dict | None, mensual, fuga: dict, artefacto: dict,
            umbrales: dict | None = None, nombre_cand: str = "cand", nombre_act: str = "act") -> dict:
    """Aplica los criterios. `res_*` = resultados por examen de evaluate.evaluar_modelos."""
    u = {**UMBRALES, **(umbrales or {})}
    criterios: list[dict] = []

    def crit(nombre, ok, valor, umbral=None):
        criterios.append({"criterio": nombre, "ok": bool(ok), "valor": valor, "umbral": umbral})

    crit("sin_fuga", fuga["veredicto"] == "OK: sin fuga", fuga["veredicto"], "OK: sin fuga")
    crit("artefacto_carga", artefacto.get("ok", False), artefacto.get("error") or artefacto.get("sha256"), True)

    mes = lambda r, ex, k="MAE": r[ex]["modelo"]["mes"][k]          # noqa: E731
    base = lambda r, ex: r[ex]["baseline"]["mes"]["MAE"]            # noqa: E731
    for ex in evaluate.EXAMENES:
        crit(f"supera_a_nasa:{ex}", mes(res_cand, ex) < base(res_cand, ex),
             {"MAE_mes": round(mes(res_cand, ex), 3), "MAE_nasa": round(base(res_cand, ex), 3)}, "MAE_mes < MAE_nasa")

    if res_act is not None:
        ex = "test_spatial"
        b = bootstrap_mejora(mensual[ex], f"p_{nombre_cand}", f"p_{nombre_act}", u["remuestreos"], u["semilla"])
        crit("mejora_mae_mes_espacial", b["mejora_pct"] >= u["mejora_min_mae_mes_pct"], round(b["mejora_pct"], 3),
             f">= {u['mejora_min_mae_mes_pct']} %")
        crit("mejora_confirmada_bootstrap", b["pct_remuestreos_a_favor"] >= u["confianza_min"],
             {"pct_remuestreos_a_favor": round(b["pct_remuestreos_a_favor"], 4), "ic95_mejora_pct": [round(x, 3) for x in b["ic95"]]},
             f">= {u['confianza_min']}")
        d_sesgo = abs(mes(res_cand, ex, "sesgo_%")) - abs(mes(res_act, ex, "sesgo_%"))
        crit("sesgo_no_empeora", d_sesgo <= u["sesgo_max_empeora_pp"], round(float(d_sesgo), 3),
             f"<= {u['sesgo_max_empeora_pp']} pp")
        for ex2 in ("test_temporal", "test_spatiotemporal"):
            peor = 100 * (mes(res_cand, ex2) / mes(res_act, ex2) - 1)
            crit(f"no_empeora:{ex2}", peor <= u["tolerancia_examenes_pct"], round(float(peor), 3),
                 f"<= {u['tolerancia_examenes_pct']} %")
    else:
        b = None

    aprobado = all(c["ok"] for c in criterios)
    return {"aprobado": aprobado, "criterios": criterios, "umbrales": u,
            "fallidos": [c["criterio"] for c in criterios if not c["ok"]],
            "bootstrap_espacial": b}


def evaluar_candidato(registro: reg.Registro, version: str, ruta_gold: str | None = None,
                      umbrales: dict | None = None) -> dict:
    """
    Evalúa el candidato contra el activo sobre las mismas filas, guarda metricas.json
    (una sola vez) y decision.json, y devuelve la decisión.
    """
    if not registro.existe_version(version):
        raise PromocionRechazada(f"{version} no existe en {registro.raiz}.")
    activo = version_activa(registro)
    if activo == version:
        raise PromocionRechazada(f"{version} ya es la versión activa.")
    modelos = {"cand": CorrectorRadiacion(registro.descargar_modelo(version))}
    if activo:
        modelos["act"] = CorrectorRadiacion(registro.descargar_modelo(activo))
    resultados, mensual, fuga = evaluate.evaluar_modelos(modelos, ruta_gold)
    artefacto = comprobar_artefacto(registro, version)
    d = decidir(resultados["cand"], resultados.get("act"), mensual, fuga, artefacto, umbrales)
    meta = registro.metadatos(version)
    if registro.metricas(version) is None:
        registro.escribir_metricas(version, {
            "version": version, "gold_version": meta.get("gold_version"), "evaluado_utc": reg.ahora_utc(),
            "examenes": resultados["cand"], "fuga": fuga})
    d.update({"version": version, "activo_version": activo, "evaluado_utc": reg.ahora_utc(),
              "gold_evaluacion": reg.huella_gold(ruta_gold)["gold_version"] if ruta_gold else None,
              "metricas_activo": resultados.get("act")})
    registro.escribir_decision(version, d)
    return d


# ------------------------------------------------------------ promover / rollback
def promover(registro: reg.Registro, version: str, por: str, forzar: bool = False, motivo: str = "") -> dict:
    """
    Pone `version` como activa. Exige decisión aprobada frente al activo actual; con
    `forzar` (promoción manual) basta un responsable y un motivo, y queda registrado.
    """
    if not registro.existe_version(version):
        raise PromocionRechazada(f"{version} no existe.")
    activo = version_activa(registro)
    if activo == version:
        raise PromocionRechazada(f"{version} ya es la versión activa.")
    dec = registro.decision(version)
    if forzar:
        if not motivo:
            raise PromocionRechazada("La promoción forzada exige --motivo.")
        if not comprobar_artefacto(registro, version)["ok"]:
            raise PromocionRechazada("El artefacto no carga con LightGBM; no se puede promover ni forzado.")
        etiqueta = f"{por} (forzado)"
        motivo = f"FORZADO: {motivo}"
    else:
        if dec is None:
            raise PromocionRechazada(f"Falta la evaluación: ejecute `promocion evaluar --version {version}`.")
        if dec.get("activo_version") != activo:
            raise PromocionRechazada(
                f"La evaluación de {version} fue contra {dec.get('activo_version')} y el activo ahora es {activo}; vuelva a evaluar.")
        if not dec["aprobado"]:
            raise PromocionRechazada(f"{version} no cumple los criterios: {', '.join(dec['fallidos'])}.")
        etiqueta, motivo = por, motivo or "cumple los criterios de promoción"
    if registro.manifiesto() is None and activo:          # deja a v1 registrado para poder volver a él
        registro.escribir_manifiesto(registro.construir_manifiesto(activo, "bootstrap", "modelo inicial"))
    nuevo = registro.construir_manifiesto(version, etiqueta, motivo)
    registro.escribir_manifiesto(nuevo)
    return nuevo


def fijar(registro: reg.Registro, version: str, por: str, motivo: str = "") -> dict:
    """
    Escribe el manifiesto del modelo que ya está en servicio (p. ej. v1) cuando aún no existe ninguno:
    el modelo activo pasa a ser explícito sin cambiar qué modelo se sirve. No evalúa ni compara.
    """
    if registro.manifiesto() is not None:
        raise PromocionRechazada(f"Ya hay un manifiesto (activa: {registro.version_activa()}); use promover o rollback.")
    if version != version_activa(registro):
        raise PromocionRechazada(f"Solo se puede fijar la versión activa actual ({version_activa(registro)}).")
    m = registro.construir_manifiesto(version, por, motivo or "modelo activo explícito")
    registro.escribir_manifiesto(m)
    return m


def rollback(registro: reg.Registro, por: str, motivo: str = "") -> dict:
    """Vuelve al manifiesto anterior. No borra ninguna versión."""
    return registro.retirar_manifiesto_actual({"rollback_por": por, "rollback_motivo": motivo})


# ------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evalúa, promueve y revierte versiones del modelo de radiación.")
    ap.add_argument("accion", choices=["evaluar", "promover", "rollback", "estado", "sembrar", "fijar"])
    ap.add_argument("--version")
    ap.add_argument("--origen", help="sembrar: carpeta local con modelo_lgbm.txt y metadatos.json (p. ej. models/radiacion/v1)")
    ap.add_argument("--registro", help="raíz del registro: local o s3://... (por defecto CALCUSOL_MODELOS_ROOT o ./models)")
    ap.add_argument("--gold", help="Gold para evaluar: local o s3://...")
    ap.add_argument("--umbrales", help="JSON con umbrales que reemplazan a los de por defecto")
    ap.add_argument("--por", default=getpass.getuser(), help="quién promueve (queda en el manifiesto)")
    ap.add_argument("--motivo", default="")
    ap.add_argument("--forzar", action="store_true", help="promoción manual aunque no pase los criterios")
    a = ap.parse_args(argv)
    r = reg.Registro(a.registro)
    try:
        if a.accion == "estado":
            print(json.dumps({"activa": version_activa(r), "manifiesto": r.manifiesto(), "versiones": r.versiones(),
                              "historial": [h["model_version"] for h in r.historial()]}, ensure_ascii=False, indent=2))
        elif a.accion == "sembrar":              # sube una versión existente al registro (p. ej. v1 a S3); no crea manifiesto
            if not (a.version and a.origen):
                ap.error("--version y --origen son obligatorios")
            print(json.dumps(r.publicar_version(a.version, a.origen)))
        elif a.accion == "fijar":                # hace explícito el modelo activo cuando todavía no hay manifiesto
            if not a.version:
                ap.error("--version es obligatorio")
            print(json.dumps(fijar(r, a.version, a.por, a.motivo), ensure_ascii=False, indent=2))
        elif a.accion == "evaluar":
            if not a.version:
                ap.error("--version es obligatorio")
            u = json.load(open(a.umbrales, encoding="utf-8")) if a.umbrales else None
            d = evaluar_candidato(r, a.version, a.gold, u)
            for c in d["criterios"]:
                print(f"  [{'OK' if c['ok'] else 'NO'}] {c['criterio']}: {c['valor']}  (umbral: {c['umbral']})")
            print(f"\n{a.version} frente a {d['activo_version']}: {'APROBADO' if d['aprobado'] else 'RECHAZADO'}")
            return 0 if d["aprobado"] else 2
        elif a.accion == "promover":
            if not a.version:
                ap.error("--version es obligatorio")
            m = promover(r, a.version, a.por, a.forzar, a.motivo)
            print(json.dumps(m, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(rollback(r, a.por, a.motivo), ensure_ascii=False, indent=2))
    except (PromocionRechazada, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
