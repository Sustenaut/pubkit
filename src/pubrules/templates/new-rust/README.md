# {name}

{description}

Licensed under Apache-2.0.

### Documentation

[docs.rs/{name}](https://docs.rs/{name}): the overview, examples, how every
item is tested, and how each dependency is vouched for.

### Usage

```toml
[dependencies]
{name} = "0.1"
```

### Example

```rust
assert_eq!({crate}::greet("publet"), "hello, publet");
```

### Minimum Rust version

Rust 1.85, tested in CI. The minimum may rise in a release that bumps the
minor version, and never in a patch release.

### Security

Report a vulnerability privately through the repository's Security tab.
[`SECURITY.md`](SECURITY.md) describes what is checked, and
[`SUPPLY-CHAIN.md`](SUPPLY-CHAIN.md) how each dependency is vouched for.
