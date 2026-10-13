# The shared targets come from pubkit.mk, which pubkit manages: the ones the
# Rust crate standard requires (`make required` lists what CI runs), and
# venv, lock, fmt, test, build and supply-chain. This file adds {name}'s own.
SUPPLY_CHAIN_CRATES := {name}

include pubkit.mk

# Binaries under test, by <NAME>_BIN_DIR.
export {bin_var} ?= $(CURDIR)/target/debug

.DEFAULT_GOAL := help
.PHONY: check clean

check: sync-check fmt-check lint test doc doc-coverage-check functional vectors-check publish-check supply-chain-check vet deny ## Everything CI runs

clean: ## Remove build artifacts
	cargo clean
	rm -rf .pytest_cache tests/.pytest_cache
