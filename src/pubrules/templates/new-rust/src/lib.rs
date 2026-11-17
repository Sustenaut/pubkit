/*!
{description}

```
assert_eq!({crate}::greet("publet"), "hello, publet");
```

# Overview

| Item | What it is |
|---|---|
| [`greet`] | the one function this new crate starts with |

The rest of this page: [why this crate](#why-this-crate), [crate
features](#crate-features), [the minimum Rust version](#minimum-rust-version)
and [versioning](#versioning). Longer documents live in [`_documentation`]:
how every item is [tested](crate::_documentation::testing), how each
dependency is [vouched for](crate::_documentation::supply_chain), the
[security](crate::_documentation::security) policy, and the
[changelog](crate::_documentation::changelog).

# Why this crate?

Say what this crate does that a general-purpose one would get wrong here.

# Crate features

None.

# Minimum Rust version

1.85, the `rust-version` in `Cargo.toml`. It may rise in a release that
bumps the second number of the version, never in one that bumps only the
third.

# Versioning

Cargo's convention: below 1.0, a breaking change bumps the second number
and anything else the third. Each change is recorded with pubrel as it
lands, and the public API is checked against the last release by
cargo-semver-checks.
*/

/// A greeting for `name`.
///
/// ```
/// assert_eq!({crate}::greet("world"), "hello, world");
/// ```
#[must_use]
pub fn greet(name: &str) -> String {
    ["hello, ", name].concat()
}

/// Longer documents: testing, the supply chain, security, and the changelog.
///
/// These modules hold no code. Each is a document, kept in the repository as
/// Markdown and rendered here so it is versioned with the crate.
#[allow(clippy::doc_markdown, rustdoc::broken_intra_doc_links)]
pub mod _documentation {
    /// How every public item is tested.
    #[doc = include_str!("../TESTING.md")]
    pub mod testing {}

    /// Every crate this one depends on, and how its code is vouched for.
    #[doc = include_str!("../SUPPLY-CHAIN.md")]
    pub mod supply_chain {}

    /// How to report a vulnerability, and what is checked before each release.
    #[doc = include_str!("../SECURITY.md")]
    pub mod security {}

    /// Every release, newest first, generated from the package publet.
    #[doc = include_str!("../CHANGELOG.md")]
    pub mod changelog {}
}

/// The README's examples, run as tests without being rendered twice.
#[cfg(doctest)]
#[doc = include_str!("../README.md")]
struct ReadmeDoctests;

#[cfg(test)]
mod tests {
    use super::*;

    // covers: greet
    #[test]
    fn greets_by_name() {
        assert_eq!(greet(""), "hello, ");
    }
}
