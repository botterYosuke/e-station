//! Tachibana IPC wire DTOs (HIGH-8 ラウンド 6 強制修正 / Group F).
//!
//! Plain-`String` mirrors of `data::config::tachibana::TachibanaCredentials`
//! / `TachibanaSession`, used only as the IPC wire format. Construct via
//! [`From`] from the secret-holding internal type and drop the value as
//! soon as serialization is done. Hand-rolled `Debug` masks every secret
//! field. All secret-bearing string fields use [`zeroize::Zeroizing`] so
//! the heap buffer is wiped on drop.

use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

#[derive(Clone, Serialize)]
pub struct TachibanaCredentialsWire {
    pub user_id: String,
    /// Plain-text password held in a `Zeroizing<String>` so the heap buffer
    /// is wiped on drop (M4 / MEDIUM-B2-2). `Serialize` falls through to
    /// `String`'s impl via `Deref` — no `serde` feature on `zeroize` needed.
    pub password: Zeroizing<String>,
    pub second_password: Option<Zeroizing<String>>,
    pub is_demo: bool,
    pub session: Option<TachibanaSessionWire>,
}

impl std::fmt::Debug for TachibanaCredentialsWire {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaCredentialsWire")
            .field("user_id", &self.user_id)
            .field("password", &"***")
            .field(
                "second_password",
                &self.second_password.as_ref().map(|_| "***"),
            )
            .field("is_demo", &self.is_demo)
            .field("session", &self.session)
            .finish()
    }
}

#[derive(Serialize, Deserialize, Clone)]
pub struct TachibanaSessionWire {
    /// Virtual URLs are session-bound secrets (architecture.md §2.1, F-B2)
    /// and must be wiped on drop. `Zeroizing<String>` derives `Serialize` /
    /// `Deserialize` transparently through the inner `String`.
    pub url_request: Zeroizing<String>,
    pub url_master: Zeroizing<String>,
    pub url_price: Zeroizing<String>,
    pub url_event: Zeroizing<String>,
    pub url_event_ws: Zeroizing<String>,
    pub expires_at_ms: Option<i64>,
    pub zyoutoeki_kazei_c: String,
}

impl std::fmt::Debug for TachibanaSessionWire {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaSessionWire")
            .field("url_request", &"***")
            .field("url_master", &"***")
            .field("url_price", &"***")
            .field("url_event", &"***")
            .field("url_event_ws", &"***")
            .field("expires_at_ms", &self.expires_at_ms)
            .field("zyoutoeki_kazei_c", &self.zyoutoeki_kazei_c)
            .finish()
    }
}
