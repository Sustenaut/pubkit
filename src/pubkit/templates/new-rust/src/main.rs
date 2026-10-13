//! The `{name}` command.

use std::process::ExitCode;

const USAGE: &str = "usage: {name} [--version | --help | NAME]";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("--version") => println!("{} {}", env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION")),
        Some("-h" | "--help") => println!("{USAGE}"),
        Some(name) => println!("{}", {crate}::greet(name)),
        None => {
            eprintln!("{USAGE}");
            return ExitCode::FAILURE;
        }
    }
    ExitCode::SUCCESS
}
