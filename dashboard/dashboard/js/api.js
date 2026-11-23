/* Cliente de la API de Calcusol (física y finanzas de R3).
 *
 * Hace lo mismo que Calcusol.simular (model.js) pero el cálculo lo hace el servidor:
 * recibe el mismo objeto de entrada y devuelve el mismo formato, así app.js y los gráficos
 * no cambian. Si la API no responde, app.js usa model.js como respaldo.
 *
 * URL de la API: ?api=http://127.0.0.1:8000 en la dirección del dashboard (por defecto, esa).
 * ?api=off fuerza el cálculo local. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.CalcusolAPI = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // js/config.js puede fijar window.CALCUSOL_API (lo hace el despliegue); en local se usa la API de este equipo
  const POR_DEFECTO = (typeof window !== 'undefined' && window.CALCUSOL_API) || 'http://127.0.0.1:8000';
  const cache = new Map();           // cuerpo de la petición -> resultado ya adaptado
  let base = POR_DEFECTO;

  function configurar(url) { base = (url || POR_DEFECTO).replace(/\/+$/, ''); cache.clear(); }
  const url = () => base;

  async function llamar(ruta, opciones, ms) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms || 60000);
    try {
      const r = await fetch(base + ruta, { ...opciones, signal: ctl.signal });
      if (!r.ok) {
        let detalle = '';
        try { detalle = JSON.stringify((await r.json()).detail); } catch (_) { /* sin cuerpo */ }
        throw new Error(`API ${r.status} ${ruta} ${detalle}`.trim());
      }
      return await r.json();
    } finally { clearTimeout(t); }
  }

  /* true si la API responde y trae el endpoint del dashboard. */
  async function disponible() {
    try {
      const salud = await llamar('/health', {}, 4000);
      return salud.estado === 'ok';
    } catch (_) { return false; }
  }

  /* Valores por defecto de la ficha de supuestos de R3 (GET /v1/parametros): hogar, sistema, finanzas, tarifas
   * por estrato y, de cada parámetro, su fuente y su estado. Lanza error si la API no los trae. */
  function parametros() {
    return llamar('/v1/parametros', {}, 8000);
  }

  /* Cuerpo de POST /v1/simulacion/climatologia a partir de la entrada de Calcusol.simular.
   * e = { est, fuente, kwp, consumo, perfil, estrato, sup, horas }; perfiles = Calcusol.PERFILES */
  function cuerpo(e, perfiles) {
    const sup = e.sup;
    // Con una ubicación exacta del hogar, la geometría del sol (pvlib) usa esa latitud y longitud, redondeadas a ~1 km
    // por privacidad; la radiación, la temperatura y la altitud son las de la estación más cercana.
    const r2 = (v) => Math.round(v * 100) / 100;
    const lat = e.punto ? r2(e.punto.lat) : e.est.lat, lon = e.punto ? r2(e.punto.lon) : e.est.lon;
    return {
      ubicacion: { latitud: lat, longitud: lon, altitud: e.est.alt },
      climatologia: { horas: e.horas, ghi: e.est.clim[e.fuente], t_aire: e.est.clim.t2m },
      sistema: {
        kwp: e.kwp, kwh_mes: e.consumo, estrato: e.estrato,
        precio_excedente_cop: sup.precioBolsa,
        // El dashboard separa un costo fijo del costo por kWp; la API usa un solo costo por kWp.
        costo_kwp_cop: (sup.capexFijo + sup.capexKwp * e.kwp) / e.kwp,
        tasa_descuento: sup.descuento,
      },
      ajustes: {
        perdidas: sup.perdidas, degradacion_anual: sup.degradacion, mantenimiento_pct: sup.omPct,
        alza_tarifa: sup.alzaTarifa, horizonte_anios: sup.horizonte, comercializacion_cop: sup.comercializacion,
        eficiencia_inversor: sup.eficiencia, degradacion_primer_anio: sup.degradacionInicial,
        anio_reemplazo_inversor: sup.anioInversor, costo_reemplazo_inversor_cop: sup.costoInversor,
        // Tarifas EPM del estrato del hogar (las que muestra el panel; se cambian solas al cambiar el estrato)
        tarifa_subsistencia_cop: sup.tarifaSub, tarifa_plena_cop: sup.tarifaPlena,
        perfil_consumo: perfiles[e.perfil].horas,
      },
    };
  }

  /* Respuesta de la API -> mismo formato que devuelve Calcusol.simular. */
  function adaptar(r, e) {
    const meses = r.mensual.map((m) => ({
      generado: m.generado, autoconsumo: m.autoconsumo, excedente: m.excedente, importado: m.importado,
      sinSolar: m.factura_sin_solar, conSolar: m.factura_con_solar, ahorro: m.ahorro,
    }));
    const generado = r.kWh_produccion, autoconsumo = r.kWh_autoconsumo;
    const payback = r.payback_simple_anios;            // null si no se recupera
    const vpn = r.VPN_cop;
    return {
      kwp: e.kwp, capex: r.capex_cop, meses, flujo: r.flujo_acumulado_cop, vpn, payback, tir: r.TIR,
      genMes: generado / 12,
      excedenteMes: r.kWh_excedente / 12,
      ahorroMes: meses.reduce((s, m) => s + m.ahorro, 0) / 12,
      facturaSinSolar: r.factura_anual_sin_panel_cop / 12,
      autoconsumoPct: generado > 0 ? autoconsumo / generado : 0,
      coberturaPct: r.kWh_consumo > 0 ? autoconsumo / r.kWh_consumo : 0,
      viable: vpn > 0 && payback !== null,
      genDia: r.produccion_dia_promedio_kwh,
      avisos: Object.keys(r).filter((k) => k.startsWith('aviso')).map((k) => r[k]),
    };
  }

  /* Simula con la API. Repite la misma entrada sin volver a llamar (resultados en caché). */
  function simular(e, perfiles) {
    const c = cuerpo(e, perfiles), clave = JSON.stringify(c);
    if (!cache.has(clave)) {
      const p = llamar('/v1/simulacion/climatologia', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: clave,
      }).then((r) => adaptar(r, e));
      cache.set(clave, p);
      p.catch(() => cache.delete(clave));              // un error no se queda en caché
    }
    return cache.get(clave);
  }

  return { configurar, url, disponible, parametros, simular, cuerpo, adaptar };
});
