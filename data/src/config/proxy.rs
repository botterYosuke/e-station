use exchange::proxy::{Proxy, ProxyAuth};

const KEYCHAIN_SERVICE: &str = "flowsurface.proxy";
const PROXY_URL_FILE: &str = "proxy-url.json";

/// Persist the proxy URL (without auth) to a dedicated file so it survives
/// a crash between Apply and the next graceful shutdown.
pub fn save_proxy_url(url: Option<&str>) {
    match serde_json::to_string(&url) {
        Ok(json) => {
            if let Err(e) = crate::write_json_to_file(&json, PROXY_URL_FILE) {
                log::warn!("Failed to save proxy URL to file: {e}");
            }
        }
        Err(e) => log::warn!("Failed to serialize proxy URL: {e}"),
    }
}

/// Load the proxy URL written by [`save_proxy_url`].
/// Returns `None` if the file doesn't exist or cannot be parsed.
pub fn load_proxy_url() -> Option<Option<String>> {
    let path = crate::data_path(Some(PROXY_URL_FILE));
    let contents = std::fs::read_to_string(path).ok()?;
    match serde_json::from_str::<Option<String>>(&contents) {
        Ok(url) => Some(url),
        Err(e) => {
            log::warn!("Failed to parse proxy URL file: {e}");
            None
        }
    }
}

fn entry_for(proxy: &Proxy) -> Result<keyring::Entry, keyring::Error> {
    let key = proxy.to_url_string_no_auth();
    keyring::Entry::new(KEYCHAIN_SERVICE, &key)
}

pub fn load_proxy_auth(proxy: &Proxy) -> Option<ProxyAuth> {
    let key = proxy.to_url_string_no_auth();

    let entry = match entry_for(proxy) {
        Ok(e) => e,
        Err(err) => {
            log::warn!(
                "Keychain entry init failed for service={KEYCHAIN_SERVICE} key={key}: {err}"
            );
            return None;
        }
    };

    let secret = match entry.get_password() {
        Ok(s) => s,
        Err(err) => {
            log::info!("No proxy auth in keychain for service={KEYCHAIN_SERVICE} key={key}: {err}");
            return None;
        }
    };

    match serde_json::from_str::<ProxyAuth>(&secret) {
        Ok(auth) => Some(auth),
        Err(err) => {
            log::warn!(
                "Proxy auth in keychain is invalid JSON for service={KEYCHAIN_SERVICE} key={key}: {err}"
            );
            None
        }
    }
}

pub fn save_proxy_auth(proxy: &Proxy) {
    let key = proxy.to_url_string_no_auth();

    let Some(auth) = proxy.auth() else {
        log::info!("Not saving proxy auth: auth is None (service={KEYCHAIN_SERVICE} key={key})");
        return;
    };

    let entry = match entry_for(proxy) {
        Ok(e) => e,
        Err(err) => {
            log::warn!(
                "Keychain entry init failed for service={KEYCHAIN_SERVICE} key={key}: {err}"
            );
            return;
        }
    };

    let secret = match serde_json::to_string(auth) {
        Ok(s) => s,
        Err(err) => {
            log::warn!(
                "Failed to serialize proxy auth for service={KEYCHAIN_SERVICE} key={key}: {err}"
            );
            return;
        }
    };

    match entry.set_password(&secret) {
        Ok(()) => {
            log::info!("Stored proxy auth in keychain (service={KEYCHAIN_SERVICE} key={key})")
        }
        Err(err) => {
            log::warn!(
                "Failed to store proxy auth in keychain (service={KEYCHAIN_SERVICE} key={key}): {err}"
            );
            return;
        }
    }

    match entry.get_password() {
        Ok(roundtrip) => {
            if roundtrip == secret {
                log::info!("Keychain roundtrip OK (service={KEYCHAIN_SERVICE} key={key})");
            } else {
                log::warn!("Keychain roundtrip MISMATCH (service={KEYCHAIN_SERVICE} key={key})");
            }
        }
        Err(err) => log::warn!(
            "Keychain roundtrip read FAILED (service={KEYCHAIN_SERVICE} key={key}): {err}"
        ),
    }
}
