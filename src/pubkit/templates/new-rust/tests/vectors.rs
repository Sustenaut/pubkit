//! The test vectors in `testdata/`, run against this crate. Their expected
//! values come from `tools/vectors/generate.py`, not from this crate.
#![allow(clippy::unwrap_used, clippy::indexing_slicing, clippy::panic)]

/// The `"input"` and `"output"` strings of each case. The generator writes
/// one case per line and no escapes, so this needs no JSON library.
fn cases() -> Vec<(String, String)> {
    let text = include_str!("../testdata/greetings.json");
    text.lines()
        .filter(|l| l.contains("\"input\""))
        .map(|l| {
            let field = |key: &str| {
                let start = l.find(&format!("\"{key}\": \"")).unwrap() + key.len() + 5;
                let end = start + l[start..].find('"').unwrap();
                l[start..end].to_owned()
            };
            (field("input"), field("output"))
        })
        .collect()
}

// covers: greet
#[test]
fn greetings_match_the_vectors() {
    let cases = cases();
    assert!(!cases.is_empty());
    for (input, output) in cases {
        assert_eq!({crate}::greet(&input), output, "{input}");
    }
}
