/* Modelo energético y financiero de Calcusol.
 * Funciones puras: sirven igual en el navegador y en Node (tests/check_model.mjs).
 * Todo el balance se hace sobre el "día promedio" de cada mes, hora a hora. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Calcusol = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DIAS_MES = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

  /* Reparto del consumo diario en 24 horas (suma 1). Son supuestos de forma. */
  const norm = (a) => { const s = a.reduce((x, y) => x + y, 0); return a.map((v) => v / s); };
  const PERFILES = {
    tipico: {
      nombre: 'Típico residencial',
      horas: norm([2, 2, 2, 2, 2.5, 4, 5.5, 5, 4, 3.5, 3.5, 4, 5, 4.5, 3.5, 3.5, 4, 5, 7, 8, 7.5, 6, 4, 2.5]),
    },
    diurno: {
      nombre: 'Diurno (hogar ocupado de día)',
      horas: norm([1.5, 1.5, 1.5, 1.5, 2, 3, 4, 5, 6, 6.5, 6.5, 7, 7, 6.5, 6, 5.5, 5, 5, 5, 5, 4.5, 3.5, 2.5, 2]),
    },
    nocturno: {
      nombre: 'Nocturno (hogar vacío de día)',
      horas: norm([2.5, 2.5, 2.5, 2.5, 3, 5, 6.5, 4, 2, 1.5, 1.5, 1.5, 2, 1.5, 1.5, 1.5, 2.5, 5, 8.5, 10, 9.5, 8, 5.5, 3.5]),
    },
  };

  /* Valores de referencia. Todos se pueden cambiar en el panel de supuestos. */
  const SUPUESTOS = {
    tarifa: 900,            // $/kWh, costo unitario (CU) del operador
    comercializacion: 120,  // $/kWh que se paga sobre la energía permutada (CREG 174)
    precioBolsa: 300,       // $/kWh al que se liquidan excedentes por encima de lo importado
    capexFijo: 3.0e6,       // $ por instalación (diseño, trámites, medidor bidireccional)
    capexKwp: 4.2e6,        // $ por kWp instalado
    omPct: 0.01,            // operación y mantenimiento anual, fracción del CAPEX
    degradacion: 0.005,     // pérdida anual de producción de los paneles
    alzaTarifa: 0.04,       // crecimiento anual de la tarifa
    descuento: 0.10,        // tasa de descuento del VPN
    horizonte: 25,          // años de vida útil evaluados
    perdidas: 0.14,         // inversor, cableado, suciedad, sombras
    gamma: -0.004,          // coeficiente de temperatura de potencia (1/°C)
    noct: 45,               // temperatura nominal de operación de la celda (°C)
  };

  const SUBSIDIO = { 1: 0.60, 2: 0.50, 3: 0.15, 4: 0, 5: -0.20, 6: -0.20 };

  /* Consumo de subsistencia (UPME): 130 kWh/mes a 1000 m o más, 173 por debajo. */
  const subsistencia = (altitud) => (altitud >= 1000 ? 130 : 173);

  /* Factura de energía de un mes para `kwh` importados de la red. */
  function factura(kwh, estrato, tarifa, altitud) {
    const s = SUBSIDIO[estrato];
    if (s < 0) return kwh * tarifa * (1 - s);            // contribución estratos 5 y 6
    const base = Math.min(kwh, subsistencia(altitud));
    return tarifa * (base * (1 - s) + (kwh - base));     // subsidio solo hasta la subsistencia
  }

  /* Generación del día promedio: kWh por hora [mes][hora 0-23]. */
  function generacion(est, fuente, kwp, sup, horas) {
    return est.clim[fuente].map((fila, m) => {
      const dia = new Array(24).fill(0);
      fila.forEach((g, i) => {
        const tCelda = est.clim.t2m[m][i] + g * (sup.noct - 20) / 800;
        const factorT = 1 + sup.gamma * (tCelda - 25);
        dia[horas[i]] = Math.max(0, kwp * (g / 1000) * factorT * (1 - sup.perdidas));
      });
      return dia;
    });
  }

  /* Simula un sistema: energía, autoconsumo, excedentes, ahorro, Payback y VPN.
   * e = { est, fuente, kwp, consumo, perfil, estrato, sup, horas } */
  function simular(e) {
    const sup = e.sup;
    const perfil = PERFILES[e.perfil].horas;
    const gen = generacion(e.est, e.fuente, e.kwp, sup, e.horas);
    const meses = gen.map((dia, m) => {
      const dm = DIAS_MES[m];
      const cargaDia = e.consumo / dm;
      let g = 0, auto = 0;
      dia.forEach((gh, h) => { g += gh; auto += Math.min(gh, cargaDia * perfil[h]); });
      const generado = g * dm, autoconsumo = auto * dm;
      const excedente = generado - autoconsumo;
      const importado = e.consumo - autoconsumo;
      const sinSolar = factura(e.consumo, e.estrato, sup.tarifa, e.est.alt);
      // CREG 174 de 2021: los excedentes se permutan 1 a 1 contra lo importado
      // (se paga la comercialización); lo que sobra se liquida a precio de bolsa.
      const conSolar = excedente <= importado
        ? factura(importado - excedente, e.estrato, sup.tarifa, e.est.alt) + excedente * sup.comercializacion
        : importado * sup.comercializacion - (excedente - importado) * sup.precioBolsa;
      return { generado, autoconsumo, excedente, importado, sinSolar, conSolar, ahorro: sinSolar - conSolar };
    });

    const tot = (k) => meses.reduce((s, x) => s + x[k], 0);
    const generado = tot('generado'), autoconsumo = tot('autoconsumo'), ahorroAnual = tot('ahorro');
    const capex = sup.capexFijo + sup.capexKwp * e.kwp;

    const flujo = [-capex];          // flujo acumulado nominal, año 0..N
    let vpn = -capex, payback = null;
    const flujos = [-capex];
    for (let t = 1; t <= sup.horizonte; t++) {
      const alza = Math.pow(1 + sup.alzaTarifa, t - 1);
      const f = ahorroAnual * Math.pow(1 - sup.degradacion, t - 1) * alza - capex * sup.omPct * alza;
      flujos.push(f);
      const antes = flujo[t - 1];
      flujo.push(antes + f);
      if (payback === null && antes + f >= 0) payback = t - 1 + (f > 0 ? -antes / f : 0);
      vpn += f / Math.pow(1 + sup.descuento, t);
    }

    return {
      kwp: e.kwp, capex, meses, flujo, vpn, payback, tir: tir(flujos),
      genMes: generado / 12,
      excedenteMes: (generado - autoconsumo) / 12,
      ahorroMes: ahorroAnual / 12,
      facturaSinSolar: tot('sinSolar') / 12,
      autoconsumoPct: generado > 0 ? autoconsumo / generado : 0,   // parte de lo generado que usa el hogar
      coberturaPct: e.consumo > 0 ? autoconsumo / (e.consumo * 12) : 0, // parte del consumo que cubre el sol
      viable: vpn > 0 && payback !== null,
      genDia: gen,
    };
  }

  /* Tasa interna de retorno por bisección; null si no existe en (-99 %, 100 %). */
  function tir(flujos) {
    const van = (r) => flujos.reduce((s, f, t) => s + f / Math.pow(1 + r, t), 0);
    let a = -0.99, b = 1;
    if (van(a) * van(b) > 0) return null;
    for (let i = 0; i < 60; i++) {
      const c = (a + b) / 2;
      if (van(a) * van(c) <= 0) b = c; else a = c;
    }
    return (a + b) / 2;
  }

  /* Promedio de una matriz [mes][hora] sobre un mes (0-11) o sobre el año (null). */
  function perfilHorario(matriz, mes) {
    if (mes !== null) return matriz[mes].slice();
    return matriz[0].map((_, h) => matriz.reduce((s, f, m) => s + f[h] * DIAS_MES[m], 0) / 365.25);
  }

  /* Distancia aproximada entre dos puntos (km), fórmula del semiverseno. */
  function distanciaKm(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180, dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(a));
  }

  /* Estación más cercana a un punto: { est, km }. */
  function masCercana(estaciones, lat, lon) {
    return estaciones.map((est) => ({ est, km: distanciaKm(lat, lon, est.lat, est.lon) }))
      .reduce((a, b) => (b.km < a.km ? b : a));
  }

  return { distanciaKm, masCercana, DIAS_MES, PERFILES, SUPUESTOS, SUBSIDIO, subsistencia, factura, generacion, simular, tir, perfilHorario };
});
