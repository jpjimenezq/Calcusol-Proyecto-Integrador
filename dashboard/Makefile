# Entorno del dashboard Calcusol · Antioquia
PY := .venv/bin/python
PUERTO ?= 8000

.PHONY: help setup data demo-data check serve all clean

help:
	@echo "make setup   Crea el entorno virtual e instala dependencias"
	@echo "make data    Regenera dashboard/data/colombia.json desde el parquet"
	@echo "make demo-data  Genera datos de DEMOSTRACIÓN (NASA POWER + modelo de la API; necesita internet)"
	@echo "make check   Verifica el modelo energético y financiero"
	@echo "make serve   Sirve el dashboard en http://localhost:$(PUERTO)"
	@echo "make all     setup + data + check"

setup:
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt

data:
	$(PY) pipeline/build_data.py

# Sin dataset_modelo.parquet: necesita pvlib y lightgbm, que no están en el entorno de make setup
demo-data:
	uv run --no-project --with pvlib --with lightgbm --with pandas --with numpy --with pyyaml --with joblib python pipeline/build_demo_data.py

check:
	node tests/check_model.mjs

serve:
	$(PY) -m http.server $(PUERTO) -d dashboard

all: setup data check

clean:
	rm -rf .venv
