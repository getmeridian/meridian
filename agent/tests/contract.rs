//! Canonical JSON, PKI, signatures, ordering, and composition against the Python contract.

mod common;

use common::pki::TestPki;
use ed25519_dalek::SigningKey;
use meridian_agent::bootstrap::Bootstrap;
use meridian_agent::compose::{UsersRef, compose};
use meridian_agent::identity::{
    Ca, Identity, ServiceCert, check_node_certificate, proof_signed_bytes,
};
use meridian_agent::jcs;
use meridian_agent::model::{NodeStateRef, Resource, Spec};
use meridian_agent::verify::{Admission, Trust, admit, signed_bytes, verify_desired};
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

fn text<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap()
}

fn fixture_ca(v: &Value) -> Ca {
    Ca::pinned(
        text(v, "ca_certificate_pem"),
        &fixture_bootstrap(v).ca_sha256,
    )
    .unwrap()
}

fn fixture_service(v: &Value) -> ServiceCert {
    let now = v["now"].as_u64().unwrap();
    ServiceCert::check(
        text(v, "service_certificate_pem"),
        &fixture_ca(v),
        common::FLEET_ID,
        now,
    )
    .unwrap()
}

fn fixture_trust(v: &Value) -> Trust {
    let service = fixture_service(v);
    Trust {
        fleet_id: common::FLEET_ID.into(),
        node_id: common::NODE_ID.into(),
        public_key: service.public_key,
        key_id: service.key_id,
    }
}

fn node_key(v: &Value) -> SigningKey {
    SigningKey::from_bytes(&unhex(text(v, "node_key_hex")).try_into().unwrap())
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
fn python_certificates_chain_to_the_pinned_ca_and_key_id_is_the_certificate_hash() {
    let v = vectors();
    assert_eq!(fixture_service(&v).key_id, text(&v, "key_id"));
    let ca = fixture_ca(&v);
    let key = node_key(&v).verifying_key();
    let node = text(&v, "node_certificate_pem");
    check_node_certificate(node, &ca, common::FLEET_ID, common::NODE_ID, &key).unwrap();

    // A certificate only counts in its own role, for its own node and key.
    let now = v["now"].as_u64().unwrap();
    assert!(ServiceCert::check(node, &ca, common::FLEET_ID, now).is_err());
    let service = text(&v, "service_certificate_pem");
    assert!(check_node_certificate(service, &ca, common::FLEET_ID, common::NODE_ID, &key).is_err());
    assert!(check_node_certificate(node, &ca, common::FLEET_ID, "exit-2", &key).is_err());
    let other = SigningKey::from_bytes(&[8u8; 32]).verifying_key();
    assert!(check_node_certificate(node, &ca, common::FLEET_ID, common::NODE_ID, &other).is_err());
    assert!(ServiceCert::check(service, &ca, common::FLEET_ID, now + 11 * 365 * 86_400).is_err());

    // Another CA, even one issuing the same shapes, is not the pinned one.
    let pki = TestPki::new();
    assert!(Ca::pinned(&pki.ca_pem, &fixture_bootstrap(&v).ca_sha256).is_err());
    let foreign = Ca::pinned(&pki.ca_pem, &pki.ca_sha256()).unwrap();
    assert!(ServiceCert::check(service, &foreign, common::FLEET_ID, now).is_err());
}

#[test]
fn hello_proofs_match_python_byte_for_byte() {
    let v = vectors();
    let hello = &v["hello"];
    let expected = unhex(text(&v, "hello_signed_bytes_hex"));
    assert_eq!(proof_signed_bytes(hello).unwrap(), expected);
    // Ed25519 is deterministic: the same key signs the same bytes identically.
    let identity = Identity {
        key: node_key(&v),
        certificate_pem: text(&v, "node_certificate_pem").into(),
        ca: fixture_ca(&v),
        service: fixture_service(&v),
    };
    assert_eq!(
        identity.sign_proof(hello).unwrap(),
        hello["proof"]["signature"].as_str().unwrap()
    );
}

#[test]
fn python_signed_document_verifies_and_hashes_each_resource_as_received() {
    let v = vectors();
    let verified = verify_desired(&v["document"], &fixture_trust(&v)).unwrap();
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
    let err = verify_desired(&v["tampered"], &fixture_trust(&v)).unwrap_err();
    assert_eq!(err, "signature does not verify");
}

#[test]
fn documents_for_another_node_key_or_with_floats_are_refused() {
    let v = vectors();
    let trust = fixture_trust(&v);

    let mut other_node = trust.clone();
    other_node.node_id = "exit-2".into();
    assert!(
        verify_desired(&v["document"], &other_node)
            .unwrap_err()
            .contains("another fleet or node")
    );

    let mut other_key = trust.clone();
    other_key.key_id = "00".repeat(32);
    assert!(
        verify_desired(&v["document"], &other_key)
            .unwrap_err()
            .contains("unknown key")
    );

    let mut floats = v["document"].clone();
    floats["resources"][0]["size"] = json!(1.5);
    assert!(
        verify_desired(&floats, &trust)
            .unwrap_err()
            .contains("floating-point")
    );
}

#[test]
fn resource_hash_mismatch_is_refused_even_with_a_valid_signature() {
    let pki = TestPki::new();
    let service = ServiceCert::check(
        &pki.service_pem,
        &Ca::pinned(&pki.ca_pem, &pki.ca_sha256()).unwrap(),
        common::FLEET_ID,
        1_793_268_245,
    )
    .unwrap();
    let trust = Trust {
        fleet_id: common::FLEET_ID.into(),
        node_id: common::NODE_ID.into(),
        public_key: service.public_key,
        key_id: service.key_id,
    };
    let mut doc = common::document(
        1,
        1,
        &[common::file_inline("f", "/etc/netagent/f", b"x")],
        &pki,
    );
    doc["sha256"] = json!("00".repeat(32));
    let sig = {
        use base64::Engine as _;
        use ed25519_dalek::Signer;
        let signature = pki.service_key.sign(&signed_bytes(&doc).unwrap());
        base64::engine::general_purpose::STANDARD.encode(signature.to_bytes())
    };
    doc["signature"]["signature"] = json!(sig);
    assert_eq!(
        verify_desired(&doc, &trust).unwrap_err(),
        "sha256 does not match resources"
    );
}

#[test]
fn bootstrap_requires_https_and_refuses_a_tls_pin_it_cannot_honour() {
    let pki = TestPki::new();
    let plain =
        serde_json::to_vec(&common::bootstrap_json("http://198.51.100.10/p", &pki)).unwrap();
    assert!(Bootstrap::from_json(&plain, false).is_err());
    assert!(Bootstrap::from_json(&plain, true).is_ok());
    let debug = format!("{:?}", Bootstrap::from_json(&plain, true).unwrap());
    assert!(!debug.contains(common::ENROLL_TOKEN));

    let mut pinned = common::bootstrap_json("https://198.51.100.10/p", &pki);
    pinned["tls_pin_sha256"] = json!("ab".repeat(32));
    let err = Bootstrap::from_json(&serde_json::to_vec(&pinned).unwrap(), false).unwrap_err();
    assert!(err.contains("tls_pin_sha256"), "{err}");

    let mut short = common::bootstrap_json("https://198.51.100.10/p", &pki);
    short["enrollment_token"] = json!("too-short");
    assert!(Bootstrap::from_json(&serde_json::to_vec(&short).unwrap(), false).is_err());

    // An enrolled bootstrap (token nulled) still loads.
    let mut spent = common::bootstrap_json("https://198.51.100.10/p", &pki);
    spent["enrollment_token"] = Value::Null;
    spent["token_expires_at"] = Value::Null;
    assert!(Bootstrap::from_json(&serde_json::to_vec(&spent).unwrap(), false).is_ok());
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
