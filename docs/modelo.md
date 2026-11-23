# Modelo de corrección de radiación — Calcusol (R1)

Documento del modelo de ML: qué hace, cómo se decidió, cómo reproducirlo y sus
límites. La fuente de verdad del código es `src/calcusol/ml/`.

## 1. Qué hace

Corrige el sesgo de la radiación horaria de NASA POWER usando las mediciones
terrestres del IDEAM como verdad de campo. Es **regresión supervisada**.

- **Entradas (X, 17 features):** 4 de NASA (`ALLSKY_SFC_SW_DWN`,
  `CLRSKY_SFC_SW_DWN`, `ALLSKY_KT`, `T2M`), ubicación (lat, lon, altitud),
  física solar con pvlib (zenit, azimut, extraterrestre horizontal, razón
  all-sky/clear-sky, índice de claridad calculado) y tiempo (hora, día del año
  cíclicos, mes).
- **Objetivo (Y):** `residuo = Valor_IDEAM - ALLSKY`. La predicción final es
  `ALLSKY + residuo`, recortada a >= 0 W/m². El modelo parte del satélite y solo
  aprende la corrección, por lo que nunca se aleja arbitrariamente de NASA.
- **Baseline:** NASA POWER sin corregir (residuo = 0).
- **Métrica principal:** MAE (W/m²). La de negocio es el **MAE mensual**, porque
  el Payback depende de la energía acumulada.

## 2. Decisiones (tomadas con evidencia)

| # | Decisión | Resultado | Evidencia |
|---|---|---|---|
| 1 | Target | **residuo** + clip >=0 | más estable que valor directo |
| 2 | Preliminares | **excluir** | A/B (evaluar_modelo.py): agregarlos sube 6.4% el MAE mensual en estaciones nuevas (1.3% por hora) y casi no cambia en estaciones conocidas (-0.4% mensual); esto no significa que NASA tenga mayor MAE en esas filas |
| 3 | Features | 17 (NASA + ubicación + física + tiempo) | sin fuga (ninguna usa `Valor`) |
| 4 | Ablación ubicación | **A0: todas** | quitar la ubicación sube 2.6% el MAE mensual en estaciones nuevas (47.73 -> 48.99; la mejora baja de 21.5% a 19.4%); por hora casi no cambia (4.6% -> 4.4%) → geografía real, no memorización |

Otras: split 1b (estratificado por altitud, test temporal >=2021, holdout
espacial 20%); alineación temporal verificada (pico de radiación a las ~12-13 h
local, lag-test con mínimo en 0); familia LightGBM (empate técnico con XGBoost y
Random Forest, elegido por velocidad); hiperparámetros por Optuna contra
validación espacial.

## 3. Resultados (evaluación única en test)

Cifra defendible = **test_spatial** (estaciones nunca vistas), nivel mensual.
Evaluación sobre Gold v1 completo, usando `dataset_modelo.parquet`.

| Escenario | Nivel | MAE NASA | MAE modelo | Mejora | Sesgo NASA→modelo |
|---|---|---|---|---|---|
| **Espacial** (sitios nuevos) | mes | 60.8 | 47.7 | **+21.5%** | 12.5% → 2.9% |
| Espacial | hora | 120.8 | 115.3 | +4.6% | 12.3% → 2.8% |
| Temporal (sitios vistos)* | mes | 56.5 | 18.4 | +67.3% | 8.3% → 1.0% |
| Espacio-temporal (sitio + año nuevos) | mes | 63.3 | 55.3 | +12.7% | 15.8% → 8.4% |

\* El escenario temporal está **inflado** porque esas estaciones estuvieron en
el entrenamiento; no representa el uso en un hogar sin piranómetro. Se declara. El
espacio-temporal es el más exigente (lugar y año nuevos) y mejora menos.

**Hallazgo central:** el sesgo de NASA crece con la altitud. En Gold v1, usando
las bandas definidas para el análisis, pasa aproximadamente de 3.9% en las
altitudes bajas a 13.6% entre 2,000 y 2,500 m. El modelo corrige más donde más
falta hace.

## 4. Limitaciones (reportadas con transparencia)

- La mejora horaria es modesta (~5% en sitios nuevos): el ruido de nubosidad a
  la resolución de NASA (~50 km) es irreducible con estas variables.
- **El modelo empeora en estaciones donde NASA ya tiene bajo sesgo**
  (típicamente < 40 W/m² de MAE base). En el holdout espacial mejora en 18 de 24
  estaciones; en las 6 restantes NASA ya era precisa y la corrección añade ruido.
  La mejora media espacial tiene IC95% amplio (cruza el cero): el modelo es un
  corrector **condicional**, no universal. (Mejora pendiente: capa de blend o
  umbral para no corregir donde NASA ya acierta — ver v2.)
- Alcance: Colombia. No validar fuera del dominio geográfico entrenado.

## 5. Validación en estaciones ajenas al pipeline

Las 4 estaciones excluidas por el corte 2005 (nunca entraron a Bronze/Silver/
Gold de ML) se usaron como prueba externa. Mejora mensual: Serranilla +11.8%,
Puerto Inírida +8.6%, Florencia +40.8%, Gorgona +5.3%. Las 4 superan a NASA.

## 6. Cómo reproducir

Todo comparte el mismo código: las 17 variables (`ml/features.py`), la partición (`ml/splits.py`), las rutas
(`rutas.py`) y el modelo (`ml/modelo.py`). Los datos van en `data/gold/dataset_modelo.parquet`
(`python -m calcusol.gold.consolidar` lo crea desde el Gold particionado).

```
# 1. entrenar (genera models/radiacion/v1/modelo_lgbm.txt y metadatos.json)
docker compose run --rm calcusol uv run python -m calcusol.ml.train
# 2. evaluar en los tres exámenes (temporal, espacial y espacio-temporal)
docker compose run --rm calcusol uv run python -m calcusol.ml.evaluate
# 3. evaluación completa de R2 con validación cruzada: genera el Excel que lee R3 (varias horas)
python src/calcusol/validacion/evaluar_modelo.py
# 4. comparar familias de modelos y buscar hiperparámetros (pesado; ver notebooks/comparacion_modelos.ipynb)
python -m calcusol.ml.comparar_modelos --muestra 0.25
python -m calcusol.ml.buscar_hiperparametros --trials 25 --muestra 0.25
```

Los pasos 4 necesitan `pip install ".[comparacion]"` (xgboost y optuna) y escriben en `reports/ml_comparacion/`.
Para correrlos con más RAM o GPU (Colab) está `notebooks/comparacion_modelos.ipynb`.

El modelo no necesita convertirse a `.joblib`: `ml/modelo.py` abre el `.txt` como regresor de scikit-learn
y recupera los hiperparámetros de `metadatos.json`.

Artefacto: `models/radiacion/v1/modelo_lgbm.txt` (formato portable) +
`metadatos.json` (features, HP, decisiones, versiones). Para predecir desde otro
módulo (API, física): `from calcusol.ml.predict import cargar`.
