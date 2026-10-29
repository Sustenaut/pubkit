# Managed by pubkit {version}: `pubkit sync` rewrites this file, and
# `pubkit sync --check` fails when it differs. Include it from the Makefile,
# and override the variables below there; targets of your own go there too.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
export PATH := $(HOME)/.cargo/bin:$(CURDIR)/.venv/bin:$(PATH)

CARGO ?= cargo
CARGO_SCOPE ?= --workspace
PYTEST_ARGS ?=
# The crates published to crates.io. Each gets a SUPPLY-CHAIN.md, each
# library among them a TESTING.md, and each is packaged by publish-check and
# built by msrv.
SUPPLY_CHAIN_CRATES ?=
PUBLISHED_CRATES ?= $(SUPPLY_CHAIN_CRATES)
# The generator of testdata/, and the directories it writes.
VECTORS_GENERATE ?= ./tools/vectors/generate.py
VECTORS_DIRS ?= $(wildcard testdata crates/*/testdata)
# fuzz/fuzz_targets/*.rs, and how long each runs in fuzz-smoke.
FUZZ_TARGETS ?= $(notdir $(basename $(wildcard fuzz/fuzz_targets/*.rs)))
FUZZ_SECONDS ?= 60
# The oldest Rust promised: CI passes the lowest rust-version any package
# states.
MSRV ?= $(shell sed -n 's/^rust-version = "\(.*\)"/\1/p' Cargo.toml | head -1)

.PHONY: help venv lock sync-check fmt fmt-check lint test doc build functional coverage supply-chain supply-chain-check \
        doc-coverage doc-coverage-check vectors vectors-check publish-check msrv fuzz-smoke deny vet required

help: ## Show available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  %-14s %s\n", $$1, $$2}'

# .venv is rebuilt whenever tests/requirements.lock changes, so the pubkit
# in it is always the release the repository pins: every target below that
# runs .venv/bin/pubkit depends on it.
VENV := .venv/.installed

venv:: $(VENV) ## Python environment for the functional suite, hash-locked

$(VENV): tests/requirements.lock
	rm -rf .venv
	python3 -m venv .venv
	.venv/bin/pip install --quiet --require-hashes -r tests/requirements.lock
	touch $@

lock: $(VENV) ## Pin tests/requirements.txt, with hashes, in tests/requirements.lock
	.venv/bin/pubkit lock

sync-check: $(VENV) ## Fail if pubkit's managed files differ from the pinned version's
	.venv/bin/pubkit sync --check

fmt: ## Format Rust sources
	$(CARGO) fmt --all

fmt-check: ## Verify formatting without modifying
	$(CARGO) fmt --all -- --check

lint: ## Clippy, warnings are errors
	$(CARGO) clippy $(CARGO_SCOPE) --all-targets -- -D warnings

test: ## Rust unit tests
	$(CARGO) test $(CARGO_SCOPE)

doc: ## Documentation, warnings are errors
	RUSTDOCFLAGS="-D warnings" $(CARGO) doc $(CARGO_SCOPE) --no-deps

build: ## Debug build
	$(CARGO) build $(CARGO_SCOPE)

functional: build $(VENV) ## The Gherkin suite against the built binaries
	cd tests && pytest -q $(PYTEST_ARGS)

# Everything `functional` runs, and the Rust tests, on a build instrumented
# by cargo-llvm-cov, so the suite's runs of the binaries count too. Paths in
# the report are relative to the repository, for SonarQube Cloud.
coverage: $(VENV) ## The Rust tests and the Gherkin suite on an instrumented build; LCOV in coverage/lcov.info (needs cargo-llvm-cov)
	rm -rf coverage && mkdir coverage
	source <($(CARGO) llvm-cov show-env --sh) \
	  && $(CARGO) llvm-cov clean --workspace \
	  && $(CARGO) build $(CARGO_SCOPE) \
	  && $(CARGO) test $(CARGO_SCOPE) \
	  && (cd tests && pytest -q $(PYTEST_ARGS)) \
	  && $(CARGO) llvm-cov report --lcov --output-path coverage/lcov.info
	sed -i.bak 's|^SF:$(CURDIR)/|SF:|' coverage/lcov.info && rm coverage/lcov.info.bak

supply-chain: $(VENV) ## Regenerate SUPPLY-CHAIN.md for each of SUPPLY_CHAIN_CRATES from supply-chain/
	.venv/bin/pubkit supply-chain $(SUPPLY_CHAIN_CRATES)

supply-chain-check: $(VENV) ## Fail if a committed SUPPLY-CHAIN.md is stale
	.venv/bin/pubkit supply-chain --check $(SUPPLY_CHAIN_CRATES)

# The targets below enforce the Rust crate standard's checked rules; `pubkit
# required` lists which each CI job runs here.

doc-coverage: $(VENV) ## Regenerate each published library's TESTING.md
	.venv/bin/pubkit doc-coverage $(PUBLISHED_CRATES)

doc-coverage-check: $(VENV) ## Fail if a public function has no example, a test covers no real item, or TESTING.md is stale
	.venv/bin/pubkit doc-coverage --check $(PUBLISHED_CRATES)

vectors: $(VENV) ## Regenerate the test vectors from their independent sources
	$(VECTORS_GENERATE)

vectors-check: vectors ## Fail if the committed test vectors differ from what the sources give
	@test -n "$(VECTORS_DIRS)" || { echo "no testdata/ here: the standard asks for test vectors (RS-TEST-01)"; exit 1; }
	git diff --exit-code -- $(VECTORS_DIRS)

# The verify build gets a target directory of its own. Sharing target/
# leaves dep-info naming the packaged copy's sources, after which cargo
# reports the crate Fresh however src/ changes, and tests run a stale binary.
publish-check: ## Package each published crate and verify it builds as published
	for crate in $(PUBLISHED_CRATES); do \
	  CARGO_TARGET_DIR=$(CURDIR)/target/publish-check $(CARGO) publish --dry-run --locked -p $$crate; \
	done

msrv: ## Build the published crates on their minimum Rust version (rustup toolchain install $(MSRV))
	$(CARGO) +$(MSRV) check --locked $(foreach crate,$(PUBLISHED_CRATES),-p $(crate))

fuzz-smoke: ## Replay fuzz regressions, then fuzz each target briefly (needs nightly and cargo-fuzz)
	@test -n "$(FUZZ_TARGETS)" || { echo "no fuzz/fuzz_targets/ here: the standard asks for fuzzing (RS-FUZZ-01)"; exit 1; }
	cd fuzz && cargo +nightly fuzz build
	cd fuzz && for target in $(FUZZ_TARGETS); do \
	  mkdir -p corpus/$$target regressions/$$target; \
	  cargo +nightly fuzz run $$target corpus/$$target regressions/$$target -- \
	    -max_total_time=$(FUZZ_SECONDS) -timeout=10 || exit 1; \
	done

deny: ## Licence, advisory, ban and source audit
	$(CARGO) deny --locked check

vet: ## Every dependency is audited, trusted, or exempt with its evidence
	cargo vet --locked

required: $(VENV) ## The targets each CI job runs here, from the standard
	.venv/bin/pubkit required
