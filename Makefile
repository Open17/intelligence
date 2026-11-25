# Developer shortcuts. The setup scripts don't need any of this.
PYTHON ?= python3
VENV   ?= .venv
PY      = $(VENV)/bin/python

.PHONY: help venv test test-server test-client test-deploy lint docker test-docker test-stack clean

help:
	@echo "make test         run all tests (server, client, setup libraries)"
	@echo "make lint         shellcheck the setup scripts (+ ruff if installed)"
	@echo "make docker       build the voice and agent images"
	@echo "make test-docker  run the voice tests inside the image (real echo + Piper)"
	@echo "make test-stack   run the whole Compose stack and talk to it like a Pi (no billing)"

$(VENV)/.ready: server/voice/pyproject.toml server/agent/pyproject.toml client/pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e "server/voice[dev]" -e "server/agent[dev]" -e "client[dev]"
	touch $@

venv: $(VENV)/.ready

test: test-server test-client test-deploy

test-server: venv
	cd server/voice && ../../$(PY) -m pytest -q
	cd server/agent && ../../$(PY) -m pytest -q

test-client: venv
	cd client && ../$(PY) -m pytest -q

test-deploy:
	bash deploy/tests/test_deploy.sh

lint:
	shellcheck -x -S warning server_setup.sh local_setup.sh deploy/*.sh deploy/lib/*.sh deploy/tests/*.sh
	-command -v ruff >/dev/null && ruff check server client

docker:
	docker build -f server/voice/Dockerfile -t intelligence-voice:dev .
	docker build -f server/agent/Dockerfile -t intelligence-agent:dev .

test-docker:
	docker run --rm --read-only --tmpfs /tmp --cap-drop ALL intelligence-voice:dev python3 -m pytest -q -p no:cacheprovider /opt/voice/tests

test-stack: venv
	bash deploy/tests/test_stack.sh $(abspath $(PY))

clean:
	rm -rf $(VENV) build
