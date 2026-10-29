//! Canonical JSON, signatures, ordering, and composition against the Python contract.

mod common;

use meridian_agent::bootstrap::Bootstrap;
use meridian_agent::compose::{UsersRef, compose};
use meridian_agent::jcs;
use meridian_agent::model::{NodeStateRef, Resource, Spec};
use meridian_agent::verify::{Admission, admit, signed_bytes, verify_desired};
use serde_json::{Value, json};

fn vectors() -> Value {
    let text = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/vectors.json"
    ))
    .unwrap();
    serde_json::from_str(&text).unwrap()
}

fn fixture_bootstrap(v: &Value) -> Bootstrap {
    Bootstrap::from_json(&serde_json::to_vec(&v["bootstrap"]).unwrap(), false).unwrap()
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}

#[test]
fn canonical_bytes_match_python_byte_for_byte() {
    let v = vectors();
    let expected = unhex(v["canonical"]["bytes_hex"].as_str().unwrap());
    assert_eq!(jcs::canonical(&v["canonical"]["value"]).unwrap(), expected);
}

#[test]
fn signed_bytes_match_python_for_a_contract_document() {
    let v = vectors();
    let expected = unhex(v["signed_bytes_hex"].as_str().unwrap());
    assert_eq!(signed_bytes(&v["document"]).unwrap(), expected);
}

#[test]
fn key_id_is_sha256_of_the_raw_public_key() {
    let v = vectors();
    assert_eq!(fixture_bootstrap(&v).key_id, v["key_id"].as_str().unwrap());
}

#[test]
fn python_signed_document_verifies_and_hashes_each_resource_as_received() {
    let v = vectors();
    let verified = verify_desired(&v["document"], &fixture_bootstrap(&v)).unwrap();
    assert_eq!(verified.reference.fleet_epoch, 3);
    assert_eq!(verified.reference.revision, 7);
    for resource in &verified.resources {
        assert_eq!(
            resource.hash,
            v["resource_sha256"][&resource.id].as_str().unwrap(),
            "{}",
            resource.id
        );
    }
    let kinds: Vec<&str> = verified.resources.iter().map(|r| r.kind.as_str()).collect();
    assert_eq!(
        kinds,
        [
            "binary_pin",
            "file",
            "xray_config",
            "xray_users",
            "systemd_unit",
            "firewall_rule"
        ]
    );
    assert!(matches!(verified.resources[5].spec, Spec::Unsupported));
    assert!(
        verified.resources[..5]
            .iter()
            .all(|r| !matches!(r.spec, Spec::Unsupported | Spec::Invalid(_)))
    );
}

#[test]
fn tampered_document_is_refused() {
    let v = vectors();
    let err = verify_desired(&v["tampered"], &fixture_bootstrap(&v)).unwrap_err();
    assert_eq!(err, "signature does not verify");
}

#[test]
fn documents_for_another_node_key_or_with_floats_are_refused() {
    let v = vectors();
    let boot = fixture_bootstrap(&v);

    let mut other_node = boot.clone();
    other_node.node_id = "exit-2".into();
    assert!(
        verify_desired(&v["document"], &other_node)
            .unwrap_err()
            .contains("another fleet or node")
    );

    let mut other_key = boot.clone();
    other_key.key_id = "00".repeat(32);
    assert!(
        verify_desired(&v["document"], &other_key)
            .unwrap_err()
            .contains("unknown key")
    );

    let mut floats = v["document"].clone();
    floats["resources"][0]["size"] = json!(1.5);
    assert!(
        verify_desired(&floats, &boot)
            .unwrap_err()
            .contains("floating-point")
    );
}

#[test]
fn resource_hash_mismatch_is_refused_even_with_a_valid_signature() {
    let key = common::signing_key();
    let boot = Bootstrap::from_json(
        &serde_json::to_vec(&common::bootstrap_json("https://198.51.100.10/p", &key)).unwrap(),
        false,
    )
    .unwrap();
    let mut doc = common::document(
        1,
        1,
        &[common::file_inline("f", "/etc/netagent/f", b"x")],
        &key,
    );
    doc["sha256"] = json!("00".repeat(32));
    let sig = {
        use base64::Engine as _;
        use ed25519_dalek::Signer;
        let signature = key.sign(&signed_bytes(&doc).unwrap());
        base64::engine::general_purpose::STANDARD.encode(signature.to_bytes())
    };
    doc["signature"]["signature"] = json!(sig);
    assert_eq!(
        verify_desired(&doc, &boot).unwrap_err(),
        "sha256 does not match resources"
    );
}

#[test]
fn bootstrap_requires_https_unless_tests_allow_http() {
    let key = common::signing_key();
    let plain =
        serde_json::to_vec(&common::bootstrap_json("http://198.51.100.10/p", &key)).unwrap();
    assert!(Bootstrap::from_json(&plain, false).is_err());
    assert!(Bootstrap::from_json(&plain, true).is_ok());
    let debug = format!("{:?}", Bootstrap::from_json(&plain, true).unwrap());
    assert!(!debug.contains(common::TOKEN));
}

fn r(epoch: u64, revision: u64, sha: char) -> NodeStateRef {
    NodeStateRef {
        fleet_epoch: epoch,
        revision,
        sha256: sha.to_string().repeat(64),
    }
}

#[test]
fn ordering_follows_epoch_then_revision() {
    // First document.
    assert_eq!(admit(None, None, &r(1, 1, 'a')), Admission::Apply);
    // An older epoch than ever accepted is refused even with nothing held.
    assert!(matches!(
        admit(Some(2), None, &r(1, 9, 'a')),
        Admission::Refuse(_)
    ));
    // Lower revision in the same epoch.
    assert!(matches!(
        admit(Some(1), Some(&r(1, 5, 'a')), &r(1, 4, 'b')),
        Admission::Refuse(_)
    ));
    // Same revision, different content.
    assert!(matches!(
        admit(Some(1), Some(&r(1, 5, 'a')), &r(1, 5, 'b')),
        Admission::Refuse(_)
    ));
    assert_eq!(
        admit(Some(1), Some(&r(1, 5, 'a')), &r(1, 5, 'a')),
        Admission::AlreadyHeld
    );
    // Same content, newer revision: record only.
    assert_eq!(
        admit(Some(1), Some(&r(1, 5, 'a')), &r(1, 6, 'a')),
        Admission::RecordRevision
    );
    assert_eq!(
        admit(Some(1), Some(&r(1, 5, 'a')), &r(1, 6, 'b')),
        Admission::Apply
    );
    // A higher epoch wins with a lower revision.
    assert_eq!(
        admit(Some(1), Some(&r(1, 5, 'a')), &r(2, 1, 'b')),
        Admission::Apply
    );
}

#[test]
fn users_compose_into_their_inbounds() {
    let resources = common::parse(&[
        common::xray_config(&[], None),
        common::vless_users(&[
            (
                "u-a",
                "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
                "xtls-rprx-vision",
            ),
            ("u-b", "4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00", ""),
        ]),
        common::hy2_users(&[("u-a", "0123456789abcdef0123")]),
    ]);
    let Spec::XrayConfig(config) = &resources[0].spec else {
        panic!()
    };
    let Spec::XrayUsers(vless) = &resources[1].spec else {
        panic!()
    };
    let Spec::Hysteria2Users(hy2) = &resources[2].spec else {
        panic!()
    };
    let composed = compose(
        &config.config,
        &[UsersRef::Vless(vless), UsersRef::Hysteria2(hy2)],
    )
    .unwrap();
    assert_eq!(
        composed["inbounds"][0]["settings"],
        json!({"decryption": "none", "clients": [
            {"id": "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00", "email": "u-a", "flow": "xtls-rprx-vision"},
            {"id": "4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00", "email": "u-b"},
        ]})
    );
    assert_eq!(
        composed["inbounds"][1]["settings"]["clients"],
        json!([{"auth": "0123456789abcdef0123", "email": "u-a"}])
    );
    // The desired config itself is untouched.
    assert_eq!(
        config.config["inbounds"][0]["settings"]["clients"],
        json!([])
    );
}

#[test]
fn users_for_an_undeclared_inbound_fail_composition() {
    let mut users = common::vless_users(&[]);
    users["inbound_tag"] = json!("missing");
    let resources = common::parse(&[common::xray_config(&[], None), users]);
    let Spec::XrayConfig(config) = &resources[0].spec else {
        panic!()
    };
    let Spec::XrayUsers(vless) = &resources[1].spec else {
        panic!()
    };
    assert!(
        compose(&config.config, &[UsersRef::Vless(vless)])
            .unwrap_err()
            .contains("missing")
    );
}

#[test]
fn unknown_fields_make_a_supported_resource_invalid() {
    let mut value = common::file_inline("f", "/etc/netagent/f", b"x");
    value["surprise"] = json!(1);
    assert!(matches!(
        Resource::parse(&value).unwrap().spec,
        Spec::Invalid(_)
    ));
}
