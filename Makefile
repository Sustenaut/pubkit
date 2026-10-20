SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.PHONY: help venv lock standard standard-check functional check wheel protect protect-check clean

help: ## Show available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv: ## Hash-locked environment, with this checkout installed editable
	python3 -m venv .venv
	.venv/bin/pip install --quiet --require-hashes -r tests/requirements.lock
	.venv/bin/pip install --quiet --no-deps -e .

# Made on first use, so `make protect` needs nothing run before it.
.venv/bin/pubkit:
	$(MAKE) venv

lock: ## Pin tests/requirements.txt, with hashes
	.venv/bin/pubkit lock

standard: ## Regenerate src/pubkit/standard.json from the standard's [checked] tags
	.venv/bin/python -m pubkit.standard

standard-check: ## Fail if src/pubkit/standard.json differs from the standard's tags
	.venv/bin/python -m pubkit.standard --check

functional: ## The Gherkin suite, with its coverage in coverage/coverage.xml
	rm -rf coverage && mkdir coverage
	cd tests && COVERAGE_RCFILE=$(CURDIR)/pyproject.toml COVERAGE_FILE=$(CURDIR)/coverage/.coverage \
	  coverage run -m pytest -q
	COVERAGE_FILE=coverage/.coverage coverage combine -q
	COVERAGE_FILE=coverage/.coverage coverage xml -q

check: standard-check functional ## Everything CI runs

wheel: ## Build the wheel a release carries
	.venv/bin/pip wheel --quiet --no-deps . -w dist

protect: .venv/bin/pubkit ## Put the ruleset on every repository in src/pubkit/protected.json (or REPOS="o/r ..."), through gh as their admin
	.venv/bin/pubkit protect $(REPOS)

protect-check: .venv/bin/pubkit ## Show how each repository differs from the ruleset, changing nothing
	.venv/bin/pubkit protect --check $(REPOS)

clean: ## Remove build artifacts
	rm -rf build dist coverage src/*.egg-info .pytest_cache tests/.pytest_cache
