# Plan del dashboard Calcusol · Antioquia

## Objetivo

Que una persona elija su ubicación y describa su hogar, y el dashboard le diga si el sistema solar es viable y en cuántos años se paga. Las preguntas de negocio no aparecen escritas: cada una se responde con un indicador y un gráfico.

## De las preguntas a los gráficos

| Pregunta de negocio | Dónde se responde | Forma elegida y por qué |
|---|---|---|
| Principal: ¿es viable y en cuántos años se recupera? | Bloque «Respuesta»: titular, sello Viable / No viable, años de recuperación, VPN y TIR | Cifra destacada: es un solo valor que se lee de un vistazo |
| Ubicación: ¿cuánta radiación hay en mi zona? | Mapa de calor de Antioquia con límites y nombres de municipios; al hacer clic se marca la ubicación del hogar, se selecciona la estación más cercana y una línea muestra la distancia aproximada. Admite zoom (rueda, botones + − y arrastre); al acercar se rotulan todos los municipios | Mapa con escala de azul (menos radiación) a rojo (más radiación) |
| 1. ¿Cuánta energía genera con radiación corregida frente a la satelital? | «Energía por mes» | Columnas apiladas (desviación): la base es lo corregido y el tramo coral es lo que NASA añade de más |
| 2. ¿Qué parte del consumo cubre y cuánto excedente inyecta? | «Generación frente a consumo, día promedio» y tarjetas de cobertura y excedente | Columnas apiladas autoconsumo + excedente con el consumo como línea escalonada (parte de un todo, hora a hora) |
| 3. ¿Cuánto ahorra en la factura según estrato y tarifa? | Indicador «Ahorro en la factura» (antes → después) y detalle mensual al pasar el cursor | Cifra destacada; el estrato y la tarifa son filtros |
| 4. ¿Payback y VPN por escenario de dimensionamiento? | Tres tarjetas de escenario, dumbbell y flujo de caja acumulado, más tabla comparativa | Tarjetas (magnitud), dumbbell (cambio sin corregir → corregido) y líneas (el cruce con cero es el Payback) |

## Filtros

Estación más cercana, consumo mensual, estrato, tarifa del operador, perfil de consumo, mes de las curvas horarias y tamaño del sistema. En «Supuestos técnicos y financieros» se editan los tres tamaños y los parámetros económicos.

## Línea gráfica (tomada del PDF)

| Elemento | Valor |
|---|---|
| Tipografía | Inter |
| Fondo / tarjetas | `#FBFDFF` / `#EAF2FB` |
| Azul marino (títulos, destacado) | `#0B3D7A` |
| Azul (dato principal) | `#2F80D0` |
| Coral / coral claro (exceso de NASA, sin corregir) | `#E87B7B` / `#F3BDBD` |
| Verde (corregido, viable, excedente) | `#1E9E5A` |
| Texto secundario | `#4A6283` |

Cada bloque toma la estructura de una diapositiva, reducida a lo visual: antetítulo en mayúsculas, titular que enuncia la conclusión, gráficos y tarjetas con la cifra clave (la destacada en azul marino). Las notas metodológicas quedan plegadas al final.

Coral y verde se distinguen mal con daltonismo rojo-verde. Por eso nunca van solos: en el dumbbell cada punto lleva su valor y su posición, y el sello de viabilidad lleva icono y texto.

## Modelo de cálculo

**Energía.** Para cada mes y hora del día promedio:

- Temperatura de celda = temperatura ambiente + radiación × (NOCT − 20) ÷ 800
- Generación = kWp × radiación ÷ 1000 × [1 + γ × (T celda − 25)] × (1 − pérdidas)

**Autoconsumo.** En cada hora, autoconsumo = mínimo entre generación y consumo. El excedente es lo que sobra y lo importado es el consumo que no se cubrió.

**Factura.** Subsidio de 60 %, 50 % y 15 % (estratos 1, 2 y 3) hasta el consumo de subsistencia (130 kWh/mes a 1000 m o más, 173 por debajo). Contribución del 20 % en estratos 5 y 6.

**Excedentes (CREG 174 de 2021).** Los excedentes se permutan uno a uno contra la energía importada y sobre ellos se paga la comercialización. Lo que supera lo importado se liquida a precio de bolsa.

**Finanzas.** Inversión = costo fijo + costo por kWp × tamaño. El ahorro anual crece con la tarifa y baja con la degradación; se descuenta el mantenimiento. Payback = año en que el flujo acumulado nominal cruza cero. VPN = flujos descontados menos la inversión.

Valores de referencia (editables): tarifa 900 $/kWh, comercialización 120 $/kWh, bolsa 300 $/kWh, costo fijo 3,0 M$, 4,2 M$ por kWp, mantenimiento 1 %, degradación 0,5 %, alza de tarifa 4 %, descuento 10 %, 25 años, pérdidas 14 %.

## Limitaciones conocidas

- Los valores financieros de referencia no salen del parquet. Hay que validarlos con tarifas publicadas y cotizaciones reales.
- El error del modelo (RMSE) se calculó sobre los registros históricos disponibles, que pueden incluir los de entrenamiento.
- El balance usa el día promedio de cada mes, lo que sobrestima algo el autoconsumo frente a una simulación día a día.
- Se usa radiación horizontal, sin trasponer al plano inclinado del panel. A la latitud de Antioquia la diferencia es pequeña.
- El mapa interpola la radiación entre solo 11 estaciones (inverso de la distancia al cuadrado); lejos de ellas es orientativo. Los cálculos usan siempre los datos de la estación de la zona, no el valor interpolado.
- La ubicación marcada en el mapa solo sirve para elegir la estación más cercana en línea recta (11 en Antioquia); los cálculos usan los datos de esa estación, que puede estar a decenas de kilómetros y a otra altitud. Cuatro estaciones tienen menos de seis años de datos.
- No se modelan beneficios tributarios (Ley 1715) ni el reemplazo del inversor.

## Siguientes pasos

1. Validar tarifa, costos y precio de bolsa con fuentes vigentes y, si aplica, ofrecer una lista de operadores con su tarifa.
2. Aplicar el modelo a coordenadas arbitrarias (descargando NASA POWER para el punto) y sustituir el selector de estación por un mapa.
3. Simular día a día en el pipeline (con pvlib) para afinar el autoconsumo.
4. Evaluar el modelo con un conjunto de prueba separado y publicar ese error.
5. Extender a otros departamentos: el pipeline solo requiere cambiar `DEPARTAMENTO`.
