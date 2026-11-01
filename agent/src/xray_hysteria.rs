//! The pinned HandlerService account that `xray api adu` cannot serialize.
//!
//! curl is installed by Meridian's server baseline. Its HTTP/2 transport carries
//! this small, closed protobuf request over loopback; no new agent dependency or
//! listener is needed. Never retain or report gRPC error text.

use std::fs;
use std::time::Duration;

use crate::apply::Engine;
use crate::fsutil;

fn bytes_field(field: u8, bytes: &[u8], out: &mut Vec<u8>) {
    out.push((field << 3) | 2);
    let mut length = bytes.len();
    while length >= 128 {
        out.push((length as u8 & 127) | 128);
        length >>= 7;
    }
    out.push(length as u8);
    out.extend_from_slice(bytes);
}

fn typed(name: &str, message: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    bytes_field(1, name.as_bytes(), &mut out);
    bytes_field(2, message, &mut out);
    out
}

fn add_request(tag: &str, email: &str, auth: &str) -> Vec<u8> {
    let mut account = Vec::new();
    bytes_field(1, auth.as_bytes(), &mut account);
    let mut user = Vec::new();
    bytes_field(2, email.as_bytes(), &mut user);
    bytes_field(
        3,
        &typed("xray.proxy.hysteria.account.Account", &account),
        &mut user,
    );
    let mut add = Vec::new();
    bytes_field(1, &user, &mut add);
    let mut request = Vec::new();
    bytes_field(1, tag.as_bytes(), &mut request);
    bytes_field(
        2,
        &typed("xray.app.proxyman.command.AddUserOperation", &add),
        &mut request,
    );
    let mut framed = vec![0]; // Uncompressed gRPC message.
    framed.extend_from_slice(&(request.len() as u32).to_be_bytes());
    framed.extend_from_slice(&request);
    framed
}

fn succeeded(headers: &str, body: &[u8]) -> bool {
    let http_ok = headers.lines().any(|line| line.trim_end() == "HTTP/2 200");
    let status = headers
        .lines()
        .filter_map(|line| line.split_once(':'))
        .filter(|(name, _)| name.eq_ignore_ascii_case("grpc-status"))
        .map(|(_, value)| value.trim())
        .next_back();
    // AlterInboundResponse is empty. A transport success alone is not a user success.
    http_ok && status == Some("0") && body == [0, 0, 0, 0, 0]
}

impl Engine<'_> {
    /// Add one bounded account to a managed loopback HandlerService without resetting its inbound.
    pub fn add_hysteria_user(
        &self,
        api: &str,
        tag: &str,
        email: &str,
        auth: &str,
    ) -> Result<(), String> {
        crate::model::check_loopback(api)?;
        crate::model::check_tag(tag)?;
        if email.is_empty() || email.len() > 128 || auth.is_empty() || auth.len() > 512 {
            return Err("The Hysteria2 account is invalid.".into());
        }
        fs::create_dir_all(&self.cache_dir)
            .map_err(|_| "Cannot prepare the Hysteria2 user API.")?;
        let request = self.cache_dir.join(format!("hy2-{tag}.grpc"));
        let headers = self.cache_dir.join(format!("hy2-{tag}.headers"));
        let response = self.cache_dir.join(format!("hy2-{tag}.response"));
        let paths = [&request, &headers, &response];
        let result = (|| {
            fsutil::write_atomic(&request, &add_request(tag, email, auth), 0o600, None)
                .map_err(|_| "Cannot prepare the Hysteria2 user API.")?;
            for path in [&headers, &response] {
                fsutil::write_atomic(path, &[], 0o600, None)
                    .map_err(|_| "Cannot prepare the Hysteria2 user API.")?;
            }
            let input = format!("@{}", request.display());
            let header_file = headers.to_string_lossy();
            let response_file = response.to_string_lossy();
            let url = format!("http://{api}/xray.app.proxyman.command.HandlerService/AlterInbound");
            let output = self
                .exec
                .run_timeout(
                    "curl",
                    &[
                        "--disable",
                        "--silent",
                        "--show-error",
                        "--http2-prior-knowledge",
                        "--noproxy",
                        "*",
                        "--proto",
                        "=http",
                        "--max-time",
                        "3",
                        "--max-filesize",
                        "65536",
                        "--header",
                        "content-type: application/grpc",
                        "--header",
                        "te: trailers",
                        "--data-binary",
                        &input,
                        "--dump-header",
                        &header_file,
                        "--output",
                        &response_file,
                        "--url",
                        &url,
                    ],
                    &[],
                    Duration::from_secs(5),
                )
                .map_err(|_| "The Hysteria2 user API could not run.")?;
            if !output.success {
                return Err("The Hysteria2 user API could not complete.");
            }
            let headers = fs::read_to_string(&headers)
                .map_err(|_| "The Hysteria2 user API returned no result.")?;
            let body =
                fs::read(&response).map_err(|_| "The Hysteria2 user API returned no result.")?;
            if headers.len() > 65536 || !succeeded(&headers, &body) {
                return Err("The Hysteria2 user API refused the user.");
            }
            Ok(())
        })();
        for path in paths {
            let _ = fs::remove_file(path);
        }
        result.map_err(str::to_string)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_successful_grpc_trailer_with_an_empty_response_counts() {
        let headers = "HTTP/2 200\r\ncontent-type: application/grpc\r\n\r\ngrpc-status: 0\r\n";
        assert!(succeeded(headers, &[0, 0, 0, 0, 0]));
        assert!(!succeeded(
            &headers.replace("grpc-status: 0", "grpc-status: 13"),
            &[0, 0, 0, 0, 0]
        ));
        assert!(!succeeded("HTTP/2 200\r\n", &[0, 0, 0, 0, 0]));
        assert!(!succeeded(headers, &[]));
    }
}
