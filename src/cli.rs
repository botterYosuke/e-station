/// Command-line argument parsing for the Flowsurface viewer.
use std::path::PathBuf;
use url::Url;

#[derive(Debug, Default)]
pub struct CliArgs {
    /// WebSocket URL of an externally managed Python data engine.
    /// When set, Flowsurface connects to this URL and does not spawn the engine.
    pub data_engine_url: Option<Url>,
    /// Override path to the engine executable (or `python` interpreter).
    /// Used by `--engine-cmd` for dev installs that need a non-default
    /// interpreter (e.g. inside a uv-managed virtualenv).
    pub engine_cmd: Option<PathBuf>,
}

impl CliArgs {
    pub fn parse() -> Self {
        Self::parse_from(std::env::args()).unwrap_or_else(|e| {
            eprintln!("flowsurface: {e}");
            std::process::exit(1);
        })
    }

    pub fn parse_from(args: impl Iterator<Item = String>) -> Result<Self, String> {
        let mut data_engine_url: Option<Url> = None;
        let mut engine_cmd: Option<PathBuf> = None;
        let mut iter = args.skip(1); // skip executable name

        while let Some(arg) = iter.next() {
            if arg == "--engine-cmd" {
                let raw = iter
                    .next()
                    .ok_or_else(|| "--engine-cmd requires a value".to_string())?;
                engine_cmd = Some(PathBuf::from(raw));
                continue;
            }
            if arg == "--data-engine-url" {
                let raw = iter
                    .next()
                    .ok_or_else(|| "--data-engine-url requires a value".to_string())?;
                let url = Url::parse(&raw)
                    .map_err(|e| format!("invalid --data-engine-url value '{raw}': {e}"))?;
                if url.scheme() != "ws" {
                    return Err(format!(
                        "--data-engine-url: scheme '{}' is not supported; \
                         use ws:// (loopback IPC does not require TLS)",
                        url.scheme()
                    ));
                }
                if !is_loopback(&url) {
                    return Err(format!(
                        "--data-engine-url: host '{}' is not a loopback address; \
                         only 127.0.0.1, ::1, and localhost are allowed",
                        url.host_str().unwrap_or("<none>")
                    ));
                }
                data_engine_url = Some(url);
            }
            // Unknown flags are silently ignored to stay forward-compatible.
        }

        Ok(Self {
            data_engine_url,
            engine_cmd,
        })
    }
}

fn is_loopback(url: &Url) -> bool {
    match url.host() {
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        Some(url::Host::Domain(d)) => d == "localhost",
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(v: &[&str]) -> impl Iterator<Item = String> {
        std::iter::once("flowsurface".to_string()).chain(v.iter().map(|s| s.to_string()))
    }

    #[test]
    fn no_args_yields_none() {
        let cli = CliArgs::parse_from(args(&[])).expect("should succeed");
        assert!(cli.data_engine_url.is_none());
    }

    #[test]
    fn data_engine_url_is_parsed() {
        let cli = CliArgs::parse_from(args(&["--data-engine-url", "ws://127.0.0.1:9001"])).unwrap();
        let url = cli.data_engine_url.expect("should have url");
        assert_eq!(url.host_str(), Some("127.0.0.1"));
        assert_eq!(url.port(), Some(9001));
        assert_eq!(url.scheme(), "ws");
    }

    #[test]
    fn unknown_flags_are_ignored() {
        let cli = CliArgs::parse_from(args(&["--unknown-flag", "value"])).unwrap();
        assert!(cli.data_engine_url.is_none());
    }

    #[test]
    fn data_engine_url_rejects_wss_scheme() {
        // wss:// is not supported: loopback IPC never needs TLS.
        // The CLI should return a clear error so the user knows to use ws://.
        let result =
            CliArgs::parse_from(args(&["--data-engine-url", "wss://127.0.0.1:9001/engine"]));
        assert!(result.is_err(), "wss:// should be rejected");
        let msg = result.unwrap_err();
        assert!(
            msg.contains("not supported") || msg.contains("ws://"),
            "error should mention ws://: {msg}"
        );
    }

    #[test]
    fn mixed_args_with_data_engine_url() {
        let cli = CliArgs::parse_from(args(&[
            "--some-flag",
            "ignored",
            "--data-engine-url",
            "ws://127.0.0.1:8888",
        ]))
        .unwrap();
        assert!(cli.data_engine_url.is_some());
    }

    #[test]
    fn missing_value_returns_error() {
        let result = CliArgs::parse_from(args(&["--data-engine-url"]));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("requires a value"));
    }

    #[test]
    fn invalid_url_returns_error() {
        let result = CliArgs::parse_from(args(&["--data-engine-url", "not a url"]));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("invalid --data-engine-url"));
    }

    #[test]
    fn rejects_non_loopback_host() {
        let result = CliArgs::parse_from(args(&["--data-engine-url", "ws://example.com:8765"]));
        assert!(result.is_err(), "remote host should be rejected");
        let msg = result.unwrap_err();
        assert!(
            msg.contains("loopback"),
            "error should mention loopback: {msg}"
        );
    }

    #[test]
    fn accepts_localhost_domain() {
        let cli = CliArgs::parse_from(args(&["--data-engine-url", "ws://localhost:8765"])).unwrap();
        assert!(cli.data_engine_url.is_some());
    }

    #[test]
    fn accepts_ipv6_loopback() {
        let cli = CliArgs::parse_from(args(&["--data-engine-url", "ws://[::1]:8765"])).unwrap();
        assert!(cli.data_engine_url.is_some());
    }

    #[test]
    fn rejects_non_loopback_ipv4() {
        let result = CliArgs::parse_from(args(&["--data-engine-url", "ws://192.168.1.1:8765"]));
        assert!(result.is_err(), "LAN address should be rejected");
    }
}
