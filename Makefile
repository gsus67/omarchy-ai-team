.PHONY: check test test-verbose validate lint smoke

check: validate test lint smoke

validate:
	omarchy plugin validate .

test:
	python3 -m unittest discover -s tests -q

test-verbose:
	python3 -m unittest discover -s tests -v

lint:
	python3 tools/lint-qml.py

smoke:
	python3 tools/preview.py --smoke --live
