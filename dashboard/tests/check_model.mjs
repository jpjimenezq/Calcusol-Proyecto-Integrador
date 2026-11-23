// Verificación rápida del modelo con los datos reales: node tests/check_model.mjs
import { createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const C = require('../dashboard/js/model.js');
// Datos de Colombia; si no están, los de Antioquia de la versión anterior
const ruta = ['colombia', 'antioquia'].map((n) => new URL(`../dashboard/data/${n}.json`, import.meta.url)).find((u) => existsSync(u));
const datos = JSON.parse(readFileSync(ruta));
const horas = datos.meta.horas;
const base = { fuente: 'ml', consumo: 300, perfil: 'tipico', estrato: 4, sup: C.SUPUESTOS, horas };

// Perfiles de consumo normalizados
for (const p of Object.values(C.PERFILES)) assert.ok(Math.abs(p.horas.reduce((a, b) => a + b) - 1) < 1e-9);

// Factura con las tarifas de EPM: la de subsistencia hasta el consumo de subsistencia y la plena sobre él
const cerca = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} no es ${b}`);
const T = C.TARIFAS;
cerca(C.factura(100, T[4].subsistencia, T[4].plena, 1500), 99129);                       // estrato 4: todo a la plena
cerca(C.factura(200, T[1].subsistencia, T[1].plena, 1500), 130 * 427.04 + 70 * 991.29);  // estrato 1 a 1.500 m (130 kWh)
cerca(C.factura(200, T[1].subsistencia, T[1].plena, 500), 173 * 427.04 + 27 * 991.29);   // bajo 1.000 m son 173 kWh
cerca(C.factura(100, T[6].subsistencia, T[6].plena, 1500), 118954);                      // estrato 6
// Cada estrato paga distinto: la tarifa de subsistencia sube del estrato 1 al 5
assert.ok(T[1].subsistencia < T[2].subsistencia && T[2].subsistencia < T[3].subsistencia && T[3].subsistencia < T[5].subsistencia);

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
    // Colombia va de unos 800 kWh/kWp en el páramo nublado a cerca de 1.800 en La Guajira
    assert.ok(rendimiento > 700 && rendimiento < 2000, `rendimiento fuera de rango en ${est.nombre} (${est.departamento}): ${rendimiento}`);
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
// Con los datos reales, el punto de Rionegro (Antioquia) cae en la estación La Selva
if (!datos.meta.demo && datos.estaciones.some((e) => e.nombre === 'La Selva')) {
  assert.equal(C.masCercana(datos.estaciones, 6.15, -75.37).est.nombre, 'La Selva');
}
// Geografía: hay municipios y, con los datos de Colombia, cada uno apunta a un departamento con contorno
assert.ok(datos.municipios.length > 0);
if (datos.departamentos) {
  for (const m of datos.municipios) assert.ok(m.d >= 0 && m.d < datos.departamentos.length, `municipio sin departamento: ${m.n}`);
  for (const e of datos.estaciones) assert.ok(datos.departamentos.some((d) => d.n === e.departamento), `estación sin departamento en el mapa: ${e.nombre} (${e.departamento})`);
}

// Más tamaño => más generación y menor fracción autoconsumida
const est = datos.estaciones[0];
const [a, b] = [2, 5].map((kwp) => C.simular({ ...base, est, kwp }));
assert.ok(b.genMes > a.genMes && b.autoconsumoPct < a.autoconsumoPct);
// Un ahorro nulo no se recupera nunca
assert.equal(C.simular({ ...base, est, kwp: 3, consumo: 0, sup: { ...C.SUPUESTOS, precioBolsa: 0 } }).payback, null);
console.log('\nOK: todas las comprobaciones pasaron.');
