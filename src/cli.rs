/// Command-line argument parsing for the Flowsurface viewer.
///
/// Phase 0: only `--data-engine-url` is introduced; all other behaviour
/// remains unchanged when the flag is absent.
use url::Url;

#[derive(Debug, Default)]
pub struct CliArgs {
    /// WebSocket URL of an external Python data engine.
    /// When `None` the app uses the built-in Rust exchange adapters.
    pub data_engine_url: Option<Url>,
}

impl CliArgs {
    pub fn parse() -> Self {
        Self::parse_from(std::env::args())
    }

    pub fn parse_from(args: impl Iterator<Item = String>) -> Self {
        let mut data_engine_url: Option<Url> = None;
        let mut iter = args.skip(1); // skip executable name

        while let Some(arg) = iter.next() {
            if arg == "--data-engine-url" {
                let raw = iter.next().expect("--data-engine-url requires a value");
                let url = Url::parse(&raw)
                    .unwrap_or_else(|e| panic!("invalid --data-engine-url value '{raw}': {e}"));
                data_engine_url = Some(url);
            }
            // Unknown flags are silently ignored to stay forward-compatible.
        }

        Self { data_engine_url }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(v: &[&str]) -> impl Iterator<Item = String> {
        std::iter::once("flowsurface".to_string())
            .chain(v.iter().map(|s| s.to_string()))
    }

    #[test]
    fn no_args_yields_none() {
        let cli = CliArgs::parse_from(args(&[]));
        assert!(cli.data_engine_url.is_none());
    }

    #[test]
    fn data_engine_url_is_parsed() {
        let cli = CliArgs::parse_from(args(&["--data-engine-url", "ws://127.0.0.1:9001"]));
        let url = cli.data_engine_url.expect("should have url");
        assert_eq!(url.host_str(), Some("127.0.0.1"));
        assert_eq!(url.port(), Some(9001));
        assert_eq!(url.scheme(), "ws");
    }

    #[test]
    fn unknown_flags_are_ignored() {
        let cli = CliArgs::parse_from(args(&["--unknown-flag", "value"]));
        assert!(cli.data_engine_url.is_none());
    }

    #[test]
    fn data_engine_url_accepts_wss_scheme() {
        let cli =
            CliArgs::parse_from(args(&["--data-engine-url", "wss://127.0.0.1:9001/engine"]));
        let url = cli.data_engine_url.unwrap();
        assert_eq!(url.scheme(), "wss");
    }

    #[test]
    fn mixed_args_with_data_engine_url() {
        let cli = CliArgs::parse_from(args(&[
            "--some-flag",
            "ignored",
            "--data-engine-url",
            "ws://127.0.0.1:8888",
        ]));
        assert!(cli.data_engine_url.is_some());
    }
}
