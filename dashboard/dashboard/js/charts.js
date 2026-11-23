/* Gráficos SVG sin dependencias, con las marcas de la presentación:
 * columnas apiladas, barras horizontales ordenadas, dumbbell y líneas. */
(function (root) {
  'use strict';

  const C = {
    marino: '#0b3d7a', azul: '#2f80d0', azulClaro: '#95bee7', coral: '#e87b7b', coralClaro: '#f3bdbd',
    verde: '#1e9e5a', texto2: '#4a6283', texto3: '#6b84a6', rejilla: '#e1eaf5', borde: '#c9d9ec',
    banda: '#f2f7fd', fondo: '#ffffff',
  };
  const NS = 'http://www.w3.org/2000/svg';

  function s(tag, attrs, parent, texto) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    if (texto !== undefined) n.textContent = texto;
    if (parent) parent.appendChild(n);
    return n;
  }

  function lienzo(el, alto, m, descripcion) {
    el.textContent = '';
    const w = Math.max(el.clientWidth || 560, 280);
    const svg = s('svg', { viewBox: `0 0 ${w} ${alto}`, width: w, height: alto, role: 'img', 'aria-label': descripcion }, el);
    return { svg, w, h: alto, iw: w - m.l - m.r, ih: alto - m.t - m.b, m };
  }

  /* Marcas "redondas" del eje: 0, paso, 2·paso… hasta cubrir el máximo. */
  function marcas(min, max, n) {
    const rango = (max - min) || 1;
    const bruto = rango / n, p = Math.pow(10, Math.floor(Math.log10(bruto)));
    const paso = [1, 2, 2.5, 5, 10].map((k) => k * p).find((k) => k >= bruto);
    const a = Math.floor(min / paso) * paso, b = Math.ceil(max / paso - 1e-9) * paso;
    const out = [];
    for (let v = a; v <= b + paso / 2; v += paso) out.push(Math.round(v * 1e6) / 1e6);
    return out;
  }

  /* ---------- Tooltip compartido ---------- */
  const tip = () => document.getElementById('tip');
  function mostrarTip(ev, titulo, filas) {
    const t = tip();
    t.textContent = '';
    const b = document.createElement('b'); b.textContent = titulo; t.appendChild(b);
    filas.forEach((f) => {
      const d = document.createElement('div');
      if (f.color) { const i = document.createElement('i'); i.style.background = f.color; d.appendChild(i); }
      d.appendChild(document.createTextNode(f.label));
      const v = document.createElement('span'); v.textContent = f.value; d.appendChild(v);
      t.appendChild(d);
    });
    t.hidden = false;
    const r = t.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
    t.style.left = Math.max(8, x) + 'px';
    t.style.top = Math.max(8, y) + 'px';
  }
  function ocultarTip() { tip().hidden = true; }

  function ejeY(g, ticks, y, fmt) {
    ticks.forEach((v) => {
      s('line', { x1: g.m.l, x2: g.m.l + g.iw, y1: y(v), y2: y(v), stroke: v === 0 ? C.borde : C.rejilla, 'stroke-width': 1 }, g.svg);
      s('text', { x: g.m.l - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 12.5, fill: C.texto3 }, g.svg, fmt(v));
    });
  }

  /* Títulos de los ejes: el del eje Y va girado a la izquierda y el del X centrado abajo. */
  function titulosEjes(g, o) {
    const est = { 'font-size': 13.5, 'font-weight': 500, fill: C.texto2, 'text-anchor': 'middle' };
    if (o.ejeY) s('text', { ...est, transform: `translate(14,${g.m.t + g.ih / 2}) rotate(-90)` }, g.svg, o.ejeY);
    if (o.ejeX) s('text', { ...est, x: g.m.l + g.iw / 2, y: g.h - 6 }, g.svg, o.ejeX);
  }

  /* Rectángulo con las esquinas superiores redondeadas (extremo del dato). */
  function barraSup(x, y, w, h, r) {
    r = Math.min(r, h, w / 2);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  /* ---------- Columnas apiladas ----------
   * o = { cats, pilas:[{color, valores, colorEn?(i)}], yFmt, tip(i), alto, cada, destacado,
   *       etiqueta?(i), linea?:{valores,color}, ref?:{valor,texto}, desc } */
  function columnas(el, o) {
    const g = lienzo(el, o.alto || 380, { t: 30, r: 12, b: 58, l: 76 }, o.desc);
    titulosEjes(g, o);
    const n = o.cats.length;
    const totales = o.cats.map((_, i) => o.pilas.reduce((a, p) => a + Math.max(0, p.valores[i]), 0));
    const extra = [...(o.linea ? o.linea.valores : []), ...(o.ref ? [o.ref.valor] : [])];
    const ticks = marcas(0, Math.max(...totales, ...extra, 1e-9) * 1.04, 4);
    const max = ticks[ticks.length - 1];
    const y = (v) => g.m.t + g.ih * (1 - v / max);
    const banda = g.iw / n, bw = Math.min(banda * 0.74, 64);
    ejeY(g, ticks, y, o.yFmt);
    // Etiquetas del eje X: se saltan las que no caben, conservando la destacada
    const anchoEt = Math.max(...o.cats.map((c) => c.length)) * 7.4 + 6;
    const cada = Math.max(o.cada || 1, Math.ceil(anchoEt / banda));
    const fase = o.destacado >= 0 ? o.destacado % cada : 0;

    const resalte = s('rect', { y: g.m.t - 6, width: banda, height: g.ih + 6, fill: C.banda, rx: 6, visibility: 'hidden' }, g.svg);
    g.svg.insertBefore(resalte, g.svg.firstChild);

    o.cats.forEach((cat, i) => {
      const x = g.m.l + banda * i + (banda - bw) / 2;
      let base = 0;
      const visibles = o.pilas.filter((p) => p.valores[i] > 0);
      visibles.forEach((p, k) => {
        const v = p.valores[i], y1 = y(base + v), hpx = y(base) - y1;
        const hueco = k > 0 ? 2 : 0;               // separación de 2 px entre tramos
        const alto = Math.max(hpx - hueco, 0.5);
        const color = p.colorEn ? p.colorEn(i) : p.color;
        if (k === visibles.length - 1) s('path', { d: barraSup(x, y1, bw, alto, 4), fill: color }, g.svg);
        else s('rect', { x, y: y1, width: bw, height: alto, fill: color }, g.svg);
        base += v;
      });
      const et = o.etiqueta && o.etiqueta(i);
      if (et) s('text', { x: x + bw / 2, y: y(totales[i]) - 7, 'text-anchor': 'middle', 'font-size': 13.5, 'font-weight': 700, fill: C.marino }, g.svg, et);
      if ((i - fase) % cada === 0) {
        s('text', { x: x + bw / 2, y: g.m.t + g.ih + 22, 'text-anchor': 'middle', 'font-size': 13, fill: i === o.destacado ? C.marino : C.texto3, 'font-weight': i === o.destacado ? 700 : 400 }, g.svg, cat);
      }
    });

    if (o.ref) {
      const yr = y(o.ref.valor);
      s('line', { x1: g.m.l, x2: g.m.l + g.iw, y1: yr, y2: yr, stroke: C.marino, 'stroke-width': 2, 'stroke-dasharray': '2 5', 'stroke-linecap': 'round' }, g.svg);
    }
    if (o.linea) {
      // Línea escalonada: el consumo es un valor por hora, no una curva continua
      let d = '';
      o.linea.valores.forEach((v, i) => {
        const x0 = g.m.l + banda * i, x1 = x0 + banda;
        d += `${i ? 'L' : 'M'}${x0},${y(v)}L${x1},${y(v)}`;
      });
      s('path', { d, fill: 'none', stroke: C.fondo, 'stroke-width': 5, 'stroke-linejoin': 'round' }, g.svg);
      s('path', { d, fill: 'none', stroke: o.linea.color, 'stroke-width': 2, 'stroke-linejoin': 'round' }, g.svg);
    }

    // Zonas de contacto: la banda completa, más grande que la marca
    o.cats.forEach((_, i) => {
      const z = s('rect', { x: g.m.l + banda * i, y: g.m.t - 6, width: banda, height: g.ih + 6, fill: 'transparent' }, g.svg);
      z.addEventListener('pointermove', (ev) => {
        resalte.setAttribute('x', g.m.l + banda * i); resalte.setAttribute('visibility', 'visible');
        const t = o.tip(i); mostrarTip(ev, t.titulo, t.filas);
      });
      z.addEventListener('pointerleave', () => { resalte.setAttribute('visibility', 'hidden'); ocultarTip(); });
    });
  }

  /* ---------- Barras horizontales ordenadas ----------
   * o = { items:[{id,label,valor,sel}], fmt, tip(item), onClic(item), desc } */
  function barrasH(el, o) {
    const fila = 23, etq = Math.min(176, Math.max(152, el.clientWidth * 0.3));
    const g = lienzo(el, o.items.length * fila + 12, { t: 4, r: 54, b: 8, l: etq }, o.desc);
    const vals = o.items.map((d) => d.valor);
    const min = Math.min(0, ...vals), max = Math.max(0, ...vals) || 1;
    const x = (v) => g.m.l + g.iw * ((v - min) / (max - min));
    s('line', { x1: x(0), x2: x(0), y1: g.m.t, y2: g.h - g.m.b, stroke: C.borde, 'stroke-width': 1 }, g.svg);

    o.items.forEach((d, i) => {
      const yy = g.m.t + fila * i, bh = 14, by = yy + (fila - bh) / 2;
      const grupo = s('g', { 'data-clic': 1, tabindex: 0, role: 'button', 'aria-label': `${d.label}: ${o.fmt(d.valor)}`, 'aria-pressed': d.sel ? 'true' : 'false' }, g.svg);
      const fondo = s('rect', { x: 0, y: yy, width: g.w, height: fila, fill: d.sel ? C.banda : 'transparent', rx: 6 }, grupo);
      s('text', { x: g.m.l - 10, y: yy + fila / 2 + 4, 'text-anchor': 'end', 'font-size': 13.5, fill: d.sel ? C.marino : C.texto2, 'font-weight': d.sel ? 700 : 400 }, grupo, d.label);
      const x0 = x(Math.min(0, d.valor)), w = Math.max(Math.abs(x(d.valor) - x(0)), 2);
      s('rect', { x: x0, y: by, width: w, height: bh, rx: 4, fill: d.sel ? C.marino : C.azul }, grupo);
      const neg = d.valor < 0;
      s('text', { x: neg ? x(0) + 6 : x(d.valor) + 6, y: yy + fila / 2 + 4, 'font-size': 13.5, 'font-weight': d.sel ? 700 : 500, fill: d.sel ? C.marino : C.texto2 }, grupo, o.fmt(d.valor));
      grupo.addEventListener('pointermove', (ev) => {
        if (!d.sel) fondo.setAttribute('fill', C.banda);
        const t = o.tip(d); mostrarTip(ev, t.titulo, t.filas);
      });
      grupo.addEventListener('pointerleave', () => { if (!d.sel) fondo.setAttribute('fill', 'transparent'); ocultarTip(); });
      grupo.addEventListener('click', () => o.onClic(d));
      grupo.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); o.onClic(d); } });
    });
  }

  /* ---------- Dumbbell: dos valores por fila ----------
   * o = { filas:[{label, a, b, sel}], fmt, nombreA, nombreB, sinValor, desc } */
  function dumbbell(el, o) {
    const fila = 84;
    const g = lienzo(el, o.filas.length * fila + 66, { t: 8, r: 56, b: 58, l: Math.min(200, Math.max(146, el.clientWidth * 0.28)) }, o.desc);
    titulosEjes(g, o);
    const vals = o.filas.flatMap((f) => [f.a, f.b]).filter((v) => v !== null);
    const ticks = vals.length ? marcas(Math.max(0, Math.min(...vals) - 0.6), Math.max(...vals) + 0.6, 5) : [0, 1];
    const min = ticks[0], max = ticks[ticks.length - 1];
    const x = (v) => g.m.l + g.iw * ((v - min) / (max - min));
    ticks.forEach((v) => {
      s('line', { x1: x(v), x2: x(v), y1: g.m.t, y2: g.h - g.m.b, stroke: C.rejilla }, g.svg);
      s('text', { x: x(v), y: g.m.t + g.ih + 22, 'text-anchor': 'middle', 'font-size': 12.5, fill: C.texto3 }, g.svg, o.fmtEje(v));
    });
    o.filas.forEach((f, i) => {
      const yy = g.m.t + fila * i, cy = yy + fila / 2;
      const grupo = s('g', {}, g.svg);
      s('rect', { x: 30, y: yy + 4, width: g.w - 30, height: fila - 8, rx: 8, fill: f.sel ? C.banda : 'transparent' }, grupo);
      s('text', { x: g.m.l - 14, y: cy + 4, 'text-anchor': 'end', 'font-size': 14, fill: f.sel ? C.marino : C.texto2, 'font-weight': f.sel ? 700 : 400 }, grupo, f.label);
      if (f.a === null && f.b === null) {
        s('text', { x: g.m.l + 6, y: cy + 4, 'font-size': 13.5, fill: C.texto3 }, grupo, o.sinValor);
      } else {
        const izq = f.a !== null && f.b !== null && f.a <= f.b;
        if (f.a !== null && f.b !== null) s('line', { x1: x(f.a), x2: x(f.b), y1: cy, y2: cy, stroke: C.borde, 'stroke-width': 4, 'stroke-linecap': 'round' }, grupo);
        const punto = (v, color, aLaIzq, negrita) => {
          if (v === null) return;
          s('circle', { cx: x(v), cy, r: 10, fill: color, stroke: C.fondo, 'stroke-width': 2 }, grupo);
          s('text', { x: x(v) + (aLaIzq ? -17 : 17), y: cy + 4, 'text-anchor': aLaIzq ? 'end' : 'start', 'font-size': 14, 'font-weight': negrita ? 700 : 500, fill: negrita ? C.marino : C.texto2 }, grupo, o.fmt(v));
        };
        punto(f.a, C.coral, izq, false);
        punto(f.b, C.verde, !izq, true);
      }
      const z = s('rect', { x: 0, y: yy, width: g.w, height: fila, fill: 'transparent' }, grupo);
      z.addEventListener('pointermove', (ev) => mostrarTip(ev, f.label, [
        { color: C.coral, label: o.nombreA, value: f.a === null ? o.sinValor : o.fmt(f.a) + ' años' },
        { color: C.verde, label: o.nombreB, value: f.b === null ? o.sinValor : o.fmt(f.b) + ' años' },
      ]));
      z.addEventListener('pointerleave', ocultarTip);
    });
  }

  /* ---------- Líneas con cursor ----------
   * o = { x:[...], series:[{nombre,color,valores,sel}], yFmt, xFmt, tipFmt, desc } */
  function lineas(el, o) {
    const g = lienzo(el, o.alto || 380, { t: 14, r: 92, b: 58, l: 86 }, o.desc);
    titulosEjes(g, o);
    const todo = o.series.flatMap((d) => d.valores);
    const ticks = marcas(Math.min(0, ...todo), Math.max(0, ...todo), 5);
    const min = ticks[0], max = ticks[ticks.length - 1], n = o.x.length;
    const x = (i) => g.m.l + g.iw * (i / (n - 1));
    const y = (v) => g.m.t + g.ih * (1 - (v - min) / (max - min));
    ejeY(g, ticks, y, o.yFmt);
    s('line', { x1: g.m.l, x2: g.m.l + g.iw, y1: y(0), y2: y(0), stroke: C.texto3, 'stroke-width': 1 }, g.svg);
    o.x.forEach((v, i) => { if (i % 5 === 0) s('text', { x: x(i), y: g.m.t + g.ih + 22, 'text-anchor': 'middle', 'font-size': 13, fill: C.texto3 }, g.svg, o.xFmt(v)); });

    const cursor = s('line', { y1: g.m.t, y2: g.m.t + g.ih, stroke: C.borde, 'stroke-width': 1, visibility: 'hidden' }, g.svg);
    const orden = o.series.slice().sort((a, b) => (a.sel ? 1 : 0) - (b.sel ? 1 : 0));
    orden.forEach((d) => {
      const pts = d.valores.map((v, i) => `${x(i)},${y(v)}`).join(' ');
      s('polyline', { points: pts, fill: 'none', stroke: C.fondo, 'stroke-width': d.sel ? 6 : 4, 'stroke-linejoin': 'round' }, g.svg);
      s('polyline', { points: pts, fill: 'none', stroke: d.color, 'stroke-width': d.sel ? 3 : 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, g.svg);
    });
    // Etiquetas directas al final, separadas para que no se monten
    const fin = o.series.map((d) => ({ d, y: y(d.valores[n - 1]) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < fin.length; i++) if (fin[i].y - fin[i - 1].y < 18) fin[i].y = fin[i - 1].y + 18;
    fin.forEach((f) => s('text', { x: g.m.l + g.iw + 8, y: f.y + 4, 'font-size': 13.5, 'font-weight': f.d.sel ? 700 : 500, fill: f.d.sel ? C.marino : C.texto2 }, g.svg, f.d.nombre));

    const puntos = o.series.map((d) => s('circle', { r: 5, fill: d.color, stroke: C.fondo, 'stroke-width': 2, visibility: 'hidden' }, g.svg));
    const z = s('rect', { x: g.m.l, y: g.m.t, width: g.iw, height: g.ih, fill: 'transparent' }, g.svg);
    z.addEventListener('pointermove', (ev) => {
      const r = g.svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (g.w / r.width);
      const i = Math.max(0, Math.min(n - 1, Math.round((px - g.m.l) / g.iw * (n - 1))));
      cursor.setAttribute('x1', x(i)); cursor.setAttribute('x2', x(i)); cursor.setAttribute('visibility', 'visible');
      o.series.forEach((d, k) => { puntos[k].setAttribute('cx', x(i)); puntos[k].setAttribute('cy', y(d.valores[i])); puntos[k].setAttribute('visibility', 'visible'); });
      mostrarTip(ev, o.xFmt(o.x[i], true), o.series.map((d) => ({ color: d.color, label: d.nombre, value: o.tipFmt(d.valores[i]) })));
    });
    z.addEventListener('pointerleave', () => {
      cursor.setAttribute('visibility', 'hidden'); puntos.forEach((p) => p.setAttribute('visibility', 'hidden')); ocultarTip();
    });
  }


  /* ---------- Rampa de color del mapa ---------- */
  // Azul = menos radiación, rojo = más; el punto medio es un gris claro neutro
  const RAMPA = [[47, 128, 208], [160, 197, 236], [244, 238, 233], [240, 160, 150], [214, 69, 65]];
  function colorRampa(t) {
    t = Math.max(0, Math.min(1, t)) * (RAMPA.length - 1);
    const i = Math.min(RAMPA.length - 2, Math.floor(t)), f = t - i;
    return 'rgb(' + RAMPA[i].map((c, k) => Math.round(c + (RAMPA[i + 1][k] - c) * f)).join(',') + ')';
  }
  let nMapa = 0;
  const vistas = new WeakMap(), ZOOM_MAX = 6, rasteres = new Map();

  /* Campo de radiación como una sola imagen (inverso de la distancia al cuadrado entre
   * estaciones). Se calcula una vez por combinación de valores y se reutiliza al hacer zoom. */
  function rasterCalor(pts, ancho, alto, vmin, vmax) {
    const clave = pts.map((p) => p.valor.toFixed(3)).join('|') + `|${ancho}x${alto}`;
    if (rasteres.has(clave)) return rasteres.get(clave);
    const cv = document.createElement('canvas'); cv.width = ancho; cv.height = alto;
    const ctx = cv.getContext('2d'), img = ctx.createImageData(ancho, alto), d = img.data;
    // Tabla de color precalculada: evita interpolar la rampa en cada píxel
    const tabla = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
      const t = (i / 255) * (RAMPA.length - 1), k = Math.min(RAMPA.length - 2, Math.floor(t)), f = t - k;
      for (let c = 0; c < 3; c++) tabla[i * 3 + c] = Math.round(RAMPA[k][c] + (RAMPA[k + 1][c] - RAMPA[k][c]) * f);
    }
    const rango = (vmax - vmin) || 1;
    for (let y = 0, q = 0; y < alto; y++) {
      for (let x = 0; x < ancho; x++, q += 4) {
        let num = 0, den = 0, exacto = null;
        for (const p of pts) {
          const d2 = (x + 0.5 - p.rx) ** 2 + (y + 0.5 - p.ry) ** 2;
          if (d2 < 0.5) { exacto = p.valor; break; }
          num += p.valor / d2; den += 1 / d2;
        }
        const i = Math.max(0, Math.min(255, Math.round((((exacto === null ? num / den : exacto) - vmin) / rango) * 255))) * 3;
        d[q] = tabla[i]; d[q + 1] = tabla[i + 1]; d[q + 2] = tabla[i + 2]; d[q + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    const url = cv.toDataURL('image/png');
    if (rasteres.size > 30) rasteres.clear();
    rasteres.set(clave, url);
    return url;
  }

  /* ---------- Mapa de calor con zoom ----------
   * La geometría (imagen de calor, municipios, contorno, zona) se dibuja una sola vez y al
   * hacer zoom o arrastrar solo cambia su transformación; lo único que se redibuja en cada
   * cuadro es la capa ligera de marcas y rótulos.
   * o = { limite, municipios, puntos:[{id,nombre,lon,lat,valor,sel}], punto?:{lon,lat,texto}, fmt, unidad,
   *       tip(lon,lat,valor,municipio), tipEstacion(p), onPunto(lon,lat), onClic(p), desc } */
  function mapa(el, o) {
    const lons = o.limite.map((p) => p[0]), lats = o.limite.map((p) => p[1]);
    const lon0 = Math.min(...lons), lon1 = Math.max(...lons), lat0 = Math.min(...lats), lat1 = Math.max(...lats);
    const lonC = (lon0 + lon1) / 2, latC = (lat0 + lat1) / 2;
    const k = Math.cos(latC * Math.PI / 180);
    const anchoDisp = Math.max(el.clientWidth || 560, 280);
    const pie = 56, margen = 8;
    const esc = Math.min((anchoDisp - 2 * margen) / ((lon1 - lon0) * k), (o.alto || 680) / (lat1 - lat0));
    const mw = (lon1 - lon0) * k * esc, mh = (lat1 - lat0) * esc;
    const g = lienzo(el, mh + pie + margen, { t: margen, r: 0, b: pie, l: 0 }, o.desc);
    const cx = g.w / 2, cy = margen + mh / 2;

    // Proyección base (sin zoom): en ella se dibuja la geometría
    const X0 = (lon) => cx + (lon - lonC) * k * esc, Y0 = (lat) => cy + (latC - lat) * esc;
    // Vista: zoom y centro (lon, lat) se conservan entre redibujados
    const v = vistas.get(el) || { z: 1, lon: lonC, lat: latC, arrastre: 0 };
    vistas.set(el, v);
    // Enfoque pedido por la aplicación (por ejemplo, al elegir un departamento): se aplica una sola vez por
    // cambio, así el usuario puede seguir moviendo el mapa. null vuelve a mostrar todo el mapa.
    if (o.enfoque !== undefined && v.enfoque !== (o.enfoque ? o.enfoque.clave : null)) {
      v.enfoque = o.enfoque ? o.enfoque.clave : null;
      if (o.enfoque) {
        const e = o.enfoque, ew = Math.max((e.lon1 - e.lon0) * k * esc, 1), eh = Math.max((e.lat1 - e.lat0) * esc, 1);
        v.z = Math.min(ZOOM_MAX, Math.max(1, 0.8 * Math.min(g.w / ew, mh / eh)));
        v.lon = (e.lon0 + e.lon1) / 2; v.lat = (e.lat0 + e.lat1) / 2;
      } else { v.z = 1; v.lon = lonC; v.lat = latC; }
    }
    const X = (lon) => cx + (lon - v.lon) * k * esc * v.z, Y = (lat) => cy + (v.lat - lat) * esc * v.z;
    const lonDe = (x) => v.lon + (x - cx) / (k * esc * v.z), latDe = (y) => v.lat - (y - cy) / (esc * v.z);
    const acotar = (c, a, b, semi) => (b - a <= 2 * semi ? (a + b) / 2 : Math.min(b - semi, Math.max(a + semi, c)));

    const vals = o.puntos.map((p) => p.valor), vmin = Math.min(...vals), vmax = Math.max(...vals);
    const sel = o.puntos.find((p) => p.sel);
    // Estaciones insulares (San Andrés): quedan al oeste del continente y se dibujan en su recuadro, no en el mapa
    const insular = (p) => p.lon < lon0 - 0.3;
    const pMapa = o.puntos.filter((p) => !insular(p)), ptsIsla = o.puntos.filter(insular);
    const munis = o.municipios || [], deptos = o.departamentos || [];
    // Caja de cada municipio (se calcula una vez): al pasar el cursor evita probar los 1.100 contornos
    munis.forEach((m) => {
      if (m.b) return;
      const xs = m.p.map((q) => q[0]), ys = m.p.map((q) => q[1]);
      m.b = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    });
    const denso = munis.length > 300;      // país entero: líneas más tenues y menos rótulos

    // Imagen de calor: resolución fija en la proyección base
    const rw = 360, rh = Math.round(rw * mh / mw);
    const pr = pMapa.map((p) => ({ valor: p.valor, bx: X0(p.lon), by: Y0(p.lat), rx: (X0(p.lon) - X0(lon0)) * rw / mw, ry: (Y0(p.lat) - Y0(lat1)) * rh / mh }));
    const campo = (bx, by) => {           // valor interpolado en coordenadas base, para el cuadro emergente
      let num = 0, den = 0;
      for (const p of pr) {
        const d2 = (bx - p.bx) ** 2 + (by - p.by) ** 2;
        if (d2 < 1) return p.valor;
        num += p.valor / d2; den += 1 / d2;
      }
      return num / den;
    };

    const id = 'mapa-' + (++nMapa);
    const defs = s('defs', {}, g.svg);
    const ruta = (anillo) => 'M' + anillo.map((q) => `${X0(q[0]).toFixed(1)},${Y0(q[1]).toFixed(1)}`).join('L') + 'Z';
    const contorno = ruta(o.limite);
    s('path', { d: contorno }, s('clipPath', { id: id + '-clip' }, defs));
    s('rect', { x: 0, y: 0, width: g.w, height: margen + mh + 4 }, s('clipPath', { id: id + '-vista' }, defs));
    const grad = s('linearGradient', { id: id + '-grad' }, defs);
    RAMPA.forEach((_, i) => { const t = i / (RAMPA.length - 1); s('stop', { offset: t, 'stop-color': colorRampa(t) }, grad); });

    const vista = s('g', { 'clip-path': `url(#${id}-vista)` }, g.svg);
    const geo = s('g', {}, vista);                         // geometría: solo cambia su transformación
    const capa = s('g', {}, vista);                        // marcas y rótulos: se redibuja en cada cuadro
    const dentro = s('g', { 'clip-path': `url(#${id}-clip)` }, geo);
    const fijo = { 'vector-effect': 'non-scaling-stroke', 'pointer-events': 'none', 'stroke-linejoin': 'round' };
    s('image', { href: rasterCalor(pr, rw, rh, vmin, vmax), x: X0(lon0), y: Y0(lat1), width: mw, height: mh, preserveAspectRatio: 'none', 'pointer-events': 'none' }, dentro);
    s('path', { ...fijo, d: munis.map((m) => ruta(m.p)).join(''), fill: 'none', stroke: C.marino, 'stroke-width': denso ? 0.5 : 0.7, 'stroke-opacity': denso ? 0.16 : 0.28 }, dentro);
    if (deptos.length) s('path', { ...fijo, d: deptos.map((d) => ruta(d.p)).join(''), fill: 'none', stroke: C.marino, 'stroke-width': 1.1, 'stroke-opacity': 0.6 }, dentro);

    if (sel && !insular(sel)) {
      // Zona de la estación: el rectángulo del mapa recortado con la mediatriz frente a cada otra estación
      const a = { x: X0(sel.lon), y: Y0(sel.lat) };
      let pol = [[X0(lon0) - 5, Y0(lat1) - 5], [X0(lon1) + 5, Y0(lat1) - 5], [X0(lon1) + 5, Y0(lat0) + 5], [X0(lon0) - 5, Y0(lat0) + 5]];
      for (const p of pMapa) {
        if (p === sel || !pol.length) continue;
        const b = { x: X0(p.lon), y: Y0(p.lat) }, nx = b.x - a.x, ny = b.y - a.y, c = nx * (a.x + b.x) / 2 + ny * (a.y + b.y) / 2;
        const lado = (q) => nx * q[0] + ny * q[1] - c, out = [];
        pol.forEach((q, i) => {
          const r = pol[(i + 1) % pol.length], dq = lado(q), dr = lado(r);
          if (dq <= 0) out.push(q);
          if ((dq < 0 && dr > 0) || (dq > 0 && dr < 0)) { const t = dq / (dq - dr); out.push([q[0] + (r[0] - q[0]) * t, q[1] + (r[1] - q[1]) * t]); }
        });
        pol = out;
      }
      const dz = 'M' + pol.map((q) => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join('L') + 'Z';
      s('path', { ...fijo, d: dz, fill: 'none', stroke: C.fondo, 'stroke-width': 5 }, dentro);
      s('path', { ...fijo, d: dz, fill: 'none', stroke: C.marino, 'stroke-width': 2.5 }, dentro);
    }
    s('path', { ...fijo, d: contorno, fill: 'none', stroke: C.marino, 'stroke-width': 1.5 }, geo);

    // Todo el departamento es zona de clic: marca la ubicación del hogar
    let arrastrando = false;
    const area = s('path', { d: contorno, fill: 'transparent', 'data-clic': 1 }, geo);
    const aMapa = (ev) => {
      const r = g.svg.getBoundingClientRect(), f = g.w / r.width;
      const x = (ev.clientX - r.left) * f, y = (ev.clientY - r.top) * f;
      return { x, y, lon: lonDe(x), lat: latDe(y) };
    };
    const enPoligono = (lon, lat, r) => {
      let c = false;
      for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
        if ((r[i][1] > lat) !== (r[j][1] > lat) && lon < (r[j][0] - r[i][0]) * (lat - r[i][1]) / (r[j][1] - r[i][1]) + r[i][0]) c = !c;
      }
      return c;
    };
    area.addEventListener('pointermove', (ev) => {
      if (arrastrando) return;
      const q = aMapa(ev), m = munis.find((mu) => q.lon >= mu.b[0] && q.lon <= mu.b[2] && q.lat >= mu.b[1] && q.lat <= mu.b[3] && enPoligono(q.lon, q.lat, mu.p));
      const t = o.tip(q.lon, q.lat, campo(X0(q.lon), Y0(q.lat)), m ? (m.d !== undefined && deptos[m.d] ? `${m.n} · ${deptos[m.d].n}` : m.n) : null);
      mostrarTip(ev, t.titulo, t.filas);
    });
    area.addEventListener('pointerleave', ocultarTip);
    area.addEventListener('click', (ev) => { if (Date.now() - v.arrastre < 350) return; const q = aMapa(ev); o.onPunto(q.lon, q.lat); });

    /* ----- Islas (San Andrés y Providencia): recuadros ampliados abajo a la izquierda, fuera del zoom -----
     * Están a ~700 km del continente y miden pocos kilómetros: en el mapa del país no se verían. Cada isla se
     * dibuja a su propia escala, con el color de la radiación de su estación, y se puede tocar como el resto. */
    function dibujarIslas() {
      const islas = o.islas || [];
      if (!islas.length) return;
      const tam = Math.round(Math.max(84, Math.min(124, g.w * 0.19))), franja = 15, yBase = margen + mh - tam - 6;
      const capaI = s('g', {}, g.svg);
      const punto = (ev) => { const r = g.svg.getBoundingClientRect(), f = g.w / r.width; return { x: (ev.clientX - r.left) * f, y: (ev.clientY - r.top) * f }; };
      islas.forEach((isla, i) => {
        const x0 = 8 + i * (tam + 8), todos = isla.p.flat();
        const b = [Math.min(...todos.map((q) => q[0])), Math.min(...todos.map((q) => q[1])), Math.max(...todos.map((q) => q[0])), Math.max(...todos.map((q) => q[1]))];
        const cLon = (b[0] + b[2]) / 2, cLat = (b[1] + b[3]) / 2, kI = Math.cos(cLat * Math.PI / 180);
        const sc = Math.min((tam - 10) / Math.max((b[2] - b[0]) * kI, 1e-6), (tam - franja - 8) / Math.max(b[3] - b[1], 1e-6)) * 0.78;
        const ox = x0 + tam / 2, oy = yBase + franja + (tam - franja) / 2;
        const PX = (lon) => ox + (lon - cLon) * kI * sc, PY = (lat) => oy + (cLat - lat) * sc;
        const lonI = (x) => cLon + (x - ox) / (kI * sc), latI = (y) => cLat - (y - oy) / sc;
        const dentroDe = (p) => p.lon >= b[0] - 0.02 && p.lon <= b[2] + 0.02 && p.lat >= b[1] - 0.02 && p.lat <= b[3] + 0.02;
        const cercana = ptsIsla.length ? ptsIsla.reduce((a, p) => (Math.hypot(p.lon - cLon, p.lat - cLat) < Math.hypot(a.lon - cLon, a.lat - cLat) ? p : a)) : null;
        const valor = cercana ? cercana.valor : campo(X0(cLon), Y0(cLat));
        const nombre = (deptos[isla.d] ? `${isla.n} · ${deptos[isla.d].n}` : isla.n);
        const aqui = sel && insular(sel) && dentroDe(sel);

        s('rect', { x: x0, y: yBase, width: tam, height: tam, rx: 8, fill: '#eaf2fb', 'fill-opacity': 0.96, stroke: aqui ? C.marino : '#c9d9ec', 'stroke-width': aqui ? 2.5 : 1 }, capaI);
        s('text', { x: x0 + 8, y: yBase + 11, 'font-size': 10.5, 'font-weight': 700, fill: C.texto2, 'pointer-events': 'none' }, capaI, isla.n.split(' y ')[0]);
        s('path', { d: isla.p.map((r) => 'M' + r.map((q) => `${PX(q[0]).toFixed(1)},${PY(q[1]).toFixed(1)}`).join('L') + 'Z').join(''), fill: colorRampa(Math.min(1, Math.max(0, (valor - vmin) / ((vmax - vmin) || 1)))), stroke: C.marino, 'stroke-width': 1.3, 'stroke-linejoin': 'round', 'pointer-events': 'none' }, capaI);

        // Toda la caja es zona de clic: marca la ubicación del hogar en la isla
        const zona = s('rect', { x: x0, y: yBase, width: tam, height: tam, rx: 8, fill: 'transparent', 'data-clic': 1, style: 'cursor:pointer' }, capaI);
        zona.addEventListener('pointermove', (ev) => { const q = punto(ev), t = o.tip(lonI(q.x), latI(q.y), valor, nombre); mostrarTip(ev, t.titulo, t.filas); });
        zona.addEventListener('pointerleave', ocultarTip);
        zona.addEventListener('click', (ev) => { const q = punto(ev); o.onPunto(lonI(q.x), latI(q.y)); });

        ptsIsla.filter(dentroDe).forEach((p) => {
          const sx = PX(p.lon), sy = PY(p.lat), elegida = p.sel;
          const c = s('circle', { cx: sx, cy: sy, r: elegida ? 7 : 5, fill: elegida ? C.marino : C.fondo, stroke: elegida ? C.fondo : C.marino, 'stroke-width': elegida ? 3 : 2, 'data-clic': 1, tabindex: 0, role: 'button', style: 'cursor:pointer', 'aria-label': `Estación ${p.nombre}: ${o.fmt(p.valor)} ${o.unidad}` }, capaI);
          c.addEventListener('pointermove', (ev) => { ev.stopPropagation(); const t = o.tipEstacion(p); mostrarTip(ev, t.titulo, t.filas); });
          c.addEventListener('pointerleave', ocultarTip);
          c.addEventListener('click', (ev) => { ev.stopPropagation(); o.onClic(p); });
          c.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); o.onClic(p); } });
        });
        if (aqui) s('text', { x: x0 + tam / 2, y: yBase + tam - 6, 'text-anchor': 'middle', 'font-size': 10.5, 'font-weight': 700, fill: C.marino, 'pointer-events': 'none' }, capaI, `${o.fmt(sel.valor)} ${o.unidad.split(' ')[0]}`);
        if (o.punto && insular(o.punto) && dentroDe(o.punto)) {
          const pin = s('g', { transform: `translate(${PX(o.punto.lon)},${PY(o.punto.lat)})`, 'pointer-events': 'none' }, capaI);
          s('path', { d: 'M0,0C-11,-13 -10,-27 0,-27C10,-27 11,-13 0,0Z', fill: C.coral, stroke: C.fondo, 'stroke-width': 2.5, 'stroke-linejoin': 'round' }, pin);
          s('circle', { cx: 0, cy: -17, r: 3.6, fill: C.fondo }, pin);
        }
      });
    }
    dibujarIslas();

    // Escala de color (fija, fuera de la vista con zoom)
    const lw = Math.min(340, g.w - 40), lx = (g.w - lw) / 2, ly = margen + mh + 20;
    s('rect', { x: lx, y: ly, width: lw, height: 12, rx: 6, fill: `url(#${id}-grad)` }, g.svg);
    s('text', { x: lx, y: ly + 29, 'font-size': 13, fill: C.texto2, 'text-anchor': 'start' }, g.svg, o.fmt(vmin));
    s('text', { x: lx + lw, y: ly + 29, 'font-size': 13, fill: C.texto2, 'text-anchor': 'end' }, g.svg, o.fmt(vmax));
    s('text', { x: lx + lw / 2, y: ly + 29, 'font-size': 13, fill: C.texto3, 'text-anchor': 'middle' }, g.svg, o.unidad);
    if (sel) {
      const sx = lx + lw * ((sel.valor - vmin) / ((vmax - vmin) || 1));
      s('path', { d: `M${sx},${ly - 1}l-7,-10h14Z`, fill: C.marino, stroke: C.fondo, 'stroke-width': 1.5, 'stroke-linejoin': 'round' }, g.svg);
    }

    // Botones de zoom
    el.classList.add('mapa');
    const barra = document.createElement('div');
    barra.className = 'mapa-zoom';
    const botones = [['+', 'Acercar', () => zoomEn(v.z * 1.6, cx, cy)], ['−', 'Alejar', () => zoomEn(v.z / 1.6, cx, cy)],
      ['⟲', 'Ver todo el mapa', () => { v.z = 1; v.lon = lonC; v.lat = latC; programar(); }]].map(([txt, nombre, accion]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = txt; b.title = nombre; b.setAttribute('aria-label', nombre);
      b.addEventListener('click', accion);
      barra.appendChild(b);
      return b;
    });
    el.appendChild(barra);

    /* Capa ligera: línea de distancia, estaciones, marcador y rótulos en coordenadas de pantalla. */
    const halo = { stroke: C.fondo, 'stroke-width': 4, 'paint-order': 'stroke', 'stroke-linejoin': 'round', 'pointer-events': 'none' };
    function pintarCapa() {
      capa.textContent = '';
      const rotulos = s('g', {}, capa);         // rótulos de municipio, por debajo de marcas y etiquetas
      const pts = pMapa.map((p) => ({ ...p, x: X(p.lon), y: Y(p.lat) }));
      const ps = pts.find((p) => p.sel);
      // Cajas ya ocupadas, para que los rótulos de municipio no se monten sobre nada
      const cajas = pts.map((p) => ({ x: p.x - 10, y: p.y - 10, w: 20, h: 20 }));
      const libre = (c) => !cajas.some((d) => c.x < d.x + d.w && d.x < c.x + c.w && c.y < d.y + d.h && d.y < c.y + c.h);

      // Ubicación elegida: línea hasta la estación más cercana con la distancia
      let distCorta = '';
      if (o.punto && ps && !insular(o.punto)) {
        const px = X(o.punto.lon), py = Y(o.punto.lat);
        s('line', { x1: px, y1: py, x2: ps.x, y2: ps.y, stroke: C.fondo, 'stroke-width': 6, 'stroke-linecap': 'round', 'pointer-events': 'none' }, capa);
        s('line', { x1: px, y1: py, x2: ps.x, y2: ps.y, stroke: C.marino, 'stroke-width': 2.5, 'stroke-dasharray': '7 6', 'stroke-linecap': 'round', 'pointer-events': 'none' }, capa);
        const texto = o.punto.texto, tw = texto.length * 8.4 + 8;
        cajas.push({ x: px - 12, y: py - 30, w: 24, h: 32 });
        if (Math.hypot(ps.x - px, ps.y - py) > 60) {
          const tx = Math.min(g.w - tw / 2, Math.max(tw / 2, (px + ps.x) / 2)), ty = (py + ps.y) / 2 - 10;
          s('text', { ...halo, x: tx, y: ty, 'text-anchor': 'middle', 'font-size': 14.5, 'font-weight': 700, fill: C.marino }, capa, texto);
          cajas.push({ x: tx - tw / 2, y: ty - 15, w: tw, h: 20 });
        } else distCorta = ' · ' + texto;        // línea muy corta: la distancia va en la etiqueta de la estación
      }

      pts.filter((p) => !p.sel).forEach((p) => {
        const c = s('circle', { cx: p.x, cy: p.y, r: 5, fill: C.fondo, stroke: C.marino, 'stroke-width': 2, 'data-clic': 1, tabindex: 0, role: 'button', 'aria-label': `Estación ${p.nombre}: ${o.fmt(p.valor)} ${o.unidad}` }, capa);
        c.addEventListener('pointermove', (ev) => { if (arrastrando) return; const t = o.tipEstacion(p); mostrarTip(ev, t.titulo, t.filas); });
        c.addEventListener('pointerleave', ocultarTip);
        c.addEventListener('click', () => { if (Date.now() - v.arrastre >= 350) o.onClic(p); });
        c.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); o.onClic(p); } });
      });
      if (ps) {
        s('circle', { cx: ps.x, cy: ps.y, r: 9, fill: C.marino, stroke: C.fondo, 'stroke-width': 3, 'pointer-events': 'none' }, capa);
        // La etiqueta va a la derecha, a la izquierda o centrada encima, según dónde quepa
        const linea2 = `${o.fmt(ps.valor)} ${o.unidad}${distCorta}`;
        const tw = Math.max(ps.nombre.length * 8.6, linea2.length * 7.6);
        let pos = { x: ps.x + 16, anchor: 'start', dy: 0 };
        if (ps.x + 16 + tw > g.w) pos = { x: ps.x - 16, anchor: 'end', dy: 0 };
        if (pos.anchor === 'end' && ps.x - 16 - tw < 0) pos = { x: Math.min(g.w - tw / 2, Math.max(tw / 2, ps.x)), anchor: 'middle', dy: -34 };
        const et = { ...halo, x: pos.x, 'text-anchor': pos.anchor, fill: C.marino };
        s('text', { ...et, y: ps.y - 2 + pos.dy, 'font-size': 15, 'font-weight': 700 }, capa, ps.nombre);
        s('text', { ...et, y: ps.y + 17 + pos.dy, 'font-size': 14, 'font-weight': 600 }, capa, linea2);
        const x0 = pos.anchor === 'start' ? pos.x : pos.anchor === 'end' ? pos.x - tw : pos.x - tw / 2;
        cajas.push({ x: x0, y: ps.y - 18 + pos.dy, w: tw, h: 40 });
      }
      if (o.punto && !insular(o.punto)) {
        const pin = s('g', { transform: `translate(${X(o.punto.lon)},${Y(o.punto.lat)})`, 'pointer-events': 'none' }, capa);
        s('path', { d: 'M0,0C-11,-13 -10,-27 0,-27C10,-27 11,-13 0,0Z', fill: C.coral, stroke: C.fondo, 'stroke-width': 2.5, 'stroke-linejoin': 'round' }, pin);
        s('circle', { cx: 0, cy: -17, r: 3.6, fill: C.fondo }, pin);
      }

      // Nombres de departamento (solo con el mapa alejado): en mayúsculas y tenues, para orientar sin competir
      if (deptos.length && v.z < 2.6) {
        deptos.forEach((d) => {
          const w = d.n.length * 7 + 4, dx = X(d.c[0]), dy = Y(d.c[1]);
          if (dx < w / 2 || dx > g.w - w / 2 || dy < 12 || dy > margen + mh - 6) return;
          const c = { x: dx - w / 2, y: dy - 7, w, h: 14 };
          if (!libre(c)) return;
          cajas.push(c);
          s('text', { x: dx, y: dy + 4, 'text-anchor': 'middle', 'font-size': 10.5, 'font-weight': 600, 'letter-spacing': '0.06em', fill: C.texto3, stroke: C.fondo, 'stroke-width': 2.5, 'stroke-opacity': 0.8, 'paint-order': 'stroke', 'pointer-events': 'none' }, rotulos, d.n.toUpperCase());
        });
      }

      // Rótulos de municipio por prioridad, solo los visibles y que caben sin tapar nada.
      // Con el mapa acercado se rotulan todos; alejado, solo los de referencia (en el país entero, las capitales).
      const pri = (m) => (m.r === undefined ? 999 : m.r);
      munis.filter((m) => (denso ? v.z >= 3 || (v.z >= 1.6 && m.r !== undefined) : v.z >= 1.8 || m.r !== undefined))
        .filter((m) => { const x = X(m.c[0]), y = Y(m.c[1]); return x > -40 && x < g.w + 40 && y > -30 && y < margen + mh + 30; })
        .sort((a, b) => pri(a) - pri(b)).slice(0, g.w < 440 && v.z < 1.5 ? 12 : denso ? 260 : 999).forEach((m) => {
        const w = m.n.length * 6.5 + 6, mx = X(m.c[0]), my = Y(m.c[1]);
        if (mx < -w || mx > g.w + w || my < -30 || my > margen + mh + 30) return;
        // Se prueba el centro del municipio y, si está ocupado, justo encima o debajo;
        // solo el de mayor prioridad puede desplazarse a los lados (más lejos confundiría)
        const c = [[0, 0], [0, -17], [0, 17], ...(m.r === 0 && v.z < 1.8 ? [[-w * 0.7, 0], [w * 0.7, 0], [-w * 0.7, -17], [w * 0.7, 17]] : [])]
          .map(([dx, dy]) => ({ x: mx + dx - w / 2, y: my + dy - 9, w, h: 17 }))
          .find((q) => q.x >= 0 && q.x + w <= g.w && q.y >= 0 && q.y + q.h <= margen + mh && libre(q));
        if (!c) return;
        cajas.push(c);
        s('text', { x: c.x + w / 2, y: c.y + 13, 'text-anchor': 'middle', 'font-size': 12, 'font-weight': 500, fill: C.texto2, stroke: C.fondo, 'stroke-width': 3, 'stroke-opacity': 0.85, 'paint-order': 'stroke', 'stroke-linejoin': 'round', 'pointer-events': 'none' }, rotulos, m.n);
      });
    }

    /* Aplica la vista actual: mueve la geometría y repinta la capa ligera. */
    function aplicar() {
      v.lon = acotar(v.lon, lon0, lon1, g.w / 2 / (k * esc * v.z));
      v.lat = acotar(v.lat, lat0, lat1, mh / 2 / (esc * v.z));
      // pantalla = z · base + t
      const tx = cx - v.z * (cx + (v.lon - lonC) * k * esc), ty = cy - v.z * (cy + (latC - v.lat) * esc);
      geo.setAttribute('transform', `matrix(${v.z},0,0,${v.z},${tx.toFixed(2)},${ty.toFixed(2)})`);
      pintarCapa();
      botones[0].disabled = v.z >= ZOOM_MAX; botones[1].disabled = botones[2].disabled = v.z <= 1;
      g.svg.style.touchAction = v.z > 1 ? 'none' : 'auto';
      g.svg.style.cursor = arrastrando ? 'grabbing' : v.z > 1 ? 'grab' : '';
    }
    let pendiente = 0;
    const programar = () => { if (!pendiente) pendiente = requestAnimationFrame(() => { pendiente = 0; aplicar(); }); };

    // ----- Zoom: rueda del ratón, botones y arrastre para desplazarse -----
    function zoomEn(z, x, y) {
      z = Math.min(ZOOM_MAX, Math.max(1, z));
      if (z === v.z) return false;
      const lon = lonDe(x), lat = latDe(y);          // el punto bajo el cursor no se mueve
      v.z = z;
      v.lon = lon - (x - cx) / (k * esc * z);
      v.lat = lat + (y - cy) / (esc * z);
      ocultarTip(); programar();
      return true;
    }
    g.svg.addEventListener('wheel', (ev) => {
      const q = aMapa(ev);
      if (q.y > margen + mh) return;
      // En los límites del zoom la rueda vuelve a desplazar la página
      if (zoomEn(v.z * Math.exp(-ev.deltaY * 0.002), q.x, q.y)) ev.preventDefault();
    }, { passive: false });

    let ini = null;
    g.svg.addEventListener('pointerdown', (ev) => { if (v.z > 1 && ev.button === 0) ini = { x: ev.clientX, y: ev.clientY, id: ev.pointerId, lon: v.lon, lat: v.lat }; });
    g.svg.addEventListener('pointermove', (ev) => {
      if (!ini) return;
      const f = g.w / g.svg.getBoundingClientRect().width, dx = (ev.clientX - ini.x) * f, dy = (ev.clientY - ini.y) * f;
      if (!arrastrando && Math.hypot(dx, dy) < 5) return;
      if (!arrastrando) { arrastrando = true; try { g.svg.setPointerCapture(ini.id); } catch (e) { /* puntero ya liberado */ } ocultarTip(); }
      v.lon = ini.lon - dx / (k * esc * v.z); v.lat = ini.lat + dy / (esc * v.z);
      programar();
    });
    const soltar = () => {
      if (arrastrando) { arrastrando = false; v.arrastre = Date.now(); programar(); }
      ini = null;
    };
    g.svg.addEventListener('pointerup', soltar);
    g.svg.addEventListener('pointercancel', soltar);

    aplicar();
  }

  root.Graficos = { C, columnas, barrasH, dumbbell, lineas, mapa };
})(window);
