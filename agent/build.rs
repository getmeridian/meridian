//! Names the build: `NETAGENT_VERSION` is the crate version, plus `+<build id>` when
//! `make agent` passes `NETAGENT_BUILD_ID` (a hash of the sources). The deployer tells an
//! upgraded agent from the one it replaced by this string in the agent's hello.

fn main() {
    println!("cargo:rerun-if-env-changed=NETAGENT_BUILD_ID");
    let crate_version = std::env::var("CARGO_PKG_VERSION").expect("cargo sets CARGO_PKG_VERSION");
    let build = std::env::var("NETAGENT_BUILD_ID").unwrap_or_default();
    let valid = !build.is_empty()
        && build.len() <= 40
        && build
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    let version = if valid {
        format!("{crate_version}+{build}")
    } else {
        crate_version
    };
    println!("cargo:rustc-env=NETAGENT_VERSION={version}");
}
