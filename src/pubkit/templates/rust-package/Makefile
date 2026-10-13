include pubkit.mk

# Binaries under test, by <NAME>_BIN_DIR.
export {bin_var} ?= $(CURDIR)/target/debug

.DEFAULT_GOAL := help
.PHONY: check

check: sync-check fmt-check lint test doc functional ## Everything CI runs
