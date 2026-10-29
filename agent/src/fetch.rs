//! HTTP: the control-service client (poll, report, blobs) and artifact downloads.

use std::io::{self, Write};
use std::time::Duration;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::bootstrap::Bootstrap;

/// Largest JSON body the agent accepts (the contract's `max_response_bytes`).
pub const MAX_RESPONSE_BYTES: u64 = 32 * 1024 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(600);

/// Downloads an artifact into a writer.
pub trait Fetch {
    /// `source` is `service` (the blob endpoint, with the node token) or an https URL
    /// (never sent the token). At most `limit` bytes are accepted.
    fn fetch(
        &self,
        source: &str,
        sha256: &str,
        limit: u64,
        out: &mut dyn Write,
    ) -> Result<(), String>;
}

#[derive(Debug)]
pub enum HttpError {
    /// Connection, TLS, or timeout failure.
    Network(String),
    /// A non-2xx answer, with the contract error body when the service sent one.
    Status {
        status: u16,
        retry_after: Option<u64>,
        code: Option<String>,
    },
    /// A 2xx answer the agent cannot parse.
    Protocol(String),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::Network(msg) => write!(f, "network error: {msg}"),
            HttpError::Status { status, code, .. } => match code {
                Some(code) => write!(f, "service answered {status} ({code})"),
                None => write!(f, "service answered {status}"),
            },
            HttpError::Protocol(msg) => write!(f, "bad response: {msg}"),
        }
    }
}

/// Talks to `{service_url}/agent/v1/...` with the bootstrap bearer token.
pub struct Client {
    agent: ureq::Agent,
    base: String,
    service_url: String,
    token: String,
    allow_http: bool,
}

impl Client {
    /// `allow_http` exists for tests; production requires https everywhere.
    pub fn new(bootstrap: &Bootstrap, allow_http: bool) -> Client {
        let agent = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .https_only(!allow_http)
            .timeout_connect(Some(Duration::from_secs(10)))
            .user_agent(format!("netagent/{}", crate::VERSION))
            .build()
            .new_agent();
        Client {
            agent,
            base: format!("{}/agent/v1", bootstrap.service_url),
            service_url: bootstrap.service_url.clone(),
            token: bootstrap.node_token.clone(),
            allow_http,
        }
    }

    /// Scrub the secret service path from an error message.
    fn scrub(&self, message: String) -> String {
        message.replace(&self.service_url, "<service>")
    }

    pub fn post(
        &self,
        endpoint: &str,
        body: &Value,
        timeout: Duration,
    ) -> Result<Value, HttpError> {
        let bytes = serde_json::to_vec(body).map_err(|err| HttpError::Protocol(err.to_string()))?;
        let response = self
            .agent
            .post(format!("{}/{endpoint}", self.base))
            .config()
            .timeout_global(Some(timeout))
            .build()
            .header("Authorization", format!("Bearer {}", self.token))
            .header("Content-Type", "application/json")
            .send(&bytes[..])
            .map_err(|err| HttpError::Network(self.scrub(err.to_string())))?;
        let status = response.status().as_u16();
        let retry_header = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.trim().parse::<u64>().ok());
        let body = response
            .into_body()
            .into_with_config()
            .limit(MAX_RESPONSE_BYTES)
            .read_to_vec()
            .map_err(|err| HttpError::Network(self.scrub(err.to_string())))?;
        if !(200..300).contains(&status) {
            let parsed: Option<crate::model::AgentErrorResponse> =
                serde_json::from_slice(&body).ok();
            let error = parsed.map(|p| p.error);
            return Err(HttpError::Status {
                status,
                retry_after: error
                    .as_ref()
                    .and_then(|e| e.retry_after_seconds)
                    .or(retry_header),
                code: error.map(|e| e.code),
            });
        }
        serde_json::from_slice(&body).map_err(|err| HttpError::Protocol(err.to_string()))
    }

    pub fn poll(&self, request: &crate::model::PollRequest) -> Result<Value, HttpError> {
        let body =
            serde_json::to_value(request).map_err(|err| HttpError::Protocol(err.to_string()))?;
        self.post(
            "poll",
            &body,
            Duration::from_secs(request.wait_seconds + 10),
        )
    }

    pub fn report(&self, report: &crate::model::ObservedStateReport) -> Result<Value, HttpError> {
        let body =
            serde_json::to_value(report).map_err(|err| HttpError::Protocol(err.to_string()))?;
        self.post("report", &body, REQUEST_TIMEOUT)
    }
}

impl Fetch for Client {
    fn fetch(
        &self,
        source: &str,
        sha256: &str,
        limit: u64,
        out: &mut dyn Write,
    ) -> Result<(), String> {
        let request = if source == "service" {
            if !crate::jcs::is_sha256_hex(sha256) {
                return Err("invalid blob hash".into());
            }
            self.agent
                .get(format!("{}/blobs/{sha256}", self.base))
                .header("Authorization", format!("Bearer {}", self.token))
        } else if source.starts_with("https://")
            || (self.allow_http && source.starts_with("http://"))
        {
            self.agent.get(source)
        } else {
            return Err("refused a source that is not https".into());
        };
        let label = if source == "service" {
            "service"
        } else {
            "upstream"
        };
        let response = request
            .config()
            .timeout_global(Some(DOWNLOAD_TIMEOUT))
            .build()
            .call()
            .map_err(|err| format!("{label} download failed: {}", self.scrub(err.to_string())))?;
        let status = response.status().as_u16();
        if status != 200 {
            return Err(format!("{label} download answered {status}"));
        }
        // ureq refuses a body whose length equals its limit; the caller's writer enforces the exact size.
        let mut reader = response
            .into_body()
            .into_with_config()
            .limit(limit.saturating_add(1))
            .reader();
        io::copy(&mut reader, out)
            .map(|_| ())
            .map_err(|err| format!("{label} download failed: {}", self.scrub(err.to_string())))
    }
}

/// A writer that hashes and counts bytes and refuses more than `limit`.
pub struct HashingWriter<W: Write> {
    inner: W,
    hasher: Sha256,
    pub count: u64,
    limit: u64,
}

impl<W: Write> HashingWriter<W> {
    pub fn new(inner: W, limit: u64) -> Self {
        HashingWriter {
            inner,
            hasher: Sha256::new(),
            count: 0,
            limit,
        }
    }

    pub fn finish(self) -> (W, String, u64) {
        (
            self.inner,
            crate::jcs::hex(&self.hasher.finalize()),
            self.count,
        )
    }
}

impl<W: Write> Write for HashingWriter<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if self.count + buf.len() as u64 > self.limit {
            return Err(io::Error::other(
                "download is larger than its declared size",
            ));
        }
        let n = self.inner.write(buf)?;
        self.hasher.update(&buf[..n]);
        self.count += n as u64;
        Ok(n)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}
