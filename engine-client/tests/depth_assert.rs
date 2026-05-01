/// Phase E regression guard: unnormalised depth data must trigger `debug_assert!`
/// panics in debug builds.
///
/// In release builds these tests are omitted (`cfg(debug_assertions)` is false),
/// which is the desired behaviour — release trusts Python's normalisation.
use exchange::depth::{DeOrder, DepthPayload, DepthUpdate, LocalDepthCache};
use exchange::unit::MinTicksize;

fn min_tick(power: i8) -> MinTicksize {
    MinTicksize::new(power)
}

fn snapshot(bids: Vec<(f32, f32)>, asks: Vec<(f32, f32)>) -> DepthUpdate {
    DepthUpdate::Snapshot(DepthPayload {
        last_update_id: 1,
        time: 0,
        bids: bids
            .into_iter()
            .map(|(p, q)| DeOrder { price: p, qty: q })
            .collect(),
        asks: asks
            .into_iter()
            .map(|(p, q)| DeOrder { price: p, qty: q })
            .collect(),
    })
}

fn diff(bids: Vec<(f32, f32)>, asks: Vec<(f32, f32)>) -> DepthUpdate {
    DepthUpdate::Diff(DepthPayload {
        last_update_id: 2,
        time: 0,
        bids: bids
            .into_iter()
            .map(|(p, q)| DeOrder { price: p, qty: q })
            .collect(),
        asks: asks
            .into_iter()
            .map(|(p, q)| DeOrder { price: p, qty: q })
            .collect(),
    })
}

/// Sanity: properly normalised prices do not panic.
#[test]
fn normalised_depth_does_not_panic() {
    let mut cache = LocalDepthCache::default();
    // min_ticksize = 1.0 (power=0), prices are whole numbers — OK
    cache.update(
        snapshot(vec![(100.0, 10.0)], vec![(101.0, 5.0)]),
        min_tick(0),
    );
    cache.update(diff(vec![(100.0, 8.0)], vec![]), min_tick(0));
    // min_ticksize = 0.1 (power=-1), prices are multiples of 0.1 — OK
    cache.update(
        snapshot(vec![(100.1, 3.0)], vec![(100.2, 2.0)]),
        min_tick(-1),
    );
}

#[cfg(debug_assertions)]
mod debug_panics {
    use super::*;

    #[test]
    #[should_panic(expected = "is not at tick")]
    fn unnormalised_bid_in_snapshot_panics() {
        // 100.3 is not a multiple of 1.0
        let mut cache = LocalDepthCache::default();
        cache.update(snapshot(vec![(100.3, 10.0)], vec![]), min_tick(0));
    }

    #[test]
    #[should_panic(expected = "is not at tick")]
    fn unnormalised_ask_in_snapshot_panics() {
        let mut cache = LocalDepthCache::default();
        cache.update(snapshot(vec![], vec![(100.5, 5.0)]), min_tick(0));
    }

    #[test]
    #[should_panic(expected = "is not at tick")]
    fn unnormalised_bid_in_diff_panics() {
        let mut cache = LocalDepthCache::default();
        cache.update(diff(vec![(100.3, 10.0)], vec![]), min_tick(0));
    }

    #[test]
    #[should_panic(expected = "is not at tick")]
    fn unnormalised_ask_in_diff_panics() {
        let mut cache = LocalDepthCache::default();
        cache.update(diff(vec![], vec![(100.7, 3.0)]), min_tick(0));
    }
}
