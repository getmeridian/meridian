//! Composing managed users into the Xray configuration (the skeleton has no gRPC yet).

use serde_json::{Map, Value, json};

use crate::model::{Hysteria2UsersSpec, XrayUsersSpec};

/// A users resource of the document, borrowed for composition.
#[derive(Clone, Copy, Debug)]
pub enum UsersRef<'a> {
    Vless(&'a XrayUsersSpec),
    Hysteria2(&'a Hysteria2UsersSpec),
}

impl UsersRef<'_> {
    pub fn id(&self) -> &str {
        match self {
            UsersRef::Vless(spec) => &spec.id,
            UsersRef::Hysteria2(spec) => &spec.id,
        }
    }

    fn tag(&self) -> &str {
        match self {
            UsersRef::Vless(spec) => &spec.inbound_tag,
            UsersRef::Hysteria2(spec) => &spec.inbound_tag,
        }
    }

    fn clients(&self, generations: bool) -> Vec<Value> {
        let email = |id: &str, credential: &str| {
            if generations {
                crate::xray_identity::email(id, credential)
            } else {
                id.to_string()
            }
        };
        match self {
            UsersRef::Vless(spec) => spec
                .users
                .iter()
                .map(|user| {
                    let mut client = Map::new();
                    client.insert("id".into(), json!(user.uuid));
                    client.insert("email".into(), json!(email(&user.user_id, &user.uuid)));
                    if !user.flow.is_empty() {
                        client.insert("flow".into(), json!(user.flow));
                    }
                    Value::Object(client)
                })
                .collect(),
            UsersRef::Hysteria2(spec) => spec
                .users
                .iter()
                .map(|user| json!({"auth": user.auth, "email": email(&user.user_id, &user.auth)}))
                .collect(),
        }
    }
}

/// Fill `settings.clients` of every inbound a users resource names.
pub fn compose(config: &Value, users: &[UsersRef<'_>]) -> Result<Value, String> {
    let generations = crate::xray_identity::enabled(config);
    let mut composed = config.clone();
    let inbounds = composed.get_mut("inbounds").and_then(Value::as_array_mut);
    let Some(inbounds) = inbounds else {
        return if users.is_empty() {
            Ok(composed)
        } else {
            Err("xray_config has no inbounds for its users".into())
        };
    };
    for entry in users {
        let inbound = inbounds
            .iter_mut()
            .find(|inbound| inbound.get("tag").and_then(Value::as_str) == Some(entry.tag()))
            .ok_or_else(|| format!("xray_config declares no inbound {}", entry.tag()))?;
        let object = inbound.as_object_mut().ok_or("inbound is not an object")?;
        let settings = object
            .entry("settings")
            .or_insert_with(|| Value::Object(Map::new()));
        let settings = settings
            .as_object_mut()
            .ok_or("inbound settings is not an object")?;
        settings.insert("clients".into(), Value::Array(entry.clients(generations)));
    }
    Ok(composed)
}

/// The bytes written to disk: pretty JSON with a trailing newline (deterministic, sorted keys).
pub fn render(config: &Value) -> Vec<u8> {
    let mut bytes = serde_json::to_vec_pretty(config).expect("a JSON value always serializes");
    bytes.push(b'\n');
    bytes
}
