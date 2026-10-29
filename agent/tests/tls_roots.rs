//! `NETAGENT_TLS_CA_FILE`: a private CA replaces the WebPKI roots, and a bad file stops the agent.

mod common;

use common::TempDir;
use meridian_agent::fetch::load_roots;
use serde_json::Value;

fn fixture_ca_pem() -> String {
    let vectors: Value =
        serde_json::from_str(include_str!("fixtures/vectors.json")).expect("vectors.json");
    vectors["ca_certificate_pem"].as_str().unwrap().to_string()
}

#[test]
fn a_pem_ca_file_loads_as_the_only_roots() {
    let dir = TempDir::new("tls-ca");
    let path = dir.0.join("ca.pem");
    std::fs::write(&path, fixture_ca_pem()).unwrap();

    assert!(load_roots(&path).is_ok());
}

#[test]
fn a_ca_file_without_a_certificate_is_refused() {
    let dir = TempDir::new("tls-ca-empty");
    let path = dir.0.join("ca.pem");
    std::fs::write(&path, "not a certificate\n").unwrap();

    let err = load_roots(&path).expect_err("refused");
    assert!(err.contains("holds no certificate"), "{err}");
}

#[test]
fn a_missing_ca_file_is_refused() {
    let dir = TempDir::new("tls-ca-missing");

    let err = load_roots(&dir.0.join("absent.pem")).expect_err("refused");
    assert!(err.contains("cannot read CA file"), "{err}");
}
