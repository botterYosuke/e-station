/// Fix 2: HybridVenueBackend — compile-time contract check.
///
/// Verifies that `HybridVenueBackend` implements `VenueBackend + Send + Sync`
/// and can be installed into `AdapterHandles`.  No IO is performed.
use exchange::adapter::{AdapterHandles, Venue, VenueBackend};
use flowsurface_engine_client::HybridVenueBackend;
use std::sync::Arc;

fn assert_send_sync<T: Send + Sync>() {}

#[test]
fn hybrid_backend_is_send_sync() {
    assert_send_sync::<HybridVenueBackend>();
}

#[test]
fn hybrid_backend_implements_venue_backend() {
    fn _type_check(b: HybridVenueBackend) -> Arc<dyn VenueBackend> {
        Arc::new(b)
    }
}

#[test]
fn set_backend_accepts_hybrid_backend() {
    fn _type_check(mut handles: AdapterHandles, b: HybridVenueBackend) {
        handles.set_backend(Venue::Binance, Arc::new(b));
    }
}
