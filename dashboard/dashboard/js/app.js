/* Estado, controles y composición del dashboard. */
(function () {
  'use strict';
  const M = window.Calcusol, G = window.Graficos, C = G.C;
  const $ = (id) => document.getElementById(id);

  const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
  const MES_C = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
  const ESC = [
    { id: 'pequeno', nombre: 'Pequeño', color: C.azulClaro },
    { id: 'mediano', nombre: 'Mediano', color: C.azul },
    { id: 'maximo', nombre: 'Máximo', color: C.marino },
  ];
  const ESTACION_INICIAL = 'Aeropuerto Olaya Herrera';

  /* Supuestos editables: [clave, etiqueta, factor de presentación, paso] */
  const CAMPOS = [
    ['kwp0', 'Sistema pequeño (kWp)', 1, 0.5], ['kwp1', 'Sistema mediano (kWp)', 1, 0.5], ['kwp2', 'Sistema máximo (kWp)', 1, 0.5],
    ['capexFijo', 'Costo fijo de instalación ($ M)', 1e-6, 0.1], ['capexKwp', 'Costo por kWp ($ M)', 1e-6, 0.1],
    ['comercializacion', 'Comercialización ($/kWh)', 1, 5], ['precioBolsa', 'Precio de bolsa ($/kWh)', 1, 10],
    ['alzaTarifa', 'Alza anual de la tarifa (%)', 100, 0.5], ['descuento', 'Tasa de descuento (%)', 100, 0.5],
    ['omPct', 'Mantenimiento anual (% del costo)', 100, 0.1], ['degradacion', 'Degradación anual (%)', 100, 0.1],
    ['perdidas', 'Pérdidas del sistema (%)', 100, 1], ['horizonte', 'Vida útil evaluada (años)', 1, 1],
  ];

  let datos = null;
  const inicial = () => ({
    estacion: null, punto: null, consumo: 300, estrato: 4, perfil: 'tipico', mes: null, escenario: 1,
    kwp: [2, 3, 5], sup: { ...M.SUPUESTOS },
  });
  let st = inicial();

  /* ---------- Formatos (es-CO) ---------- */
  const nf = (d) => new Intl.NumberFormat('es-CO', { minimumFractionDigits: d, maximumFractionDigits: d });
  const n0 = (v) => nf(0).format(v), n1 = (v) => nf(1).format(v);
  const pct = (v, d = 0) => nf(d).format(v * 100) + ' %';
  const pctS = (v, d = 1) => (v > 0 ? '+' : v < 0 ? '−' : '') + nf(d).format(Math.abs(v) * 100) + ' %';
  const pesos = (v) => (v < 0 ? '−' : '') + '$ ' + n0(Math.abs(v));
  const pesosC = (v) => {
    const a = Math.abs(v), sg = v < 0 ? '−' : '';
    if (a >= 1e6) return `${sg}$ ${n1(a / 1e6)} M`;
    if (a >= 1e3) return `${sg}$ ${n0(a / 1e3)} mil`;
    return `${sg}$ ${n0(a)}`;
  };
  const anios = (v) => (v === null ? 'No se recupera' : n1(v));

  /* ---------- DOM ---------- */
  function el(tag, cls, texto) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (texto !== undefined) n.textContent = texto;
    return n;
  }
  function opciones(sel, lista) {
    sel.textContent = '';
    lista.forEach(([v, t]) => { const o = el('option', null, t); o.value = v; sel.appendChild(o); });
  }
  function leyenda(id, items) {
    const c = $(id); c.textContent = '';
    items.forEach((it) => {
      const sp = el('span'), i = el('i', it.tipo || '');
      i.style.background = it.color; i.style.borderColor = it.color;
      sp.append(i, it.texto); c.appendChild(sp);
    });
  }
  function tarjetas(id, items) {
    const c = $(id); c.textContent = '';
    items.forEach((it) => {
      const t = el('div', 'tarjeta' + (it.oscura ? ' oscura' : '')), cab = el('div', 'tarjeta-c');
      cab.append(el('span', 'tarjeta-t', it.titulo), el('span', 'tarjeta-v', it.valor));
      t.append(cab); c.appendChild(t);
    });
  }

  /* ---------- Controles ---------- */
  function montarControles() {
    opciones($('f-estacion'), datos.estaciones.map((e) => [e.id, `${e.nombre} · ${n0(e.alt)} m`]));
    opciones($('f-estrato'), [1, 2, 3, 4, 5, 6].map((e) => [e, `Estrato ${e}`]));
    opciones($('f-perfil'), Object.entries(M.PERFILES).map(([k, p]) => [k, p.nombre]));
    opciones($('f-mes'), [['', 'Promedio del año'], ...MESES.map((m, i) => [i, m])]);

    const seg = $('f-escenario');
    ESC.forEach((e, i) => {
      const b = el('button'); b.type = 'button'; b.dataset.i = i;
      b.addEventListener('click', () => { st.escenario = i; render(); });
      seg.appendChild(b);
    });

    const grid = $('supuestos-grid');
    CAMPOS.forEach(([k, etiqueta, , paso]) => {
      const l = el('label', 'campo', etiqueta), inp = el('input');
      inp.type = 'number'; inp.step = paso; inp.min = 0; inp.id = 's-' + k;
      inp.addEventListener('change', () => leerSupuesto(k, inp));
      l.appendChild(inp); grid.appendChild(l);
    });

    $('f-estacion').addEventListener('change', (e) => { st.estacion = +e.target.value; st.punto = null; render(); });
    $('f-estrato').addEventListener('change', (e) => { st.estrato = +e.target.value; render(); });
    $('f-perfil').addEventListener('change', (e) => { st.perfil = e.target.value; render(); });
    $('f-mes').addEventListener('change', (e) => { st.mes = e.target.value === '' ? null : +e.target.value; render(); });
    $('f-consumo').addEventListener('change', (e) => { st.consumo = acotar(+e.target.value, 30, 3000, 300); render(); });
    $('f-tarifa').addEventListener('change', (e) => { st.sup.tarifa = acotar(+e.target.value, 100, 3000, M.SUPUESTOS.tarifa); render(); });
    $('btn-reset').addEventListener('click', () => {
      const { estacion, punto, consumo, estrato, perfil, mes, escenario } = st;
      st = { ...inicial(), estacion, punto, consumo, estrato, perfil, mes, escenario };
      render();
    });
  }
  const acotar = (v, a, b, def) => (Number.isFinite(v) ? Math.min(b, Math.max(a, v)) : def);

  function leerSupuesto(k, inp) {
    const campo = CAMPOS.find((c) => c[0] === k);
    const v = +inp.value / campo[2];
    if (!Number.isFinite(v) || v < 0) return render();
    if (k.startsWith('kwp')) st.kwp[+k[3]] = acotar(v, 0.5, 50, 3);
    else if (k === 'horizonte') st.sup.horizonte = Math.round(acotar(v, 5, 40, 25));
    else if (k === 'perdidas') st.sup.perdidas = acotar(v, 0, 0.6, 0.14);
    else st.sup[k] = v;
    render();
  }

  function pintarControles() {
    $('f-estacion').value = st.estacion; $('f-estrato').value = st.estrato; $('f-perfil').value = st.perfil;
    $('f-mes').value = st.mes === null ? '' : st.mes;
    $('f-consumo').value = st.consumo; $('f-tarifa').value = st.sup.tarifa;
    [...$('f-escenario').children].forEach((b, i) => {
      b.textContent = `${ESC[i].nombre} · ${n1(st.kwp[i]).replace(',0', '')} kWp`;
      b.setAttribute('aria-pressed', i === st.escenario ? 'true' : 'false');
    });
    CAMPOS.forEach(([k, , f]) => {
      const v = k.startsWith('kwp') ? st.kwp[+k[3]] : st.sup[k];
      $('s-' + k).value = Math.round(v * f * 1000) / 1000;
    });
  }

  /* ---------- Render ---------- */
  function render() {
    pintarControles();
    const est = datos.estaciones.find((e) => e.id === st.estacion);
    const horas = datos.meta.horas;
    const base = { est, consumo: st.consumo, perfil: st.perfil, estrato: st.estrato, sup: st.sup, horas };
    const esc = ESC.map((e, i) => ({
      ...e, i, kwp: st.kwp[i], etiqueta: `${e.nombre} · ${n1(st.kwp[i]).replace(',0', '')} kWp`,
      ml: M.simular({ ...base, fuente: 'ml', kwp: st.kwp[i] }),
      nasa: M.simular({ ...base, fuente: 'nasa', kwp: st.kwp[i] }),
    }));
    const act = esc[st.escenario], r = act.ml;
    const conPayback = esc.filter((e) => e.ml.payback !== null);
    const mejor = conPayback.length ? conPayback.reduce((a, b) => (b.ml.payback < a.ml.payback ? b : a)) : null;
    const mesTxt = st.mes === null ? 'Promedio del año' : MESES[st.mes];

    respuesta(act, r, est, mesTxt);
    energia(act, r, mesTxt);
    rentabilidad(esc, act, mejor);
    notas(est);
  }

  /* Radiación corregida del día promedio (kWh/m²) en el mes elegido o en el año. */
  const radiacionDia = (e) => M.perfilHorario(e.clim.ml, st.mes).reduce((a, b) => a + b, 0) / 1000;

  function respuesta(act, r, est, mesTxt) {
    $('t-respuesta').textContent = r.viable
      ? `Sí es rentable: se recupera en ${n1(r.payback)} años`
      : r.payback !== null
        ? `Se recupera en ${n1(r.payback)} años, pero no rinde el ${pct(st.sup.descuento)} exigido`
        : `No es rentable: no se recupera en ${st.sup.horizonte} años`;

    $('t-mapa').textContent = `Radiación solar · ${mesTxt.toLowerCase()}`;
    const km = (v) => `≈ ${n0(v)} km`;
    $('pista-mapa').textContent = st.punto
      ? `Tu ubicación está a ${km(M.distanciaKm(st.punto.lat, st.punto.lon, est.lat, est.lon))} de la estación`
      : 'Haz clic en tu ubicación · rueda o botones + − para acercar';
    G.mapa($('g-mapa'), {
      desc: 'Mapa de calor de la radiación solar corregida en Antioquia, con municipios, la ubicación elegida y la estación más cercana',
      limite: datos.limite, municipios: datos.municipios, unidad: 'kWh/m² al día', fmt: (v) => nf(2).format(v),
      puntos: datos.estaciones.map((e) => ({ id: e.id, nombre: e.nombre, lon: e.lon, lat: e.lat, valor: radiacionDia(e), sel: e.id === est.id, e })),
      punto: st.punto && { ...st.punto, texto: km(M.distanciaKm(st.punto.lat, st.punto.lon, est.lat, est.lon)) },
      tip: (lon, lat, aqui, municipio) => {
        const c = M.masCercana(datos.estaciones, lat, lon);
        return { titulo: municipio || 'Antioquia', filas: [
          { label: 'Radiación aquí', value: `${nf(2).format(aqui)} kWh/m²` },
          { label: 'Estación más cercana', value: c.est.nombre },
          { label: 'Distancia', value: km(c.km) },
        ] };
      },
      tipEstacion: (p) => ({ titulo: `Estación ${p.nombre}`, filas: [
        { label: 'Radiación', value: `${nf(2).format(p.valor)} kWh/m²` },
        { label: 'Sesgo de NASA', value: pctS(p.e.sesgo_nasa) },
        { label: 'Altitud', value: `${n0(p.e.alt)} m` },
      ] }),
      onPunto: (lon, lat) => { st.punto = { lon, lat }; st.estacion = M.masCercana(datos.estaciones, lat, lon).est.id; render(); },
      onClic: (p) => { st.estacion = p.id; st.punto = null; render(); },
    });

    // De mayor a menor importancia; el título dice qué es cada cifra sin más contexto
    const kpis = [
      { t: 'Recuperación de la inversión', v: r.payback === null ? '—' : n1(r.payback), u: r.payback === null ? '' : 'años', estado: r.viable },
      { t: 'Consumo del hogar cubierto por el sol', v: pct(r.coberturaPct) },
      { t: 'Ahorro en la factura de energía', v: pesosC(r.ahorroMes), u: 'al mes' },
      { t: 'Energía que genera el sistema', v: n0(r.genMes), u: 'kWh al mes' },
      { t: `Ganancia neta en ${st.sup.horizonte} años, a valor de hoy`, v: pesosC(r.vpn) },
    ];
    const c = $('kpis'); c.textContent = '';
    kpis.forEach((k, i) => {
      const d = el('div', `kpi n${i + 1}`);
      const v = el('div', 'kpi-v', k.v); if (k.u) v.appendChild(el('small', null, k.u));
      d.append(el('div', 'kpi-t', k.t), v);
      if (k.estado !== undefined) {
        const e = el('span', 'estado');
        e.innerHTML = k.estado
          ? '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="#1e9e5a"/><path d="M4.5 8.2l2.3 2.3 4.7-4.9" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
          : '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="#e87b7b"/><path d="M5.2 5.2l5.6 5.6M10.8 5.2l-5.6 5.6" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>';
        e.appendChild(document.createTextNode(k.estado ? 'Inversión viable' : 'Inversión no viable'));
        d.appendChild(e);
      }
      c.appendChild(d);
    });
  }

  function energia(act, r, mesTxt) {
    const genML = r.meses.map((m) => m.generado), genNASA = act.nasa.meses.map((m) => m.generado);
    const exceso = genNASA.map((v, i) => Math.max(0, v - genML[i]));

    $('t-energia').textContent = `Genera ${n0(r.genMes)} kWh/mes y cubre el ${pct(r.coberturaPct)} del consumo`;
    leyenda('ley-mensual', [
      { color: C.azul, texto: 'Corregida' }, { color: C.coralClaro, texto: 'Exceso de NASA sin corregir' },
      { color: C.marino, texto: `Consumo (${n0(st.consumo)} kWh)`, tipo: 'linea' },
    ]);
    G.columnas($('g-mensual'), {
      desc: 'Energía generada por mes con radiación corregida y con NASA sin corregir',
      cats: MES_C, yFmt: n0, destacado: st.mes === null ? -1 : st.mes, ejeX: 'Mes', ejeY: 'Energía (kWh)',
      pilas: [
        { valores: genML, colorEn: (i) => (i === st.mes ? C.marino : C.azul) },
        { valores: exceso, color: C.coralClaro },
      ],
      ref: { valor: st.consumo },
      tip: (i) => ({ titulo: MESES[i], filas: [
        { color: C.azul, label: 'Generación corregida', value: `${n0(genML[i])} kWh` },
        { color: C.coralClaro, label: 'Con NASA sin corregir', value: `${n0(genNASA[i])} kWh` },
        { label: 'Autoconsumo', value: `${n0(r.meses[i].autoconsumo)} kWh` },
        { label: 'Excedente a la red', value: `${n0(r.meses[i].excedente)} kWh` },
        { label: 'Ahorro del mes', value: pesos(Math.round(r.meses[i].ahorro)) },
      ] }),
    });

    // Balance del día promedio: kW medios por hora
    const perfil = M.PERFILES[st.perfil].horas;
    const dias = st.mes === null ? 365.25 / 12 : M.DIAS_MES[st.mes];
    const carga = perfil.map((f) => (st.consumo / dias) * f);
    const gen = M.perfilHorario(r.genDia, st.mes);
    const auto = gen.map((v, h) => Math.min(v, carga[h])), exc = gen.map((v, h) => v - auto[h]);
    $('t-balance').textContent = `Día promedio · ${mesTxt.toLowerCase()}`;
    leyenda('ley-balance', [
      { color: C.azul, texto: 'Autoconsumo' }, { color: C.verde, texto: 'Excedente a la red' },
      { color: C.marino, texto: 'Consumo', tipo: 'linea solida' },
    ]);
    G.columnas($('g-balance'), {
      desc: 'Generación autoconsumida, excedente y consumo del hogar por hora del día',
      ejeX: 'Hora del día', ejeY: 'Energía (kWh por hora)',
      cats: perfil.map((_, h) => `${h} h`), cada: 3, yFmt: (v) => nf(v < 1 && v > 0 ? 1 : (Number.isInteger(v) ? 0 : 1)).format(v),
      pilas: [{ valores: auto, color: C.azul }, { valores: exc, color: C.verde }],
      linea: { valores: carga, color: C.marino },
      tip: (h) => ({ titulo: `${h}:00 a ${h + 1}:00`, filas: [
        { label: 'Consumo', value: `${nf(2).format(carga[h])} kWh` },
        { label: 'Generación', value: `${nf(2).format(gen[h])} kWh` },
        { color: C.azul, label: 'Autoconsumo', value: `${nf(2).format(auto[h])} kWh` },
        { color: C.verde, label: 'Excedente', value: `${nf(2).format(exc[h])} kWh` },
      ] }),
    });

    tarjetas('tar-energia', [
      { titulo: 'Autoconsumo', valor: pct(r.autoconsumoPct) },
      { titulo: 'Excedente a la red', valor: `${n0(r.excedenteMes)} kWh/mes` },
      { titulo: 'Sin corregir (NASA)', valor: `${n0(act.nasa.genMes)} kWh/mes`, oscura: true },
    ]);
  }

  function rentabilidad(esc, act, mejor) {
    $('t-rentabilidad').textContent = !esc.some((e) => e.ml.viable)
      ? 'Ningún tamaño es rentable: el valor presente neto es negativo'
      : mejor
      ? `El sistema ${mejor.nombre.toLowerCase()} se recupera antes: ${n1(mejor.ml.payback)} años`
      : `Ningún tamaño se recupera en ${st.sup.horizonte} años`;

    const cont = $('escenarios'); cont.textContent = '';
    esc.forEach((e) => {
      const r = e.ml, b = el('button', 'escenario' + (mejor && e.i === mejor.i ? ' mejor' : ''));
      b.type = 'button'; b.setAttribute('aria-pressed', e.i === st.escenario ? 'true' : 'false');
      const cab = el('div', 'esc-n'); cab.appendChild(el('span', null, e.etiqueta));
      if (e.i === st.escenario) cab.appendChild(el('em', null, 'En análisis'));
      const l1 = el('div', 'esc-l'); l1.append(el('span', null, r.payback === null ? 'no se recupera' : 'años'), el('span', null, `Ganancia neta ${pesosC(r.vpn)}`));
      const b1 = el('div', 'barra'), i1 = el('i'); i1.style.width = (r.payback === null ? 100 : Math.min(100, r.payback / st.sup.horizonte * 100 * 2)) + '%'; b1.appendChild(i1);
      const l2 = el('div', 'esc-l'); l2.append(el('span', null, `${pct(r.autoconsumoPct)} autoconsumo`), el('span', null, `Ahorro ${pesosC(r.ahorroMes)}/mes`));
      const b2 = el('div', 'barra'), i2 = el('i', 'b2'); i2.style.width = r.autoconsumoPct * 100 + '%'; b2.appendChild(i2);
      b.append(cab, el('div', 'esc-v', r.payback === null ? '—' : n1(r.payback)), l1, b1, l2, b2);
      b.addEventListener('click', () => { st.escenario = e.i; render(); });
      cont.appendChild(b);
    });

    leyenda('ley-payback', [{ color: C.coral, texto: 'NASA sin corregir', tipo: 'punto' }, { color: C.verde, texto: 'Corregido', tipo: 'punto' }]);
    G.dumbbell($('g-payback'), {
      desc: 'Años de recuperación con NASA sin corregir y con radiación corregida, por tamaño de sistema',
      filas: esc.map((e) => ({ label: e.etiqueta, a: e.nasa.payback, b: e.ml.payback, sel: e.i === st.escenario })),
      ejeX: 'Años de recuperación', ejeY: 'Tamaño del sistema',
      fmt: n1, fmtEje: (v) => nf(Number.isInteger(v) ? 0 : 1).format(v), nombreA: 'NASA sin corregir', nombreB: 'Corregido con ML', sinValor: 'No se recupera',
    });

    leyenda('ley-flujo', esc.map((e) => ({ color: e.color, texto: e.etiqueta })));
    G.lineas($('g-flujo'), {
      desc: 'Flujo de caja acumulado por año para los tres tamaños de sistema',
      ejeX: 'Año', ejeY: 'Flujo acumulado ($ millones)',
      x: act.ml.flujo.map((_, i) => i),
      series: esc.map((e) => ({ nombre: e.nombre, color: e.color, valores: e.ml.flujo, sel: e.i === st.escenario })),
      yFmt: (v) => `${v < 0 ? '−' : ''}${nf(0).format(Math.abs(v) / 1e6)}`,
      xFmt: (v, largo) => (largo ? `Año ${v}` : String(v)), tipFmt: pesosC,
    });

    const filas = [
      ['Inversión', (e) => pesosC(e.ml.capex)],
      ['Energía generada (kWh/mes)', (e) => n0(e.ml.genMes)],
      ['Energía con NASA sin corregir (kWh/mes)', (e) => n0(e.nasa.genMes)],
      ['Autoconsumo (% de lo generado)', (e) => pct(e.ml.autoconsumoPct)],
      ['Consumo cubierto', (e) => pct(e.ml.coberturaPct)],
      ['Excedente a la red (kWh/mes)', (e) => n0(e.ml.excedenteMes)],
      ['Ahorro en la factura ($/mes)', (e) => pesos(Math.round(e.ml.ahorroMes))],
      ['Recuperación (años)', (e) => anios(e.ml.payback)],
      ['Recuperación con NASA sin corregir (años)', (e) => anios(e.nasa.payback)],
      [`Ganancia neta a valor de hoy (VPN, ${st.sup.horizonte} años)`, (e) => pesosC(e.ml.vpn)],
      ['Tasa interna de retorno', (e) => (e.ml.tir === null ? '—' : pct(e.ml.tir, 1))],
    ];
    const t = $('tabla'); t.textContent = '';
    const th = t.createTHead().insertRow(); th.appendChild(el('th', null, 'Indicador'));
    esc.forEach((e) => th.appendChild(el('th', null, e.etiqueta)));
    const tb = t.createTBody();
    filas.forEach(([nombre, f]) => {
      const tr = tb.insertRow(); const h = el('th', null, nombre); h.scope = 'row'; tr.appendChild(h);
      esc.forEach((e) => tr.appendChild(el('td', null, f(e))));
    });
  }

  function notas(est) {
    const m = datos.meta, c = $('notas'); c.textContent = '';
    [
      `Datos: ${n0(m.registros)} registros horarios de ${m.estaciones} estaciones del IDEAM en Antioquia (${m.desde}–${m.hasta}) cruzados con NASA POWER. ${est.nombre}: ${n0(est.registros)} registros entre ${est.desde} y ${est.hasta}, a ${n0(est.alt)} m.`,
      'Mapa: la radiación entre estaciones se interpola por distancia y es orientativa. Al marcar una ubicación se usa la estación más cercana en línea recta; la distancia es aproximada. Contorno del departamento: geoBoundaries / OpenStreetMap (ODbL). Límites municipales: geoBoundaries / DANE (CC BY 4.0).',
      'Radiación corregida: predicción del modelo XGBoost de Calcusol sobre las variables de NASA POWER. El error se midió sobre los mismos registros históricos; no es una validación independiente.',
      `Energía: radiación horizontal del día promedio de cada mes, corrección por temperatura de celda y ${pct(st.sup.perdidas)} de pérdidas. El balance con el día promedio tiende a sobrestimar un poco el autoconsumo.`,
      'Finanzas: la tarifa, los costos de instalación, el precio de bolsa y las tasas son valores de referencia editables en «Supuestos técnicos y financieros», no datos medidos. El subsidio (estratos 1 a 3) aplica hasta el consumo de subsistencia y la contribución (5 y 6) es del 20 %. Use la tarifa de su factura para un resultado ajustado a su hogar.',
    ].forEach((p) => c.appendChild(el('p', null, p)));
  }

  /* Estado inicial desde la URL, para compartir un caso:
   * ?lat=6.15&lon=-75.37&consumo=250&estrato=3&tarifa=950&perfil=diurno&mes=7&escenario=1 */
  function leerUrl() {
    const q = new URLSearchParams(location.search), num = (k) => (q.has(k) ? +q.get(k) : NaN);
    if (datos.estaciones.some((e) => e.id === num('estacion'))) st.estacion = num('estacion');
    if (num('lat') > 5 && num('lat') < 9 && num('lon') > -77.5 && num('lon') < -73.5) {
      st.punto = { lat: num('lat'), lon: num('lon') };
      st.estacion = M.masCercana(datos.estaciones, st.punto.lat, st.punto.lon).est.id;
    }
    if (num('consumo') > 0) st.consumo = acotar(num('consumo'), 30, 3000, 300);
    if (num('estrato') in M.SUBSIDIO) st.estrato = num('estrato');
    if (num('tarifa') > 0) st.sup.tarifa = acotar(num('tarifa'), 100, 3000, M.SUPUESTOS.tarifa);
    if (q.get('perfil') in M.PERFILES) st.perfil = q.get('perfil');
    if (num('mes') >= 1 && num('mes') <= 12) st.mes = Math.round(num('mes')) - 1;
    if ([1, 2, 3].includes(num('escenario'))) st.escenario = num('escenario') - 1;
  }

  /* ---------- Arranque ---------- */
  fetch('data/antioquia.json')
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => {
      datos = d;
      const m = d.meta;
      $('meta-datos').textContent = `${n0(m.registros)} registros horarios del IDEAM y NASA POWER · ${m.estaciones} estaciones · ${m.desde}–${m.hasta}`;
      st.estacion = (d.estaciones.find((e) => e.nombre === ESTACION_INICIAL) || d.estaciones[0]).id;
      leerUrl();
      montarControles();
      render();
      let pend = 0, ancho = innerWidth;
      addEventListener('resize', () => {
        if (innerWidth === ancho) return;
        ancho = innerWidth; cancelAnimationFrame(pend); pend = requestAnimationFrame(render);
      });
    })
    .catch((e) => {
      $('meta-datos').textContent = 'No se pudieron cargar los datos';
      $('t-respuesta').textContent = 'No se pudieron cargar los datos. Ejecute «make data» y abra el dashboard con «make serve».';
      console.error(e);
    });
})();
