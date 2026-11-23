# Contrato de datos y decisiones — Calcusol

Documento vivo. Registra las decisiones de datos tomadas **con evidencia**, para
que no se reabran en cada sesión. Cada decisión dice qué se decidió, por qué, y
dónde está la evidencia.

---

## 1. Arquitectura y entorno

- **Medallion por zonas**: Bronze (crudo, intacto) → Silver (limpio y
  estandarizado) → Gold (integrado y listo para ML).
- **Local-first, portable a AWS**: el código corre hoy con Docker + PySpark
  sobre `data/`; las rutas se resuelven por configuración para apuntar mañana a
  `s3://`. Glue para Silver/Gold, Step Functions para orquestar, entrenamiento
  fuera de Glue.
- **Regla de portabilidad**: la lógica de transformación es una función pura
  `(df, config) -> df` en PySpark; el acople a Glue/local/Step Functions vive
  solo en los *entry points* (`src/calcusol/aws/`, `orchestration/`).

---

## 2. Fuentes

| Fuente | Rol | Ubicación Bronze |
|---|---|---|
| NASA POWER (horaria) | Predictoras X + baseline | `data/bronze/nasa/*.json` |
| IDEAM (RAD SOLAR) | Objetivo Y | `data/bronze/ideam/mediciones/<Depto>/*.csv` |
| Catálogo CNE IDEAM | Coordenadas de estación | `data/bronze/ideam/catalogo/CNE_IDEAM.csv` |

Variables NASA: `ALLSKY_SFC_SW_DWN`, `CLRSKY_SFC_SW_DWN`, `ALLSKY_KT`, `T2M`.

---

## 3. Decisiones cerradas con evidencia

### D-TEMPORAL — Alineación del merge NASA ↔ IDEAM

**Decisión:** descargar NASA en `UTC`, convertir a hora civil de Colombia
(**−5 h**) y **sumar +1 h** por la convención de etiquetado de intervalo. Neto:
**−4 h** sobre el timestamp UTC de NASA.

**Por qué:**
- NASA etiqueta cada hora con el **inicio** del intervalo; el IDEAM con el
  **fin**. Para que ambos describan el mismo intervalo físico hay que correr la
  etiqueta de NASA +1 h, además del offset de zona horaria −5 h.

**Evidencia** (`reports/auditoria_alineacion_nasa/`,
`reports/_auditorias_legacy/inspeccion_estandar_nasa.py`):
- En UTC crudo el pico medio de radiación cae a la **hora 16** (coherente con
  mediodía solar ~17 h UTC). Aplicando −4 h, el pico queda centrado en la
  **hora 12** local.
- Correlación NASA↔IDEAM (3 estaciones, 2017–2019): sube de ~0.84–0.90 (desfase
  0) a **0.89–0.95** (desfase +1); el MAE baja ~40 %.

**Alternativa considerada y descartada:** usar el estándar `LST` de NASA (hora
solar). Da un pico crudo a la hora 11, cercano al mediodía sin ajuste, pero el
código ya usa UTC y da el resultado correcto verificado. Se descarta por
simplicidad (menos piezas móviles). Queda documentada por transparencia.

**Matiz honesto:** el −4 h es una constante global. Para estaciones en
longitudes extremas de Colombia podría quedar ~20–30 min descentrada. Aceptable
para un modelo horario; anotado como supuesto.

### D-AÑO — Año de corte del dataset de entrenamiento

**Decisión:** el dataset de ML empieza en **2005**.

**Por qué / evidencia** (`reports/auditoria_cobertura_temporal/`):
- Antes de 2005: **≤4 estaciones por año**, **2004 vacío**, y **100 %
  Preliminar** (0 Definitivos). Representan solo **0.68 %** de las filas totales.
- 2005 arranca con **58 estaciones** y cobertura nacional real.
- Piso duro independiente: NASA POWER horaria existe desde **2001/01/01**
  (documentación oficial), así que antes de 2001 no hay X satelital.

**Nota para validación (R2):** la cobertura decae tras 2016 (119 estaciones en
2016 → 44 en 2022). El split del plan (train ≤2019, test 2020–2022) deja un test
con 44–51 estaciones: evaluación espacial más exigente, no un error.

### D-PRELIMINARES — Uso de registros Preliminar

**Estado:** PENDIENTE (decisión de modelado, no de ingesta).
- Bronze y Silver **conservan** los Preliminar, marcados en `EsPreliminar`.
- La decisión final sale de un A/B en modelado: entrenar con/sin preliminares y
  aceptarlos si no empeoran el error > 2 % (plan D0/D8).
- Distribución medida: 11–24 % de las filas desde 2005.

---

## 4. Reglas de limpieza heredadas del código de referencia

El código en `codigo_propuesto/` (pandas, del compañero) se adopta como
**especificación de referencia** y se porta a PySpark. Sus reglas, validadas
contra las auditorías previas:

| Regla | Valor | Validación |
|---|---|---|
| Valor imposible IDEAM | validar el rango físico y descartar valores fuera del umbral | máximo real observado = 1181.8 Wh/m²; no hubo valores `> 1400` en la descarga analizada |
| Negativos y ceros | NO se tocan | auditoría 1.4: no hay negativos; ceros nocturnos esperables |
| Duplicados exactos | eliminar (filas idénticas en todos los campos) | auditoría 1.5: 18 en estación 52025090, con evidencia del conteo |
| Conflicto estación-hora | colapsar al de mayor aprobación; **conteo reportado en el embudo** | justificación física + requisito de unicidad para el cruce NASA |
| Estaciones cercanas (<0.5 km) | NO fusionar | auditoría 1.6: series idénticas pero se mantienen independientes |
| Coordenadas del catálogo | coma decimal → punto; altitud quita punto de miles | auditoría 1.1 |
| Encoding CSV IDEAM | **UTF-8** (confirmado por bytes `C3 B3` = "ó") | el mojibake visto en consola era render de PowerShell, no del archivo |
| Fechas | formatos mixtos ISO y D/M/Y | `try_to_timestamp` en Spark 4 (parser ANSI) |
| Timestamp IDEAM | se conserva original (hora local); el −4h se aplica solo a NASA | auditoría 1.7 |
| VALIDADA vs Preliminar | `EsPreliminar` solo desde `NivelAprobacion`, nunca `Parametro` | auditoría 1.2 |

---

## 5. Contrato de columnas Gold (17)

Definido en `config/schema.py`. Fuente única de verdad para train/test.

`CodigoEstacion, NombreEstacion, Departamento, Fecha, EsPreliminar,
ALLSKY_SFC_SW_DWN, CLRSKY_SFC_SW_DWN, ALLSKY_KT, T2M, Latitud, Longitud,
Altitud, Hora, Mes, DiaDelAnio, Sitio, Valor`

- X: las 4 variables NASA + Latitud, Longitud, Altitud, Hora, Mes (, DiaDelAnio).
- Y: `Valor` (radiación IDEAM, Wh/m²).
- Agrupador espacial: `Sitio` (estaciones a < 1 km = un sitio), para GroupKFold.

---

## 6. Pendientes de reconciliar

- **Conteo de estaciones 136 → 132** RESUELTO: el audit contó 136 estaciones con
  archivos de radiación sobre todo el periodo; Silver aplica el corte 2005 y
  quedan **132**. Las 4 que faltan solo tenían datos pre-2005 (coherente con la
  Auditoría B: 2–4 estaciones/año antes de 2005). `estaciones sin coordenadas: 0`.
  El "128" del plan (D19) venía de otra tabla; el número correcto bajo estas
  reglas es 132.
- **Embudo de filas** (verificado en Silver-IDEAM):
  - crudo RAD SOLAR: 7,600,984
  - −18 duplicados exactos → 7,600,966 (coincide con auditoría 1.5)
  - 0 conflictos estación-hora, 0 valores imposibles
  - corte 2005: −51,420 (0.68 %) → **7,549,564**
- **Estaciones eliminadas por corte 2005** (4, solo datos pre-2005):
  17035010 Serranilla, 31095030 Puerto Inírida, 44035010 Florencia, 57025020
  Gorgona Guapi. Evidencia en `reports/estaciones_eliminadas_corte_2005/`.

### D-NASA-COMPARTIDA — Estaciones que comparten celda NASA

8 pares de estaciones están tan próximas (< ~11 m) que al redondear coordenadas a
4 decimales comparten el mismo archivo NASA (y la misma celda de ~50 km, plan
D10 / auditoría 1.6). 136 estaciones → 128 coordenadas únicas. En Silver-NASA el
JSON compartido se asigna a AMBAS estaciones del par (no a una), para no perder
ninguna. Pares:
3.1331/-75.0815 (21115160,21115180) · 5.0905/-73.8812 (21205970,21206790) ·
6.5339/-75.8273 (26225030,26225060) · 7.1572/-75.4423 (26255020,26255030) ·
6.2246/-75.5882 (27015070,27015330) · 4.1372/-73.6250 (35030370,35035100) ·
1.5000/-75.6600 (44035030,44035050) · 2.0328/-77.2217 (52010020,52015050).

### Dataset Gold final (verificado end-to-end)

Tras la cadena Bronze→Silver→Gold con la corrección de estaciones compartidas:
- Silver NASA: 136 estaciones, 21,457,536 filas, rango 2004-12-31 20:00 →
  2022-12-31 19:00 (confirma el ajuste −4 h).
- Gold integrado: 7,549,564 filas IDEAM, **100 %** con dato NASA.
- Gold dataset (ML): embudo 7,549,564 → horas de sol 3,840,668 (G1) → sin vacíos
  → sin negativos = **3,840,668 filas · 132 estaciones · 123 sitios · 17 columnas**.
- Particionado por año en `data/gold/dataset/`.
