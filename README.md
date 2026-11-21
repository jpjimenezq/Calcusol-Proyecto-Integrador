# Calcusol · Proyecto integrador 1

## Idea principal

Calcusol responde una pregunta: ¿conviene instalar paneles solares en una casa de Colombia y en cuántos años se recupera la inversión?

Para responderla necesitamos saber cuánto sol recibe esa casa. Hay dos fuentes:

- **NASA:** estima la radiación desde satélites para todo el país, pero tiende a sobreestimarla.
- **IDEAM:** mide la radiación en tierra, pero solo en unas pocas estaciones.

Un modelo de aprendizaje automático aprende a corregir los datos de NASA usando las mediciones del IDEAM. Con esa radiación corregida:

1. Un motor solar (`pvlib`) calcula cuánta energía produce un panel.
2. Una calculadora financiera la convierte en ahorro en la factura y en años de Payback.

## Flujo de datos

Los datos pasan por tres capas en la nube (AWS):

- **Bronze:** datos crudos, tal como llegan de NASA e IDEAM.
- **Silver:** datos limpios y unidos por estación y hora.
- **Gold:** la tabla final con la que se entrena el modelo.

## Equipo

- R1 · Modelos: Juan Diego
- R2 · Validación y evaluación: Manuela
- R3 · Física y finanzas: Luciana
- R4 · Ingesta y nube: Juan Pablo
- R5 · Limpieza e integración: Felipe M
