/*!
Doubles numbers, and parses them.

```
assert_eq!(demo::twice(2), 4);
```

# Overview

[`twice`] doubles; [`parse`] reads a number. See [Examples](#examples) and
[Related crates](#related-crates).

# Examples

## Example: a rejected input

```
assert!(demo::parse("x").is_err());
```

# Related crates

| Crate | Version | Why not |
|---|---|---|
| `itoa` | 1.0.9 | It formats, and does not parse. |
| `lexical` | | It is general. |

# Minimum Rust version

1.85. It may rise in a release that bumps the second number.

# Versioning

Cargo's convention. The public API is checked against the last release.
*/

/// Twice `n`.
///
/// ```
/// assert_eq!(demo::twice(3), 6);
/// ```
pub fn twice(n: u64) -> u64 {
    n * 2
}

/// Thrice `n`, with no example.
pub fn thrice(n: u64) -> u64 {
    n * 3
}

/// Parse a number.
///
/// ```
/// # let text = "7";
/// let n = demo::parse(text).unwrap();
/// assert_eq!(n, 7);
/// ```
pub fn parse(text: &str) -> Result<u64, ParseError> {
    text.parse().map_err(|_| ParseError::NotANumber)
}

/// Why a parse failed.
#[derive(Debug)]
pub enum ParseError {
    /// The text is not a number.
    NotANumber,
}
