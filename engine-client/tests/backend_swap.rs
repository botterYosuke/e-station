use exchange::adapter::{AdapterHandles, Venue, VenueBackend};
/// Compile-time check: `EngineClientBackend` satisfies `VenueBackend` and can be
/// installed into `AdapterHandles` via `set_backend`.
///
/// This test does not perform any IO — if it compiles, the trait bound is satisfied.
use flowsurface_engine_client::EngineClientBackend;
use std::sync::Arc;

/// Assert that `EngineClientBackend` is `Send + Sync` (required by `VenueBackend`).
fn assert_send_sync<T: Send + Sync>() {}

#[test]
fn engine_client_backend_is_send_sync() {
    assert_send_sync::<EngineClientBackend>();
}

/// Assert that `Arc<EngineClientBackend>` can be coerced to `Arc<dyn VenueBackend>`.
///
/// This mirrors the call `AdapterHandles::set_backend(Venue::Binance, Arc::new(backend))`.
/// No actual `EngineConnection` is created; we only verify the type relationships.
#[test]
fn engine_client_backend_implements_venue_backend() {
    // This function body only needs to type-check, not run.
    fn _type_check(backend: EngineClientBackend) -> Arc<dyn VenueBackend> {
        Arc::new(backend)
    }
}

/// Verify that `AdapterHandles::set_backend` accepts an `EngineClientBackend`.
///
/// We use a function-level check so the test can be run without any real connection.
#[test]
fn set_backend_accepts_engine_client_backend() {
    fn _type_check(mut handles: AdapterHandles, backend: EngineClientBackend) {
        handles.set_backend(Venue::Binance, Arc::new(backend));
    }
    // If this compiles the contract is fulfilled.
}
