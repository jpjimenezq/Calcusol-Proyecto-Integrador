# Partición del dataset y evaluación del modelo — Calcusol (R1/R2)

Documento para explicar, a quien evalúa, **qué predice el modelo**, **cómo se
divide el dataset** para entrenarlo, **por qué** se eligió ese esquema, y **cómo
se mide** el resultado. Incluye la comparación con el esquema anterior (el que
proponía el plan inicial) y las razones del cambio.

Fuente de verdad del código: `src/calcusol/ml/features.py`,
`src/calcusol/ml/splits.py` y `src/calcusol/ml/evaluate.py`.

---

## 1. Resumen en una frase

Pasamos de un **split puramente temporal** (entrenar con 2005–2019, validar con
2020–2022) a un **split temporal + espacial estratificado** (split 1b), porque el
primero solo medía "¿acierta en años nuevos de estaciones que ya conoce?" y el
caso de uso real del proyecto es "¿acierta en lugares donde **no hay** estación?".

---

## 2. Qué predice el modelo: el residuo y las variables

Antes de hablar de cómo se divide el dataset, conviene entender **qué** aprende
el modelo y **con qué** lo aprende. El código de referencia es
`src/calcusol/ml/features.py`.

### El residuo (lo que el modelo predice, la Y)

El modelo **no** predice la radiación directamente. Predice el **residuo**: el
error que comete NASA POWER frente a la medición real del IDEAM.

```
residuo = Valor_IDEAM − ALLSKY_NASA
```

- `Valor_IDEAM`: radiación medida en tierra por el piranómetro del IDEAM (la
  "verdad de campo", en Wh/m²).
- `ALLSKY_NASA`: radiación que estima el satélite de NASA para esa misma
  hora y lugar (en Wh/m²).

La **predicción final** se reconstruye sumando el residuo predicho al valor de
NASA, y se recorta a ≥ 0:

```
radiación_corregida = ALLSKY_NASA + residuo_predicho   (nunca negativa)
```

**Por qué predecir el residuo y no la radiación directa.** El modelo parte del
dato de NASA, que ya es una estimación razonable, y solo tiene que aprender
*cuánto se equivoca NASA*. Ese error es más pequeño y más estructurado (depende
sobre todo de la altitud y la geometría solar) que la radiación completa. Por eso
el modelo es más estable: aunque falle, nunca se aleja arbitrariamente de NASA,
solo ajusta la corrección. Si predijera el valor absoluto, un error del modelo
podría producir una radiación disparatada.

### Las variables de entrada (las 17 features, la X)

Ninguna de estas variables usa `Valor` (la medición del IDEAM): si lo usaran,
el modelo "vería la respuesta" y habría **fuga de datos**. Todas se calculan a
partir de NASA, la ubicación y el tiempo.

**NASA (4) — lo que aporta el satélite:**

| Variable | Qué es |
|---|---|
| `ALLSKY_SFC_SW_DWN` | radiación en superficie con el cielo real (con nubes). Es el dato a corregir y el baseline. |
| `CLRSKY_SFC_SW_DWN` | radiación que habría con **cielo despejado** (sin nubes). El techo teórico de ese lugar y hora. |
| `ALLSKY_KT` | índice de claridad de NASA: cuánta radiación llega respecto a la que entra en la atmósfera (0 = muy nublado, 1 = muy despejado). |
| `T2M` | temperatura del aire a 2 m (°C). |

**Ubicación (3) — dónde está la estación:**

| Variable | Qué es |
|---|---|
| `Latitud` | latitud de la estación (grados). |
| `Longitud` | longitud de la estación (grados). |
| `Altitud` | altura sobre el nivel del mar (m). **La variable clave:** el sesgo de NASA crece con la altitud (ver §7). |

**Físicas con pvlib (5) — geometría solar, calculadas, no medidas:**

Se calculan con la librería `pvlib` a la **mitad de cada hora** (`Fecha` + 30
min), en hora local de Colombia.

| Variable | Qué es |
|---|---|
| `zenit` | ángulo del sol respecto a la vertical (0° = sol en el cenit; 90° = en el horizonte). Controla cuánta atmósfera atraviesa la luz. |
| `azimut` | dirección del sol en el horizonte (orientación este–oeste a lo largo del día). |
| `g0_horiz` | radiación **extraterrestre** sobre superficie horizontal: la que llegaría *sin atmósfera*. Referencia física máxima. |
| `razon_cielo` | `ALLSKY / CLRSKY`: qué fracción del máximo despejado se alcanza realmente (efecto de las nubes). |
| `kt_calc` | índice de claridad **recalculado** = `ALLSKY / g0_horiz`. Usa ALLSKY (de NASA), nunca `Valor`, por eso no hay fuga. |

**Temporales (5) — cuándo es:**

La hora y el día del año se codifican con seno y coseno para que el modelo
entienda que son **cíclicos** (la hora 23 está cerca de la 0; el día 365 cerca
del día 1), en vez de tratarlos como números sueltos.

| Variable | Qué es |
|---|---|
| `hora_sin`, `hora_cos` | hora del día (0–23) en forma cíclica. |
| `dia_sin`, `dia_cos` | día del año (1–365) en forma cíclica, capta la estacionalidad. |
| `Mes` | mes (1–12). |

### Baseline

El **baseline** (punto de comparación) es NASA sin corregir, es decir
`residuo = 0`. El modelo solo vale la pena si mejora ese baseline: ver §6 y §7.

---

## 3. Esquema ANTERIOR — split puramente temporal (el del plan)

### Cómo dividía

| Conjunto | Contenido | Años |
|---|---|---|
| Entrenamiento | todas las estaciones | 2005–2019 |
| Validación / test | **las mismas** estaciones | 2020–2022 |

El único criterio de división era **el tiempo**: años viejos para aprender, años
nuevos para probar. Todas las estaciones aparecían en ambos lados; solo cambiaba
el periodo.

### Qué mide bien

- La estabilidad del modelo **en el tiempo**: ¿sigue funcionando en años que no
  vio?

### Sus problemas

1. **No mide generalización a lugares nuevos.** Como las estaciones de prueba son
   las mismas que las de entrenamiento, el modelo ya conoció cada sitio: su
   altitud, su microclima, su patrón de nubosidad. Parte de su acierto en
   2020–2022 viene de haber **memorizado el lugar**, no de saber corregir NASA en
   general. La cifra resultante es **optimista** respecto al uso real.

2. **No responde la pregunta de negocio.** El producto sirve para estimar
   radiación donde **no hay piranómetro** (un hogar, un terreno sin estación
   IDEAM). El split temporal puro nunca prueba ese caso: todas sus estaciones de
   prueba ya fueron vistas.

3. **Test frágil por cobertura.** La cobertura de estaciones del IDEAM cae tras
   2016 (de 119 estaciones en 2016 a 44 en 2022). Un test restringido a 2020–2022
   queda con solo ~44–51 estaciones, una base pequeña y sesgada hacia las
   estaciones que siguieron reportando. (Ver `docs/contrato_datos.md`, nota de
   validación R2.)

---

## 4. Esquema ACTUAL — split 1b (temporal + espacial estratificado)

### Cómo divide

Divide por **dos ejes a la vez** (tiempo y estación) y produce **tres**
conjuntos, no dos.

Pasos previos (filtrado de calidad, antes de dividir):
- Solo registros **Definitivos** (el A/B mostró que incluir Preliminares
  degradaba ~5.5% el MAE mensual del modelo; no es una afirmación de que NASA
  tenga mayor error directo en esas filas).
- Rango físico válido: `0 ≤ radiación ≤ 1500 W/m²`.

Partición:

| Conjunto | Estaciones | Años | Mide |
|---|---|---|---|
| **train** | estaciones de entrenamiento | **≤ 2020** | — (aprende) |
| **test_temporal** | **las mismas** de train | **≥ 2021** | ¿generaliza a años futuros? |
| **test_spatial** | **20% apartadas por completo** | todos | ¿generaliza a lugares nuevos? |

### El holdout espacial es estratificado por altitud

El 20% de estaciones de `test_spatial` **no** se elige al azar puro. Se divide el
rango de altitudes en **4 cuartiles** y se aparta el 20% *dentro de cada
cuartil*. Así el test espacial contiene costa, valle, montaña media y alta
montaña, no solo un tipo de terreno. Es importante porque el sesgo de NASA
depende de la altitud (ver §7).

Todo con **semilla fija** (`SEED = 42`): el split es reproducible, cualquiera que
corra el código obtiene exactamente los mismos conjuntos.

### Tamaños resultantes

- **train:** 88 estaciones / ~2.25 M filas
- **test_temporal:** 29 estaciones / ~0.15 M filas
- **test_spatial:** 24 estaciones / ~0.83 M filas

---

## 5. Diferencias lado a lado

| Aspecto | Anterior (temporal puro) | Actual (split 1b) |
|---|---|---|
| Ejes de división | tiempo | tiempo **y** estación |
| Nº de conjuntos | 2 (train / test) | 3 (train / temporal / spatial) |
| ¿Prueba lugares nuevos? | **No** | **Sí** (test_spatial) |
| Estaciones de prueba | las mismas de train | 20% nunca vistas (espacial) |
| Selección del holdout | — | estratificada por altitud |
| Cifra que reporta | una, optimista | dos, la espacial es la defendible |
| Reproducible | sí | sí (seed fija) |

---

## 6. Cómo se evalúa (`evaluate.py`)

Sobre cada test se comparan dos cosas:

- **Baseline (B0):** NASA POWER cruda, sin corregir (equivale a residuo = 0).
- **Modelo:** NASA + residuo predicho, recortado a ≥ 0.

Y se mide en **tres niveles de agregación**:

| Nivel | Qué agrega | Por qué importa |
|---|---|---|
| hora | error hora a hora | granularidad fina |
| día | error del promedio diario | intermedio |
| **mes** | error del promedio mensual | **métrica de negocio** |

El nivel **mensual es el que decide**, porque la viabilidad de paneles (payback,
energía acumulada) depende de la energía mensual/anual, no de aciertos hora a
hora. Métricas: MAE, RMSE, R² y **sesgo %** (cuánto sobre/subestima en promedio).

### Qué cifra reportar y por qué

- **test_temporal** da números **inflados**: el modelo ya conoce esas estaciones.
  Mejora ~+67% mensual, pero **no** representa el uso real. Se declara como tal,
  no se esconde.
- **test_spatial** es **la cifra defendible**: estaciones nunca vistas = el caso
  de uso real (estimar radiación donde no hay estación). Es la que se presenta.

---

## 7. Resultado (los tres exámenes, nivel mensual — sobre Gold v1 completo)

| Examen | MAE base→modelo | Mejora | Sesgo base→modelo |
|---|---|---|---|
| Temporal (estaciones vistas)* | 56.5 → 18.4 | +67.3% | 8.3% → 1.0% |
| **Espacial (lugares nuevos) — cifra defendible** | 60.8 → 47.7 | **+21.5%** | 12.5% → 2.9% |
| Espacio-temporal (lugar + año nuevos) | 63.3 → 55.3 | +12.7% | 15.8% → 8.4% |

\* El temporal está **inflado** (el modelo ya conoce esas estaciones); no
representa el uso real. El espacio-temporal es el más exigente: mejora menos y el
sesgo baja menos, lo cual es esperable. La cifra que se reporta es la **espacial**.

**Hallazgo central:** el sesgo de NASA **crece con la altitud**. En Gold v1,
usando las bandas definidas para el análisis, pasa aproximadamente de 3.9% en
altitudes bajas a 13.6% entre 2,000 y 2,500 m. Por eso el modelo usa lat/lon y
altitud como variables, y por eso el holdout se estratifica por altitud: para que
el test espacial mida la corrección en todo el rango de terrenos.

---

## 8. Diagnóstico adicional — curvas de aprendizaje (`diagnostico.py`)

Aparte de los dos tests, se entrena con fracciones crecientes de estaciones
(10 / 25 / 50 / 100 %) y se valida con **GroupKFold agrupando por estación**
(para no mezclar la misma estación entre fold de entrenamiento y de validación).

Resultado: la curva de validación **se aplana** desde ~9 estaciones. Eso indica
un **techo de información** (las variables ya capturan lo capturable), no falta de
datos: añadir más estaciones no bajaría el error de forma apreciable.

---

## 9. Validación de R2 — tres exámenes, sin fuga por Sitio

La evaluación de R2 va más allá del split de R1 y aplica los **tres exámenes**
del plan, todos agrupando por **Sitio** (no por estación), lo que cierra la fuga
de las estaciones gemelas que comparten celda NASA.

### Los tres exámenes (R2-1) — `evaluate.py` y `splits.py`

| Examen | Qué mide | Cómo se arma |
|---|---|---|
| **Temporal** | años futuros de estaciones vistas | mismas estaciones, años ≥ 2021 |
| **Espacial** | sitios nunca vistos | **GroupKFold de 5 pliegues por `Sitio`** |
| **Espacio-temporal** | sitios nuevos EN años futuros (el más exigente) | sitios en holdout, años ≥ 2021 |

El examen espacial pasó de un **holdout único del 20% por estación** (lo que
describían las §4–§7, usado para entrenar el modelo de R1) a una **validación
cruzada de 5 pliegues agrupada por Sitio**. Agrupar por Sitio (estaciones a < 1
km) evita que estaciones co-localizadas que comparten celda NASA queden separadas
entre train y test.

Al verificarlo sobre Gold v1 (generado por el pipeline de integración), encontramos que la fuga
**ya está cerrada en la capa de datos**: la regla 12b de `gold/integracion.py`
elimina las copias exactas de estaciones gemelas (misma hora, mismo valor dentro
del mismo Sitio → se conserva una sola). El Gold final tiene **0 coordenadas
compartidas y 0 sitios con más de una estación**: `CodigoEstacion` y `Sitio` son
1-a-1. Por eso el split por Sitio y el split por estación coinciden: ya no quedan
gemelas que separar.

### Prueba de no-fuga (R2-1)

`splits.verificar_sin_fuga()` comprueba que **ningún Sitio** aparece a la vez en
train y test del mismo pliegue (resultado: positivo), y reporta cuántos sitios
agrupan varias estaciones (en el Gold actual: 0, porque la regla 12b ya fusionó
las gemelas). Es la evidencia de que el examen espacial no tiene fuga geográfica.

### Pruebas de robustez (R2-2) — `robustez.py`

1. **Celda NASA (D10):** confirma con los datos que las estaciones de un mismo
   Sitio tenían series NASA idénticas. Es la evidencia que justifica la regla 12b
   del Gold (eliminar las copias exactas de gemelas).
2. **A/B de preliminares:** entrena con (definitivos + preliminares) vs (solo
   definitivos), midiendo siempre sobre prueba definitiva. La decisión se apoya
   en que *no empeora*, no en que "mejora".
3. **Curvas de aprendizaje:** en `diagnostico.py` (ver §8).

### Mapa de errores y tabla por sitio (R2-3) — `validacion.py`

Desglose del error (MAE base vs modelo, mejora %, sesgo) por **estación,
departamento, hora, banda de altitud y nivel de nubosidad** (`ALLSKY_KT`), a
nivel horario y mensual. Más la **tabla de mejora por sitio** que R3 usa para
elegir sus casos. Todas las predicciones son *out-of-fold* (cada fila predicha
por un modelo que no vio su sitio).

### Examen final y "dónde no supera a NASA" (R2-4) — `validacion.py`

La **tabla por sitio** y la tabla **"dónde NO supera a NASA"** (sitios con
mejora ≤ 0). Confirma que el modelo es un corrector **condicional**: mejora donde
NASA tiene sesgo alto (altitud), y puede añadir ruido donde NASA ya acierta.

---

## 10. Cómo reproducir la evaluación de R2

```bash
# R2-1: tamaños de los tres exámenes + prueba de no-fuga por Sitio
docker compose run --rm calcusol uv run python -m calcusol.ml.splits

# R2-1: los tres exámenes con el modelo entrenado (temporal/espacial/espacio-temporal)
docker compose run --rm calcusol uv run python -m calcusol.ml.evaluate

# R2-2: pruebas de robustez (celda NASA, A/B preliminares)
docker compose run --rm calcusol uv run python -m calcusol.ml.robustez
docker compose run --rm calcusol uv run python -m calcusol.ml.diagnostico   # curvas de aprendizaje

# R2-3 y R2-4: mapa de errores, tabla por sitio, dónde no supera NASA
docker compose run --rm calcusol uv run python -m calcusol.ml.validacion
```

Los CSV quedan en `reports/ml_validacion/` y `reports/ml_robustez/`.
