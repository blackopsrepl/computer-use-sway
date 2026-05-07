PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: check test py-compile doctor build clean

check: test py-compile

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

py-compile:
	$(PYTHON) -m compileall -q src tests

doctor:
	PYTHONPATH=src $(PYTHON) -m computer_use_sway --doctor

build:
	$(PYTHON) -m build

clean:
	rm -rf build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
