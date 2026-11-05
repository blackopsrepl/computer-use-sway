PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: check test py-compile lint-files integration bench leftovers release-gate doctor build clean

check: test py-compile lint-files

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

py-compile:
	$(PYTHON) -m compileall -q src tests

lint-files:
	$(PYTHON) scripts/check_file_length.py

integration:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests/integration -v

bench:
	PYTHONPATH=src $(PYTHON) -m bench.run

leftovers:
	$(PYTHON) scripts/check_sandbox_leftovers.py

# The release verdict is this target's exit status, and nothing else.
release-gate: check integration bench leftovers

doctor:
	PYTHONPATH=src $(PYTHON) -m computer_use_sway --doctor

build:
	$(PYTHON) -m build

clean:
	rm -rf build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
