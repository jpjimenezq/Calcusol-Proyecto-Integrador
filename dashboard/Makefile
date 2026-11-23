# Entorno del dashboard Calcusol · Antioquia
PY := .venv/bin/python
PUERTO ?= 8000

.PHONY: help setup data check serve all clean

help:
	@echo "make setup   Crea el entorno virtual e instala dependencias"
	@echo "make data    Regenera dashboard/data/antioquia.json desde el parquet"
	@echo "make check   Verifica el modelo energético y financiero"
	@echo "make serve   Sirve el dashboard en http://localhost:$(PUERTO)"
	@echo "make all     setup + data + check"

setup:
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt

data:
	$(PY) pipeline/build_data.py

check:
	node tests/check_model.mjs

serve:
	$(PY) -m http.server $(PUERTO) -d dashboard

all: setup data check

clean:
	rm -rf .venv
