# -------------------------
# Project Config
# -------------------------

PYTHON := uv run
PRECOMMIT := uv run pre-commit

# -------------------------
# Install / Setup
# -------------------------

sync:
	uv sync

sync-dev:
	uv sync --all-extras

pc-install:
	$(PRECOMMIT) install --hook-type pre-commit
	$(PRECOMMIT) install --hook-type commit-msg

be-setup: sync migs pc-install

setup: be-setup

# -------------------------
# Django Commands
# -------------------------

server:
	$(PYTHON) manage.py runserver

redis:
	docker run -d --rm --name redis-dev -p 6379:6379 redis:7

dev:
	docker run -d --rm --name redis-dev -p 6379:6379 redis:7 & \
	$(PYTHON) manage.py runserver

stop-redis:
	docker stop redis-dev

migrate:
	$(PYTHON) manage.py migrate

makemigrations:
	$(PYTHON) manage.py makemigrations

migs:
	$(PYTHON) manage.py makemigrations && $(PYTHON) manage.py migrate

showmigrations:
	$(PYTHON) manage.py showmigrations

shell:
	$(PYTHON) manage.py shell

setup_admin:
	$(PYTHON) manage.py setup_admin

admin-password:
	$(PYTHON) manage.py createsuperuser

# -------------------------
# Ruff (Linting / Formatting)
# -------------------------

check:
	uv run ruff check .

checki:
	uv run ruff check --select I .

fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

lint: check checki fix format

# -------------------------
# Pre-commit
# -------------------------

pc-ruff:
	$(PRECOMMIT) run ruff --all-files

# -------------------------
# Testing & Checks
# -------------------------

check-settings:
	$(PYTHON) manage.py check --settings=settings.local

test:
	$(PYTHON) manage.py test --settings=settings.test
