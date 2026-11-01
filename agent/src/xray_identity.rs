//! Runtime identities distinguish credential generations without changing desired-user fields.

use serde_json::Value;

pub fn enabled(config: &Value) -> bool {
    config
        .pointer("/api/services")
        .and_then(Value::as_array)
        .is_some_and(|services| services.iter().any(|service| service == "RoutingService"))
}

pub fn email(user_id: &str, credential: &str) -> String {
    format!(
        "{user_id}.c{}",
        &crate::jcs::sha256_hex(credential.as_bytes())[..32]
    )
}

pub fn person_id(email: &str) -> &str {
    match email.split_once(".c") {
        Some((id, generation))
            if generation.len() == 32 && generation.bytes().all(|b| b.is_ascii_hexdigit()) =>
        {
            id
        }
        _ => email,
    }
}
