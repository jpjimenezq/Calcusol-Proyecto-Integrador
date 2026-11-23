// Verificación rápida del modelo con los datos reales: node tests/check_model.mjs
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const C = require('../dashboard/js/model.js');
const datos = JSON.parse(readFileSync(new URL('../dashboard/data/antioquia.json', import.meta.url)));
const horas = datos.meta.horas;
const base = { fuente: 'ml', consumo: 300, perfil: 'tipico', estrato: 4, sup: C.SUPUESTOS, horas };

// Perfiles de consumo normalizados
for (const p of Object.values(C.PERFILES)) assert.ok(Math.abs(p.horas.reduce((a, b) => a + b) - 1) < 1e-9);

// Factura: subsidio solo hasta la subsistencia; contribución en estratos altos
assert.equal(C.factura(100, 4, 900, 1500), 90000);
assert.equal(C.factura(200, 1, 900, 1500), 900 * (130 * 0.4 + 70));
assert.equal(C.factura(100, 6, 900, 1500), 108000);

console.log('Estación                   kWp  kWh/mes  autoc.  cobert.  ahorro/mes  payback    VPN (M$)  payback NASA');
for (const est of datos.estaciones) {
  for (const kwp of [2, 3, 5]) {
    const r = C.simular({ ...base, est, kwp });
    const n = C.simular({ ...base, est, kwp, fuente: 'nasa' });
    // Balance de energía y cotas físicas
    for (const m of r.meses) {
      assert.ok(Math.abs(m.generado - m.autoconsumo - m.excedente) < 1e-6);
      assert.ok(m.autoconsumo <= 300 + 1e-6 && m.importado >= -1e-6);
      assert.ok(m.ahorro <= m.sinSolar + m.excedente * C.SUPUESTOS.precioBolsa + 1e-6);
    }
    const rendimiento = (r.genMes * 12) / kwp;               // kWh/kWp/año
    assert.ok(rendimiento > 1000 && rendimiento < 1700, `rendimiento fuera de rango: ${rendimiento}`);
    assert.ok(r.autoconsumoPct > 0 && r.autoconsumoPct <= 1);
    assert.ok(r.payback === null || (r.payback > 2 && r.payback < 25));
    console.log(
      est.nombre.padEnd(26), String(kwp).padStart(3), r.genMes.toFixed(0).padStart(8),
      (r.autoconsumoPct * 100).toFixed(0).padStart(6) + '%', (r.coberturaPct * 100).toFixed(0).padStart(7) + '%',
      Math.round(r.ahorroMes).toLocaleString('es-CO').padStart(11), (r.payback?.toFixed(1) ?? '—').padStart(8),
      (r.vpn / 1e6).toFixed(1).padStart(11), (n.payback?.toFixed(1) ?? '—').padStart(13));
  }
}

// Distancias: Medellín–Rionegro ronda los 25–30 km y cada estación es la más cercana a sí misma
const dMR = C.distanciaKm(6.2476, -75.5658, 6.1552, -75.3737);
assert.ok(dMR > 20 && dMR < 30, `distancia Medellín–Rionegro: ${dMR}`);
for (const e of datos.estaciones) {
  const c = C.masCercana(datos.estaciones, e.lat, e.lon);
  assert.ok(c.est.id === e.id && c.km < 0.01);
}
assert.equal(C.masCercana(datos.estaciones, 6.15, -75.37).est.nombre, 'La Selva');   // Rionegro
assert.equal(datos.municipios.length, 125);

// Más tamaño => más generación y menor fracción autoconsumida
const est = datos.estaciones[0];
const [a, b] = [2, 5].map((kwp) => C.simular({ ...base, est, kwp }));
assert.ok(b.genMes > a.genMes && b.autoconsumoPct < a.autoconsumoPct);
// Un ahorro nulo no se recupera nunca
assert.equal(C.simular({ ...base, est, kwp: 3, consumo: 0, sup: { ...C.SUPUESTOS, precioBolsa: 0 } }).payback, null);
console.log('\nOK: todas las comprobaciones pasaron.');
