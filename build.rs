fn main() {
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rustc-env=RERUN_UI_EXPECTED_RERUN_MAJOR_MINOR=0.32");
    println!("cargo:rustc-env=RERUN_UI_PROTOCOL_VERSION=1");
}
