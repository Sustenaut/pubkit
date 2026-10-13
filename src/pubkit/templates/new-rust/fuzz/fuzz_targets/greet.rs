//! Any text gives a greeting, never a panic.
#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|text: &str| {
    assert!({crate}::greet(text).ends_with(text));
});
