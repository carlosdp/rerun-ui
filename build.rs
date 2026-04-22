use std::env;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=native/rive_renderer.h");
    println!("cargo:rerun-if-changed=native/rive_renderer.cc");
    println!("cargo:rerun-if-changed=third_party/rive-cpp/include");
    println!("cargo:rerun-if-changed=third_party/rive-cpp/src");
    println!("cargo:rerun-if-changed=third_party/rive-cpp/utils");
    println!("cargo:rerun-if-env-changed=CXX");
    println!("cargo:rustc-env=RERUN_UI_EXPECTED_RERUN_MAJOR_MINOR=0.29");
    println!("cargo:rustc-env=RERUN_UI_PROTOCOL_VERSION=1");

    let rive_root = Path::new("third_party/rive-cpp");
    if !rive_root.join("include/rive/file.hpp").exists() {
        panic!(
            "third_party/rive-cpp is missing; run `git submodule update --init --recursive` before building"
        );
    }

    let cairo = pkg_config::Config::new().probe("cairo").expect(
        "failed to locate cairo via pkg-config; install libcairo2-dev or the platform equivalent",
    );

    if env::consts::OS != "windows" {
        println!("cargo:rustc-link-lib=m");
    }

    let mut build = cc::Build::new();
    build.cpp(true);
    build.std("c++17");
    build.define("_RIVE_INTERNAL_", None);
    build.flag_if_supported("-fPIC");
    build.flag_if_supported("-Wno-unused-parameter");
    build.flag_if_supported("-Wno-missing-field-initializers");
    build.include("native");
    build.include(rive_root);
    build.include(rive_root.join("include"));
    build.include(rive_root.join("utils"));
    for include_path in cairo.include_paths {
        build.include(include_path);
    }

    if env::var_os("CXX").is_none() && env::consts::OS != "windows" {
        build.compiler("clang++");
    }

    build.file("native/rive_renderer.cc");
    for source in cpp_sources(rive_root.join("src")) {
        build.file(source);
    }
    build.compile("rive_native");
}

fn cpp_sources(root: PathBuf) -> Vec<PathBuf> {
    let mut sources: Vec<PathBuf> = WalkDir::new(root)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .map(|entry| entry.into_path())
        .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("cpp"))
        .collect();
    sources.sort();
    sources
}
