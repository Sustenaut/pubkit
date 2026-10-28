//! A tool whose help goes to the wrong stream.

fn main() {
    if std::env::args().any(|a| a == "--help") {
        eprintln!("usage: demo-tool");
        std::process::exit(2);
    }
}
