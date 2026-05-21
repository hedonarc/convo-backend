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

dev:
	$(MAKE) redis
	$(MAKE) mail
	$(PYTHON) manage.py runserver

migrate:
	$(PYTHON) manage.py migrate

makemigrations:
	$(PYTHON) manage.py makemigrations

migs:
	$(PYTHON) manage.py makemigrations
	$(PYTHON) manage.py migrate

showmigrations:
	$(PYTHON) manage.py showmigrations

shell:
	$(PYTHON) manage.py shell

setup_admin:
	$(PYTHON) manage.py setup_admin

admin-password:
	$(PYTHON) manage.py createsuperuser

check-settings:
	$(PYTHON) manage.py check --settings=settings.local

test:
	$(PYTHON) manage.py test --settings=settings.test

# -------------------------
# Redis
# -------------------------

redis:
	docker start redis-dev || \
	docker run -d --rm \
		--name redis-dev \
		-p 6379:6379 \
		redis:7

redis-stop:
	docker stop redis-dev

redis-cli:
	docker exec -it redis-dev redis-cli

# -------------------------
# Email (MailHog)
# -------------------------

mail:
	docker start mailhog-dev || \
	docker run -d \
		--platform linux/amd64 \
		--name mailhog-dev \
		-p 1025:1025 \
		-p 8025:8025 \
		mailhog/mailhog

mail-stop:
	docker stop mailhog-dev

open-mail:
	open http://localhost:8025

# -------------------------
# Docker Helpers
# -------------------------

docker-stop:
	-docker stop redis-dev
	-docker stop mailhog-dev

docker-clean:
	-docker rm -f redis-dev
	-docker rm -f mailhog-dev

docker-logs:
	docker logs -f redis-dev &
	docker logs -f mailhog-dev

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
