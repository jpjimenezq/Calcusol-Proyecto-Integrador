/* Estado, controles y composición del dashboard. */
(function () {
  'use strict';
  const M = window.Calcusol, G = window.Graficos, C = G.C, API = window.CalcusolAPI;
  const $ = (id) => document.getElementById(id);

  const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
  const MES_C = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
  const ESC = [
    { id: 'pequeno', nombre: 'Pequeño', color: C.azulClaro },
    { id: 'mediano', nombre: 'Mediano', color: C.azul },
    { id: 'maximo', nombre: 'Máximo', color: C.marino },
  ];
  const ESTACION_INICIAL = 'Aeropuerto Olaya Herrera';

  /* Supuestos editables: [clave, etiqueta, factor de presentación, paso]. Sus valores por defecto son los de la ficha de R3. */
  const CAMPOS = [
    ['kwp0', 'Sistema pequeño (kWp)', 1, 0.5], ['kwp1', 'Sistema mediano (kWp)', 1, 0.5], ['kwp2', 'Sistema máximo (kWp)', 1, 0.5],
    ['capexFijo', 'Costo fijo de instalación ($ M)', 1e-6, 0.1], ['capexKwp', 'Costo por kWp ($ M)', 1e-6, 0.1],
    ['comercializacion', 'Comercialización ($/kWh)', 1, 'any'],
    ['precioBolsa', 'Precio de bolsa ($/kWh)', 1, 'any'],
    ['alzaTarifa', 'Alza anual de la tarifa (%)', 100, 0.5], ['descuento', 'Tasa de descuento (%)', 100, 0.5],
    ['omPct', 'Mantenimiento anual (% del costo)', 100, 0.1],
    ['degradacion', 'Degradación anual (%)', 100, 0.1], ['degradacionInicial', 'Degradación del primer año (%)', 100, 0.1],
    ['perdidas', 'Pérdidas del sistema (%)', 100, 1], ['eficiencia', 'Eficiencia del inversor (%)', 100, 1],
    ['anioInversor', 'Año de reemplazo del inversor', 1, 1], ['costoInversor', 'Costo del inversor ($ M)', 1e-6, 0.05],
    ['horizonte', 'Vida útil evaluada (años)', 1, 1],
  ];

  let datos = null;
  /* Valores por defecto: los de model.js hasta que la API entregue la ficha de R3 (GET /v1/parametros). */
  let D = { estrato: M.HOGAR.estrato, consumo: M.HOGAR.consumo, kwp: [...M.HOGAR.kwp], sup: { ...M.SUPUESTOS } };
  let TARIFAS = M.TARIFAS;        // tarifas por estrato: { 1: { subsistencia, plena }, ... }
  const tarifasDe = (estrato) => TARIFAS[estrato] || M.TARIFAS[estrato];
  const inicial = () => {
    const t = tarifasDe(D.estrato);
    return {
      estacion: null, departamento: null, punto: null, consumo: D.consumo, estrato: D.estrato, perfil: 'tipico', mes: null, escenario: 1,
      kwp: [...D.kwp], sup: { ...D.sup, tarifaSub: t.subsistencia, tarifaPlena: t.plena },
    };
  };
  let st = inicial();
  /* Al cambiar el estrato, las tarifas pasan a ser las de ese estrato en la ficha. */
  function aplicarTarifas() {
    const t = tarifasDe(st.estrato);
    st.sup.tarifaSub = t.subsistencia; st.sup.tarifaPlena = t.plena;
  }
  /* Toma los valores por defecto de la ficha de R3 que entrega la API. */
  function aplicarParametros(p) {
    const f = p.finanzas, s = p.sistema, sens = (p.sensibilidad && p.sensibilidad.kWp) || [];
    TARIFAS = Object.fromEntries(Object.entries(p.tarifas_cop_kwh).map(([k, t]) => [+k, t]));
    D = {
      estrato: p.hogar.estrato, consumo: p.hogar.kwh_mes,
      kwp: sens.length ? [Math.min(...sens), p.hogar.kwp, Math.max(...sens)] : [...M.HOGAR.kwp],
      sup: {
        ...M.SUPUESTOS,
        comercializacion: f.cargo_comercializacion_cop_kwh, precioBolsa: f.precio_excedente_cop_kwh, capexKwp: f.costo_por_kwp_cop,
        omPct: f.mantenimiento_anual_pct_capex, degradacion: s.degradacion_anual, degradacionInicial: s.degradacion_primer_anio,
        alzaTarifa: f.incremento_real_tarifa_anual, descuento: f.tasa_descuento, horizonte: f.horizonte_anios,
        perdidas: s.perdidas, eficiencia: s.eficiencia_inversor, anioInversor: f.anio_reemplazo_inversor,
        costoInversor: f.costo_reemplazo_inversor_cop, gamma: s.coef_temp_potencia_por_c, noct: s.noct_c,
      },
    };
  }

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

  /* ---------- Departamento y estación ---------- */
  const territorio = () => st.departamento || datos.meta.departamento || 'Colombia';
  const deptosConEstacion = () => [...new Set(datos.estaciones.map((e) => e.departamento).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'es'));
  const estacionesVisibles = () => datos.estaciones.filter((e) => !st.departamento || e.departamento === st.departamento);
  let firmaEstaciones = null;
  function opcionesEstacion() {
    const firma = st.departamento || '';
    if (firma === firmaEstaciones) return;
    firmaEstaciones = firma;
    opciones($('f-estacion'), estacionesVisibles().map((e) =>
      [e.id, `${e.nombre}${st.departamento || !e.departamento || e.nombre === e.departamento ? '' : ` (${e.departamento})`} · ${n0(e.alt)} m`]));
  }
  /* Elige una estación. Con un departamento filtrado, si la estación es de otro, el filtro pasa a ese departamento. */
  function elegirEstacion(id) {
    st.estacion = id;
    const e = datos.estaciones.find((x) => x.id === id);
    if (st.departamento && e && e.departamento && e.departamento !== st.departamento) st.departamento = e.departamento;
  }
  /* Caja del departamento elegido, para que el mapa se acerque a él. */
  function enfoque() {
    const d = st.departamento && datos.departamentos && datos.departamentos.find((x) => x.n === st.departamento);
    if (!d) return null;
    // San Andrés y Providencia no están en el mapa continental: se ven en sus recuadros, no hay a dónde acercar
    if ((datos.islas || []).some((i) => datos.departamentos[i.d] === d)) return null;
    const xs = d.p.map((q) => q[0]), ys = d.p.map((q) => q[1]);
    return { clave: st.departamento, lon0: Math.min(...xs), lon1: Math.max(...xs), lat0: Math.min(...ys), lat1: Math.max(...ys) };
  }

  /* ---------- Ubicación del hogar: dirección, coordenadas o ubicación del navegador ---------- */
  const EN_COLOMBIA = (lat, lon) => lat > -5 && lat < 14 && lon > -82 && lon < -66;
  const recorte = (t, n = 90) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);
  function mensajeUbic(texto, error) {
    const p = $('ubic-estado');
    p.textContent = texto; p.classList.toggle('error', !!error);
  }
  /* Dice dónde quedó el hogar y a qué estación corresponde. */
  function describirPunto() {
    if (!st.punto) return;
    const c = M.masCercana(datos.estaciones, st.punto.lat, st.punto.lon);
    mensajeUbic(`${st.punto.etiqueta ? `${st.punto.etiqueta} · ` : ''}${st.punto.lat.toFixed(4)}, ${st.punto.lon.toFixed(4)} · a ${n0(c.km)} km de la estación ${c.est.nombre}`);
  }
  /* Marca el hogar en el mapa y elige la estación más cercana en línea recta. */
  function ubicarHogar(lat, lon, etiqueta) {
    if (!EN_COLOMBIA(lat, lon)) { mensajeUbic('Esa ubicación está fuera de Colombia.', true); return false; }
    st.punto = { lat, lon, etiqueta };
    elegirEstacion(M.masCercana(datos.estaciones, lat, lon).est.id);
    describirPunto();
    try {          // la ubicación queda en la dirección de la página, para compartir el caso
      const u = new URL(location.href);
      u.searchParams.set('lat', lat.toFixed(4)); u.searchParams.set('lon', lon.toFixed(4)); u.searchParams.delete('estacion');
      history.replaceState(null, '', u);
    } catch (e) { /* sin historial: no importa */ }
    render();
    return true;
  }
  /* "4.65, -74.06", "4,65 -74,06" o "-74.06, 4.65" (se reconoce cuál es la latitud). */
  function leerCoordenadas(texto) {
    const m = texto.trim().match(/^(-?\d+(?:[.,]\d+)?)\s*(?:[;,]|\s)\s*(-?\d+(?:[.,]\d+)?)$/);
    if (!m) return null;
    let a = +m[1].replace(',', '.'), b = +m[2].replace(',', '.');
    if (EN_COLOMBIA(b, a) && !EN_COLOMBIA(a, b)) [a, b] = [b, a];
    return Number.isFinite(a) && Number.isFinite(b) ? { lat: a, lon: b } : null;
  }
  /* Direcciones con Nominatim (OpenStreetMap): solo al pulsar Buscar, no mientras se escribe. */
  async function consultarNominatim(texto) {
    const q = new URLSearchParams({ q: texto, format: 'jsonv2', countrycodes: 'co', limit: '5', 'accept-language': 'es' });
    const r = await fetch(`https://nominatim.openstreetmap.org/search?${q}`);
    if (!r.ok) throw new Error(`Nominatim ${r.status}`);
    return (await r.json()).map((x) => ({ lat: +x.lat, lon: +x.lon, nombre: x.display_name })).filter((x) => EN_COLOMBIA(x.lat, x.lon));
  }
  /* OpenStreetMap casi nunca tiene el número de la casa en Colombia: si la dirección completa no aparece, se prueba sin
   * el número y, por último, solo con la ciudad (lo que viene después de la última coma). */
  async function buscarDireccion(texto) {
    const sinNumero = texto.replace(/\s*(?:#|n[oº°]\.?)\s*\d+\s*[a-z]?\s*[-–]\s*\d+\s*[a-z]?/i, '').replace(/\s+,/g, ',').replace(/\s+/g, ' ').trim();
    const partes = texto.split(',').map((p) => p.trim()).filter(Boolean);
    const variantes = [...new Set([texto, sinNumero, partes.length > 1 ? partes[partes.length - 1] : ''].filter(Boolean))];
    for (let i = 0; i < variantes.length; i++) {
      if (i > 0) await new Promise((fin) => setTimeout(fin, 1100));      // Nominatim: como máximo una consulta por segundo
      const resultados = await consultarNominatim(variantes[i]);
      if (resultados.length) return { resultados, usada: variantes[i], aproximada: i > 0 };
    }
    return { resultados: [], usada: texto, aproximada: false };
  }
  async function buscar() {
    const texto = $('f-ubicacion').value.trim(), lista = $('ubic-resultados');
    lista.hidden = true; lista.textContent = '';
    if (!texto) return mensajeUbic('Escribe una dirección con la ciudad, o unas coordenadas (por ejemplo 4.65, -74.06).', true);
    const c = leerCoordenadas(texto);
    if (c) return ubicarHogar(c.lat, c.lon, 'Coordenadas');
    mensajeUbic('Buscando la dirección…');
    try {
      const { resultados: rs, usada, aproximada } = await buscarDireccion(texto);
      if (!rs.length) return mensajeUbic('No encontré esa dirección en Colombia. Agrega la ciudad o el barrio, haz clic en el mapa o escribe las coordenadas.', true);
      const etiqueta = (x) => `${aproximada ? `Aproximado: no encontré la dirección exacta y usé «${usada}» · ` : ''}${recorte(x.nombre, 70)}`;
      ubicarHogar(rs[0].lat, rs[0].lon, etiqueta(rs[0]));
      if (rs.length > 1) {
        lista.appendChild(el('li', 'ubic-otras', 'Otras coincidencias:'));
        rs.slice(1).forEach((x) => {
          const li = el('li'), b = el('button', null, recorte(x.nombre));
          b.type = 'button'; b.addEventListener('click', () => ubicarHogar(x.lat, x.lon, etiqueta(x)));
          li.appendChild(b); lista.appendChild(li);
        });
        lista.hidden = false;
      }
    } catch (e) {
      console.error(e);
      mensajeUbic('No se pudo consultar el buscador de direcciones. Prueba con las coordenadas o con «Usar mi ubicación».', true);
    }
  }
  /* Ubicación del navegador: pide permiso; las coordenadas no salen de este equipo (a la API solo llegan redondeadas a ~1 km). */
  function usarMiUbicacion() {
    const aviso = 'También puedes usar «Ubicarme por mi conexión», escribir la dirección o las coordenadas, o hacer clic en el mapa.';
    if (!navigator.geolocation) { $('btn-conexion').hidden = false; return mensajeUbic(`Este navegador no permite obtener la ubicación. ${aviso}`, true); }
    mensajeUbic('Esperando el permiso del navegador…');
    navigator.geolocation.getCurrentPosition(
      (pos) => { $('btn-conexion').hidden = true; ubicarHogar(pos.coords.latitude, pos.coords.longitude, `Tu ubicación (±${n0(pos.coords.accuracy)} m)`); },
      (err) => {
        console.warn('Geolocalización:', err.code, err.message);
        // Si el navegador no puede o tarda (ubicación del equipo apagada, sin señal), se usa sola la aproximada por la conexión.
        if (err.code === 2 || err.code === 3) return ubicarPorConexion('Tu navegador no pudo darme tu ubicación exacta; ');
        $('btn-conexion').hidden = false;      // con el permiso denegado se respeta y solo se ofrece el botón
        mensajeUbic(err.code === 1
          ? `El navegador no te dejó usar tu ubicación (permiso denegado). Para activarla, haz clic en el candado junto a la dirección de la página, elige Ubicación → Permitir y vuelve a pulsar el botón. ${aviso}`
          : `No se pudo obtener tu ubicación. ${aviso}`, true);
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
  }
  /* Respaldo: ubicación aproximada (ciudad) por la conexión a internet. Solo si la persona lo pide: la dirección IP
   * se envía a un servicio público de geolocalización por IP (geojs.io; si falla, ipwho.is). */
  async function ubicarPorConexion(prefijo = '') {
    mensajeUbic(prefijo ? `${prefijo}buscando tu ubicación aproximada por la conexión…` : 'Buscando tu ubicación aproximada por la conexión…');
    const fuentes = [
      async () => { const j = await (await fetch('https://get.geojs.io/v1/ip/geo.json')).json(); return { lat: +j.latitude, lon: +j.longitude, lugar: j.city }; },
      async () => { const j = await (await fetch('https://ipwho.is/')).json(); if (!j.success) throw new Error('ipwho.is sin resultado'); return { lat: +j.latitude, lon: +j.longitude, lugar: j.city }; },
    ];
    for (const consultar of fuentes) {
      try {
        const r = await consultar();
        if (Number.isFinite(r.lat) && Number.isFinite(r.lon)) return ubicarHogar(r.lat, r.lon, `Aproximado por tu conexión${r.lugar ? ` (${r.lugar})` : ''}: puede estar a varios km`);
      } catch (e) { console.warn('Ubicación por conexión:', e); }
    }
    return mensajeUbic('No se pudo ubicarte por la conexión. Escribe la dirección o las coordenadas, o haz clic en el mapa.', true);
  }

  /* ---------- Controles ---------- */
  function montarControles() {
    const deptos = deptosConEstacion();
    $('f-departamento').closest('label').hidden = deptos.length < 2;     // con un solo departamento no hay qué filtrar
    opciones($('f-departamento'), [['', `Toda ${datos.meta.departamento || 'Colombia'}`], ...deptos.map((d) => [d, d])]);
    opcionesEstacion();
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

    $('f-departamento').addEventListener('change', (e) => {
      st.departamento = e.target.value || null; st.punto = null; mensajeUbic('');
      const lista = estacionesVisibles();
      if (!lista.some((x) => x.id === st.estacion)) st.estacion = lista[0].id;
      render();
    });
    $('f-estacion').addEventListener('change', (e) => { elegirEstacion(+e.target.value); st.punto = null; mensajeUbic(''); render(); });
    $('f-estrato').addEventListener('change', (e) => { st.estrato = +e.target.value; aplicarTarifas(); render(); });
    $('f-perfil').addEventListener('change', (e) => { st.perfil = e.target.value; render(); });
    $('f-mes').addEventListener('change', (e) => { st.mes = e.target.value === '' ? null : +e.target.value; render(); });
    $('f-consumo').addEventListener('change', (e) => { st.consumo = acotar(+e.target.value, 30, 3000, D.consumo); render(); });
    $('f-tarifa-sub').addEventListener('change', (e) => { st.sup.tarifaSub = acotar(+e.target.value, 50, 3000, tarifasDe(st.estrato).subsistencia); render(); });
    $('f-tarifa-plena').addEventListener('change', (e) => { st.sup.tarifaPlena = acotar(+e.target.value, 50, 3000, tarifasDe(st.estrato).plena); render(); });
    $('btn-buscar').addEventListener('click', buscar);
    $('f-ubicacion').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); buscar(); } });
    $('btn-gps').addEventListener('click', usarMiUbicacion);
    $('btn-conexion').addEventListener('click', () => ubicarPorConexion());
    if (!navigator.geolocation) $('btn-conexion').hidden = false;
    $('btn-reset').addEventListener('click', () => {
      const { estacion, departamento, punto, consumo, estrato, perfil, mes, escenario } = st;
      st = { ...inicial(), estacion, departamento, punto, consumo, estrato, perfil, mes, escenario };
      aplicarTarifas();                       // las tarifas son las del estrato que quedó elegido
      render();
    });
  }
  const acotar = (v, a, b, def) => (Number.isFinite(v) ? Math.min(b, Math.max(a, v)) : def);

  function leerSupuesto(k, inp) {
    const campo = CAMPOS.find((c) => c[0] === k);
    const v = +inp.value / campo[2];
    if (!Number.isFinite(v) || v < 0) return render();
    if (k.startsWith('kwp')) st.kwp[+k[3]] = acotar(v, 0.5, 50, D.kwp[+k[3]]);
    else if (k === 'horizonte') st.sup.horizonte = Math.round(acotar(v, 5, 40, 25));
    else if (k === 'anioInversor') st.sup.anioInversor = Math.round(acotar(v, 1, 40, D.sup.anioInversor));
    else if (k === 'perdidas') st.sup.perdidas = acotar(v, 0, 0.6, D.sup.perdidas);
    else if (k === 'eficiencia') st.sup.eficiencia = acotar(v, 0.5, 1, D.sup.eficiencia);
    else if (k === 'degradacionInicial') st.sup.degradacionInicial = acotar(v, 0, 0.1, D.sup.degradacionInicial);
    else st.sup[k] = v;
    render();
  }

  const redondeo = (v) => Math.round(v * 100) / 100;

  function pintarControles() {
    opcionesEstacion();
    $('f-departamento').value = st.departamento || '';
    $('f-estacion').value = st.estacion; $('f-estrato').value = st.estrato; $('f-perfil').value = st.perfil;
    $('f-mes').value = st.mes === null ? '' : st.mes;
    $('f-consumo').value = st.consumo;
    $('f-tarifa-sub').value = redondeo(st.sup.tarifaSub); $('f-tarifa-plena').value = redondeo(st.sup.tarifaPlena);
    const eTar = datos.estaciones.find((e) => e.id === st.estacion);
    $('info-tarifa').textContent = `Estrato ${st.estrato}: la tarifa de subsistencia se cobra hasta ${M.subsistencia(eTar.alt)} kWh al mes (la estación está a ${n0(eTar.alt)} m) y la plena sobre ese consumo.`;
    [...$('f-escenario').children].forEach((b, i) => {
      b.textContent = `${ESC[i].nombre} · ${n1(st.kwp[i]).replace(',0', '')} kWp`;
      b.setAttribute('aria-pressed', i === st.escenario ? 'true' : 'false');
    });
    CAMPOS.forEach(([k, , f]) => {
      const v = k.startsWith('kwp') ? st.kwp[+k[3]] : st.sup[k];
      $('s-' + k).value = Math.round(v * f * 1000) / 1000;
    });
  }

  /* ---------- Motor de cálculo ----------
   * 'api': física y finanzas de R3 en el servidor (CalcusolAPI). 'local': model.js en el navegador,
   * que queda como respaldo si la API no responde. */
  let motor = 'local';
  let ultimoRender = 0;      // si el usuario cambia otro filtro mientras se calcula, se descarta la respuesta vieja

  function mostrarMotor(texto, clase) {
    const p = $('motor');
    p.textContent = texto; p.className = 'motor ' + (clase || motor);
  }
  const textoMotor = () => (motor === 'api'
    ? `Cálculo: API de Calcusol${/127\.0\.0\.1|localhost/.test(API.url()) ? ` · ${API.url()}` : ''}`
    : 'Cálculo local en el navegador (la API no está disponible)');

  async function iniciarMotor() {
    const q = new URLSearchParams(location.search).get('api');
    if (q === 'off') { motor = 'local'; mostrarMotor('Cálculo local en el navegador (api=off)'); return; }
    API.configurar(q || undefined);
    motor = (await API.disponible()) ? 'api' : 'local';
    if (motor === 'api') {                 // los valores por defecto salen de la ficha de supuestos de R3
      try { aplicarParametros(await API.parametros()); } catch (err) { console.warn('Parámetros de la API:', err); }
    }
    mostrarMotor(textoMotor());
  }

  /* Los 3 tamaños x 2 fuentes de radiación (corregida y NASA), con la API o con el modelo local. */
  async function simularTodo(base) {
    const pares = ESC.flatMap((e, i) => ['ml', 'nasa'].map((fuente) => ({ i, fuente, entrada: { ...base, fuente, kwp: st.kwp[i] } })));
    if (motor === 'api') {
      try {
        const res = await Promise.all(pares.map((p) => API.simular(p.entrada, M.PERFILES)));
        return pares.map((p, k) => ({ ...p, r: res[k] }));
      } catch (err) {
        console.error(err);
        motor = 'local';
        mostrarMotor(`La API falló (${err.message}). Se usa el cálculo local.`);
      }
    }
    return pares.map((p) => ({ ...p, r: M.simular(p.entrada) }));
  }

  /* ---------- Render ---------- */
  async function render() {
    pintarControles();
    const id = ++ultimoRender;
    const est = datos.estaciones.find((e) => e.id === st.estacion);
    const horas = datos.meta.horas;
    const base = { est, punto: st.punto, consumo: st.consumo, perfil: st.perfil, estrato: st.estrato, sup: st.sup, horas };
    if (motor === 'api') { mostrarMotor('Calculando con la API…', 'calculando'); document.querySelector('.contenido').classList.add('calculando'); }
    const sims = await simularTodo(base);
    if (id !== ultimoRender) return;                       // llegó una petición más nueva
    document.querySelector('.contenido').classList.remove('calculando');
    mostrarMotor(textoMotor());
    const de = (i, f) => sims.find((s) => s.i === i && s.fuente === f).r;
    const esc = ESC.map((e, i) => ({
      ...e, i, kwp: st.kwp[i], etiqueta: `${e.nombre} · ${n1(st.kwp[i]).replace(',0', '')} kWp`,
      ml: de(i, 'ml'), nasa: de(i, 'nasa'),
    }));
    const act = esc[st.escenario], r = act.ml;
    const conPayback = esc.filter((e) => e.ml.payback !== null);
    const mejor = conPayback.length ? conPayback.reduce((a, b) => (b.ml.payback < a.ml.payback ? b : a)) : null;
    const mesTxt = st.mes === null ? 'Promedio del año' : MESES[st.mes];
    $('t-lugar').textContent = territorio();
    document.title = `Calcusol · Paneles solares en ${territorio()}`;

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
      desc: `Mapa de calor de la radiación solar corregida en ${datos.meta.departamento || 'Colombia'}, con departamentos y municipios, la ubicación elegida y la estación más cercana`,
      limite: datos.limite, municipios: datos.municipios, departamentos: datos.departamentos, islas: datos.islas, enfoque: enfoque(),
      unidad: 'kWh/m² al día', fmt: (v) => nf(2).format(v),
      puntos: datos.estaciones.map((e) => ({ id: e.id, nombre: e.nombre, lon: e.lon, lat: e.lat, valor: radiacionDia(e), sel: e.id === est.id, e })),
      punto: st.punto && { ...st.punto, texto: km(M.distanciaKm(st.punto.lat, st.punto.lon, est.lat, est.lon)) },
      tip: (lon, lat, aqui, municipio) => {
        const c = M.masCercana(datos.estaciones, lat, lon);
        return { titulo: municipio || territorio(), filas: [
          { label: 'Radiación aquí', value: `${nf(2).format(aqui)} kWh/m²` },
          { label: 'Estación más cercana', value: c.est.nombre },
          { label: 'Distancia', value: km(c.km) },
        ] };
      },
      tipEstacion: (p) => ({ titulo: `Estación ${p.nombre}`, filas: [
        { label: 'Radiación', value: `${nf(2).format(p.valor)} kWh/m²` },
        { label: datos.meta.demo ? 'NASA frente a corregida' : 'Sesgo de NASA', value: pctS(p.e.sesgo_nasa) },
        ...(p.e.departamento ? [{ label: 'Departamento', value: p.e.departamento }] : []),
        { label: 'Altitud', value: `${n0(p.e.alt)} m` },
        ...(p.e.preliminar > 0 ? [{ label: 'Datos preliminares', value: pct(p.e.preliminar) }] : []),
      ] }),
      onPunto: (lon, lat) => ubicarHogar(lat, lon, 'Punto del mapa'),
      onClic: (p) => { elegirEstacion(p.id); st.punto = null; mensajeUbic(''); render(); },
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
      m.demo
        ? `DATOS DE DEMOSTRACIÓN: ${n0(m.registros)} registros horarios de NASA POWER (${m.desde}–${m.hasta}) en ${m.estaciones} capitales de departamento, corregidos con el modelo de la API. No son estaciones ni mediciones del IDEAM. ${est.nombre}: ${n0(est.registros)} registros, altitud de la celda NASA (${n0(est.alt)} m). Para usar los datos reales, ejecute «make data» (parquet) o build_data_aws.py (AWS).`
        : `Datos: ${n0(m.registros)} registros horarios de ${m.estaciones} estaciones del IDEAM en ${m.departamento || 'Colombia'}${m.departamentos > 1 ? ` (${m.departamentos} departamentos)` : ''}, ${m.desde}–${m.hasta}, cruzados con NASA POWER.${m.registros_usados ? ` Para el cálculo se usan ${n0(m.registros_usados)}: las horas de sol de 7 a 18 h (la madrugada y la noche casi no tienen radiación).` : ''} ${est.nombre}: ${n0(est.registros)} registros usados entre ${est.desde} y ${est.hasta}, a ${n0(est.alt)} m.`,
      ...(m.preliminares ? [`Datos preliminares: el IDEAM aún no ha validado parte de las mediciones. ${datos.estaciones.filter((e) => e.preliminar >= 0.5).length} estaciones tienen la mitad o más de sus registros en esa condición (se ve al pasar el cursor sobre la estación en el mapa); sus cifras son menos firmes que las de las estaciones validadas.`] : []),
      motor === 'api'
        ? `Cálculo: lo hace la API de Calcusol (${API.url()}). Arma un año de 8.760 horas con el día promedio de cada mes y calcula la producción con pvlib (panel inclinado, temperatura de celda y pérdidas), el consumo con el perfil elegido, la factura con las tarifas de la ficha de supuestos escaladas a la «Tarifa del operador», el crédito de energía de la CREG 174 de 2021 y el reemplazo del inversor en el año 15. Por eso las cifras difieren algo del cálculo local del navegador, que queda como respaldo.`
        : 'Cálculo: se hace en el navegador con el modelo simplificado de model.js porque la API no respondió. Inicie la API y recargue para usar el cálculo de R3.',
      'Mapa: la radiación entre estaciones se interpola por distancia y es orientativa. Al marcar una ubicación se usa la estación más cercana en línea recta; la distancia es aproximada. Contorno del país: geoBoundaries (CC0). Departamentos: geoBoundaries / OpenStreetMap (ODbL). Municipios: geoBoundaries / DANE (CC BY 4.0). San Andrés y Providencia, a unos 700 km del continente, se dibujan en recuadros ampliados abajo a la izquierda.',
      `Radiación corregida: predicción del modelo ${m.modelo || 'LightGBM'} de Calcusol sobre las variables de NASA POWER. ${m.validacion ? `El sesgo y el error de cada estación se miden con los años ${m.validacion}, que el modelo no vio al entrenar; pero la mayoría de las estaciones sí estaban en el entrenamiento (con años anteriores), así que es una medida más favorable que la de un lugar donde no hay estación.` : 'El error se midió sobre los mismos registros históricos; no es una validación independiente.'}`,
      'Ubicación del hogar: la radiación y la temperatura son las de la estación más cercana en línea recta (la distancia se indica al buscar), y la altitud es la de esa estación. Con una ubicación exacta, el cálculo del sol usa su latitud y longitud, que llegan a la API redondeadas a unos 1.100 m; la ubicación del navegador no se guarda. La búsqueda de direcciones la hace Nominatim (OpenStreetMap), que recibe el texto escrito solo al pulsar «Buscar».',
      `Energía: radiación horizontal del día promedio de cada mes, corrección por temperatura de celda, ${pct(st.sup.perdidas)} de pérdidas y ${pct(st.sup.eficiencia, 0)} de eficiencia del inversor. El balance con el día promedio tiende a sobrestimar un poco el autoconsumo.`,
      `Finanzas y parámetros: los valores por defecto son los de la ficha de supuestos del proyecto (tarifas de EPM de octubre de 2026, costos publicados por instaladores, tasa de descuento del DNP, datos del fabricante del panel). Son valores de referencia editables en «Supuestos técnicos y financieros», no datos medidos. La tarifa depende del estrato: hasta el consumo de subsistencia (130 kWh al mes desde 1.000 m de altitud y 173 por debajo) se cobra la tarifa de subsistencia y el resto la plena; al cambiar el estrato se cargan las tarifas de ese estrato. Incluye el reemplazo del inversor en el año ${st.sup.anioInversor}.`,
      'El perfil horario del consumo del hogar es una forma de ejemplo, no una medición.',
    ].forEach((p) => c.appendChild(el('p', null, p)));
  }

  /* Estado inicial desde la URL, para compartir un caso:
   * ?lat=6.15&lon=-75.37&consumo=250&estrato=3&tarifa=950&perfil=diurno&mes=7&escenario=1 */
  function leerUrl() {
    const q = new URLSearchParams(location.search), num = (k) => (q.has(k) ? +q.get(k) : NaN);
    if (deptosConEstacion().includes(q.get('departamento'))) st.departamento = q.get('departamento');
    if (datos.estaciones.some((e) => e.id === num('estacion'))) elegirEstacion(num('estacion'));
    if (num('lat') > -5 && num('lat') < 14 && num('lon') > -82 && num('lon') < -66) {
      st.punto = { lat: num('lat'), lon: num('lon') };
      elegirEstacion(M.masCercana(datos.estaciones, st.punto.lat, st.punto.lon).est.id);
    }
    // Un departamento sin la estación elegida (por la URL o por el filtro) queda con la primera de su lista
    if (!estacionesVisibles().some((e) => e.id === st.estacion)) st.estacion = estacionesVisibles()[0].id;
    if (num('consumo') > 0) st.consumo = acotar(num('consumo'), 30, 3000, D.consumo);
    if (num('estrato') in M.TARIFAS) { st.estrato = num('estrato'); aplicarTarifas(); }
    if (num('tarifa') > 0) st.sup.tarifaPlena = acotar(num('tarifa'), 50, 3000, tarifasDe(st.estrato).plena);
    if (num('tarifa_sub') > 0) st.sup.tarifaSub = acotar(num('tarifa_sub'), 50, 3000, tarifasDe(st.estrato).subsistencia);
    if (q.get('perfil') in M.PERFILES) st.perfil = q.get('perfil');
    if (num('mes') >= 1 && num('mes') <= 12) st.mes = Math.round(num('mes')) - 1;
    if ([1, 2, 3].includes(num('escenario'))) st.escenario = num('escenario') - 1;
  }

  /* ---------- Arranque ---------- */
  // Datos de toda Colombia; si no están, los de Antioquia de la versión anterior
  fetch('data/colombia.json')
    .then((r) => (r.ok ? r : fetch('data/antioquia.json')))
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(async (d) => {
      datos = d;
      const m = d.meta;
      $('meta-datos').textContent = m.demo
        ? `${n0(m.registros)} registros horarios de NASA POWER · ${m.estaciones} capitales de departamento · ${m.desde}–${m.hasta}`
        : `${n0(m.registros)} registros horarios del IDEAM y NASA POWER · ${m.estaciones} estaciones · ${m.desde}–${m.hasta}`;
      if (m.demo) {
        const aviso = el('p', 'demo', 'Datos de demostración: NASA POWER corregida con el modelo, no mediciones del IDEAM');
        $('motor').after(aviso);
      }
      if (m.preliminares) $('chips').appendChild(el('p', 'chip', 'Incluye mediciones preliminares del IDEAM'));
      await iniciarMotor();                   // primero: si la API responde, trae los valores por defecto de la ficha
      st = inicial();
      const nombreInicial = m.estacion_inicial || ESTACION_INICIAL;
      st.estacion = (d.estaciones.find((e) => e.nombre === nombreInicial) || d.estaciones[0]).id;
      leerUrl();
      montarControles();
      describirPunto();                       // si la dirección de la página trae lat y lon
      render();
      let pend = 0, ancho = innerWidth;
      addEventListener('resize', () => {
        if (innerWidth === ancho) return;
        ancho = innerWidth; cancelAnimationFrame(pend); pend = requestAnimationFrame(render);
      });
    })
    .catch((e) => {
      $('meta-datos').textContent = 'No se pudieron cargar los datos';
      $('motor').textContent = '';
      $('t-respuesta').textContent = 'No se pudieron cargar los datos. Ejecute «make data» (o «make demo-data» para datos de demostración) y abra el dashboard con «make serve».';
      console.error(e);
    });
})();
