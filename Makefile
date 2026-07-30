# Pharma Commercial AI Sandbox — comandos de desarrollo
#
# Entorno nativo (esta máquina): PostgreSQL 17 + pgvector y Redis vía Homebrew.
# Entorno portable: `docker compose up -d db redis`.

SHELL := /bin/bash
PY    := backend/.venv/bin/python
PIP   := backend/.venv/bin/pip
PGBIN := /usr/local/opt/postgresql@17/bin
API_PORT ?= 8010

.DEFAULT_GOAL := help
.PHONY: help setup setup-native install api web dev migrate seed reset-db test test-isolation eval lint fmt logs stop status docs bootstrap-db

help: ## Muestra esta ayuda
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Instalación ──────────────────────────────────────────────────────────────

setup-native: ## Instala y arranca PostgreSQL 17 + pgvector + Redis con Homebrew
	brew install postgresql@17 pgvector redis
	brew services start postgresql@17
	brew services start redis
	@echo "Servicios arrancados. Ejecuta 'make setup' a continuación."

setup: install ## Prepara el proyecto completo desde cero
	@test -f .env || (cp .env.example .env && chmod 600 .env && echo "Creado .env desde la plantilla")
	$(MAKE) migrate seed
	@echo "Listo. 'make dev' arranca API y frontend."

install: ## Crea el entorno virtual e instala dependencias
	test -d backend/.venv || /usr/local/opt/python@3.12/bin/python3.12 -m venv backend/.venv
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r backend/requirements.txt
	cd frontend && npm install --silent

# ── Ejecución ────────────────────────────────────────────────────────────────

api: ## Arranca la API con recarga automática
	cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $(API_PORT) --reload

web: ## Arranca el frontend
	cd frontend && npm run dev

dev: ## Arranca API y frontend a la vez
	@$(MAKE) -j2 api web

stop: ## Detiene los procesos de la aplicación (no toca Postgres ni Redis)
	-pkill -f "uvicorn app.main:app" || true
	-pkill -f "next-server" || true

status: ## Estado de las dependencias de infraestructura
	@$(PGBIN)/pg_isready || true
	@redis-cli ping || true
	@curl -sf --max-time 3 http://127.0.0.1:$(API_PORT)/readyz || echo "API no responde"

logs: ## Últimas líneas del log de la API
	@tail -n 60 backend/logs/api.log 2>/dev/null || echo "sin log en fichero (la API escribe a stdout)"

# ── Datos ────────────────────────────────────────────────────────────────────

migrate: ## Aplica el esquema y las políticas RLS
	$(PY) -m app.db.migrate

seed: ## Carga los datos sintéticos de demostración
	$(PY) -m app.db.seed

reset-db: ## Borra y reconstruye la base de datos completa
	$(PY) -m app.db.migrate --drop
	$(MAKE) seed

docs: ## Regenera la documentación que se deriva del código
	$(PY) scripts/gen_permissions_matrix.py

bootstrap-db: ## Crea roles y extensiones (requiere superusuario de PostgreSQL)
	$(PGBIN)/psql -d pharma_sandbox -v ON_ERROR_STOP=1 -f scripts/bootstrap-db.sql

# ── Calidad ──────────────────────────────────────────────────────────────────

test: ## Ejecuta la suite completa de pruebas
	cd backend && .venv/bin/pytest -q

test-isolation: ## Solo las pruebas de aislamiento entre tenants
	cd backend && .venv/bin/pytest -q tests/test_tenant_isolation.py -v

eval: ## Ejecuta la suite de evaluaciones del agente
	$(PY) -m app.evals.run --dataset pharma-safety-v1

lint: ## Comprueba estilo y errores estáticos
	backend/.venv/bin/ruff check backend/app backend/tests
	cd frontend && npm run lint

fmt: ## Formatea el código
	backend/.venv/bin/ruff format backend/app backend/tests
	backend/.venv/bin/ruff check --fix backend/app backend/tests
