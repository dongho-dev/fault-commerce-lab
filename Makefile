COMPOSE := docker compose
STOCKS ?= 10
BASE_URL ?= http://localhost:8000

.PHONY: up down reset migrate test lint typecheck smoke concurrency verify timeline snapshot

up:
	$(COMPOSE) up -d --build --wait --wait-timeout 180

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) exec -T app python oracle/reset.py --stocks $(STOCKS)

migrate:
	$(COMPOSE) exec -T app alembic upgrade head

test:
	$(COMPOSE) --profile test run --build --rm test

lint:
	$(COMPOSE) build app
	$(COMPOSE) run --rm --no-deps app ruff check .

typecheck:
	$(COMPOSE) build app
	$(COMPOSE) run --rm --no-deps app mypy app oracle scripts tools

smoke:
	$(COMPOSE) exec -T app python scripts/smoke.py --base-url http://localhost:8000

concurrency:
	$(COMPOSE) exec -T app python scripts/concurrency_probe.py --base-url http://localhost:8000 --stock 10 --requests 40 --quantity 1

verify:
	$(COMPOSE) build app
	$(COMPOSE) run --rm --no-deps app ruff check .
	$(COMPOSE) run --rm --no-deps app mypy app oracle scripts tools
	$(COMPOSE) --profile test run --build --rm test
	$(COMPOSE) up -d --build --wait --wait-timeout 180
	$(COMPOSE) exec -T app python scripts/smoke.py --base-url http://localhost:8000
	$(COMPOSE) exec -T app python oracle/verify_baseline.py --base-url http://localhost:8000

timeline:
	$(COMPOSE) exec -T app python tools/render_timeline.py --input logs/app.jsonl --output artifacts/timeline.html

snapshot:
	$(COMPOSE) exec -T app python tools/db_snapshot.py
