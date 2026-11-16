.PHONY: test test-unit test-integration test-system lint generate docker-build docker-smoke

PY ?= .venv/bin/python

test:              ## unit + integration (fast, no cloud)
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest -m unit

test-integration:
	$(PY) -m pytest -m integration

test-system:       ## end-to-end against Moto via an isolated Steampipe (~/.spendlens-e2e)
	SPENDLENS_SYSTEM=1 $(PY) -m pytest -m system tests/system -v

lint:
	$(PY) -m ruff check src tests scripts

generate:          ## regenerate mod variables, tag controls, control docs and the demo DB
	$(PY) scripts/gen_mod_variables.py
	$(PY) scripts/gen_tag_controls.py
	$(PY) scripts/gen_control_docs.py
	$(PY) scripts/build_demo_db.py

IMAGE ?= spendlens:dev

docker-build:      ## build the container image (Steampipe, Powerpipe and the AWS plugin baked in)
	docker build -t $(IMAGE) .

docker-smoke:      ## offline smoke test of the image: demo data only, no AWS credentials, no network
	docker run --rm --network none $(IMAGE) --help > /dev/null
	docker run --rm --network none $(IMAGE) tags --source demo --summary
	docker run --rm --network none $(IMAGE) waste --source demo --summary --by service
	docker run --rm --network none -e SPENDLENS_STEAMPIPE_SERVICE=on $(IMAGE) doctor --offline
