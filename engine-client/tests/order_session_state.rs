//! Tpre.4: OrderSessionState の Created / IdempotentReplay / Conflict 3 ケーステスト。
//! flowsurface `agent_session_state.rs` の同名テストの移植。

use flowsurface_engine_client::order_session_state::{
    ClientOrderId, OrderSessionState, PlaceOrderOutcome,
};

fn make_state() -> OrderSessionState {
    OrderSessionState::new()
}

#[test]
fn first_insert_returns_created() {
    let mut state = make_state();
    let cid = ClientOrderId("order-001".to_string());
    let request_key: u64 = 0xdeadbeef;

    let outcome = state.try_insert(cid, request_key);
    assert!(
        matches!(outcome, PlaceOrderOutcome::Created { .. }),
        "first insert must return Created, got {:?}",
        outcome
    );
}

#[test]
fn same_client_order_id_and_key_returns_idempotent_replay_when_no_venue_id() {
    let mut state = make_state();
    let cid = ClientOrderId("order-002".to_string());
    let key: u64 = 0x1234;

    // First insert
    state.try_insert(cid.clone(), key);

    // Second insert with same key → IdempotentReplay
    let outcome = state.try_insert(cid, key);
    assert!(
        matches!(
            outcome,
            PlaceOrderOutcome::IdempotentReplay {
                venue_order_id: None
            }
        ),
        "duplicate with same key must return IdempotentReplay(None), got {:?}",
        outcome
    );
}

#[test]
fn same_client_order_id_and_key_returns_idempotent_replay_with_venue_id_after_update() {
    let mut state = make_state();
    let cid = ClientOrderId("order-003".to_string());
    let key: u64 = 0xabcd;

    state.try_insert(cid.clone(), key);
    let updated = state.update_venue_order_id(cid.clone(), "V999".to_string());
    assert!(
        updated,
        "update_venue_order_id must return true on first set"
    );

    let outcome = state.try_insert(cid, key);
    assert!(
        matches!(
            outcome,
            PlaceOrderOutcome::IdempotentReplay { venue_order_id: Some(ref v) } if v == "V999"
        ),
        "after update, IdempotentReplay must carry venue_order_id, got {:?}",
        outcome
    );
}

#[test]
fn different_key_same_client_order_id_returns_conflict() {
    let mut state = make_state();
    let cid = ClientOrderId("order-004".to_string());

    state.try_insert(cid.clone(), 0x1111);

    let outcome = state.try_insert(cid, 0x2222);
    assert!(
        matches!(outcome, PlaceOrderOutcome::Conflict { .. }),
        "different key must return Conflict, got {:?}",
        outcome
    );
}

#[test]
fn update_venue_order_id_updates_existing_record() {
    let mut state = make_state();
    let cid = ClientOrderId("order-005".to_string());
    state.try_insert(cid.clone(), 0xbeef);

    let updated = state.update_venue_order_id(cid.clone(), "V42".to_string());
    assert!(updated, "first update_venue_order_id must return true");

    let vid = state.get_venue_order_id(&cid);
    assert_eq!(vid, Some("V42"), "venue_order_id should be stored");
}

#[test]
fn get_venue_order_id_returns_none_for_unknown() {
    let state = make_state();
    let cid = ClientOrderId("nonexistent".to_string());
    assert_eq!(state.get_venue_order_id(&cid), None);
}

#[test]
fn update_venue_order_id_does_not_overwrite_existing_venue_id() {
    let mut state = make_state();
    let cid = ClientOrderId("order-overwrite".to_string());
    state.try_insert(cid.clone(), 0xc0de);

    // 最初の update は成功する
    let first = state.update_venue_order_id(cid.clone(), "FIRST".to_string());
    assert!(first, "first update_venue_order_id must return true");
    assert_eq!(state.get_venue_order_id(&cid), Some("FIRST"));

    // 2 回目の update は上書きせず false を返す
    let second = state.update_venue_order_id(cid.clone(), "SECOND".to_string());
    assert!(
        !second,
        "second update_venue_order_id must return false (no overwrite)"
    );
    assert_eq!(
        state.get_venue_order_id(&cid),
        Some("FIRST"),
        "venue_order_id must not be overwritten"
    );
}

#[test]
fn independent_client_order_ids_do_not_interfere() {
    let mut state = make_state();
    let cid_a = ClientOrderId("A".to_string());
    let cid_b = ClientOrderId("B".to_string());

    state.try_insert(cid_a.clone(), 0xAAA);
    state.try_insert(cid_b.clone(), 0xBBB);

    let updated = state.update_venue_order_id(cid_a.clone(), "VA".to_string());
    assert!(
        updated,
        "update_venue_order_id must return true on first set"
    );

    assert_eq!(state.get_venue_order_id(&cid_a), Some("VA"));
    assert_eq!(state.get_venue_order_id(&cid_b), None);
}
