//! IPC wire DTOs for venue-scoped payloads. Hosted in the `data` crate so
//! the dependency edge points the right way: `engine-client` depends on
//! `data` (HIGH-8 ラウンド 6 強制修正 / Group F). Previously the Wire
//! DTOs lived in `engine-client::dto`, which forced `data` to depend on
//! `engine-client` purely so `data::config::tachibana` could implement
//! `From<&TachibanaCredentials> for TachibanaCredentialsWire` — a layering
//! inversion since the IPC client structurally sits above the data crate.
//!
//! These types are intended to be serialized and dropped immediately. All
//! secret-bearing fields wrap their inner `String` in
//! [`zeroize::Zeroizing`] so the heap buffer is wiped on drop.

pub mod tachibana;
