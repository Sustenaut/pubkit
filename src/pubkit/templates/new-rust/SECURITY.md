# Security

## Reporting a vulnerability

Report it privately through GitHub:
**[report a vulnerability](https://github.com/{owner}/{name}/security/advisories/new)**
(the repository's Security tab, "Report a vulnerability"). Please don't open
a public issue for it.

## Supported versions

Releases are cut only from `main`, with pubrel. A fix is released as the
next version. Earlier versions get no separate fixes.

## What is checked

On every pull request, pubkit's `rust-ci.yml` runs every check the Rust
crate standard requires: formatting, clippy, docs, doc coverage, packaging,
the tests on Linux, macOS, Windows, i686 and s390x, the Gherkin suite, the
test vectors, the minimum Rust version, fuzzing, `cargo deny` and
`cargo vet`. A release also needs a current quality audit in `audit/`.

**Fuzzing found** nothing yet.

**In the code:** `unsafe` is forbidden (`unsafe_code = "forbid"`). Release
builds keep integer overflow checks.
