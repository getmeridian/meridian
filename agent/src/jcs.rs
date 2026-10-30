//! Canonical JSON and protocol hashes.
//!
//! The service computes canonical JSON with Python's
//! `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
//! serde_json without `preserve_order` produces the same bytes for protocol
//! JSON: maps are `BTreeMap`s (keys sorted by code point, which equals UTF-16
//! order for ASCII keys), control characters escape as `\b \f \n \r \t` or a
//! lowercase `\u00xx`, and non-ASCII stays literal UTF-8. Floats are refused,
//! because Python and Rust print them differently.

use serde_json::Value;
use sha2::{Digest, Sha256};

/// Refuse any floating-point number anywhere in the value.
pub fn check_no_floats(value: &Value) -> Result<(), String> {
    match value {
        Value::Number(number) if !(number.is_i64() || number.is_u64()) => {
            Err("floating-point numbers are not allowed".to_string())
        }
        Value::Array(items) => items.iter().try_for_each(check_no_floats),
        Value::Object(map) => map.values().try_for_each(check_no_floats),
        _ => Ok(()),
    }
}

/// Canonical serialization of a JSON value.
pub fn canonical(value: &Value) -> Result<Vec<u8>, String> {
    check_no_floats(value)?;
    serde_json::to_vec(value).map_err(|err| format!("cannot serialize JSON: {err}"))
}

/// Protocol hash of a JSON value: lowercase hex SHA-256 of its canonical form.
pub fn canonical_sha256(value: &Value) -> Result<String, String> {
    Ok(sha256_hex(&canonical(value)?))
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(DIGITS[(byte >> 4) as usize] as char);
        out.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    out
}

pub fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn keys_are_sorted_and_output_is_compact() {
        let value = json!({"b": 1, "a": {"d": [true, null], "c": "x"}});
        assert_eq!(
            canonical(&value).unwrap(),
            br#"{"a":{"c":"x","d":[true,null]},"b":1}"#
        );
    }

    #[test]
    fn strings_escape_like_python_json() {
        let value = json!("\u{1}\u{8}\u{c}\n\r\t\"\\/\u{7f}é\u{2028}");
        let expected = "\"\\u0001\\b\\f\\n\\r\\t\\\"\\\\/\u{7f}é\u{2028}\"";
        assert_eq!(
            String::from_utf8(canonical(&value).unwrap()).unwrap(),
            expected
        );
    }

    #[test]
    fn floats_are_refused_anywhere() {
        let value: Value = serde_json::from_str(r#"{"a":[1,{"b":1.0}]}"#).unwrap();
        assert!(canonical(&value).is_err());
        let exponent: Value = serde_json::from_str("1e3").unwrap();
        assert!(canonical(&exponent).is_err());
    }
}
