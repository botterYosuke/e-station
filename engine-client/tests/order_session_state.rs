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
    let cid = ClientOrderId::try_new("order-001").unwrap();
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
    let cid = ClientOrderId::try_new("order-002").unwrap();
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
    let cid = ClientOrderId::try_new("order-003").unwrap();
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
    let cid = ClientOrderId::try_new("order-004").unwrap();

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
    let cid = ClientOrderId::try_new("order-005").unwrap();
    state.try_insert(cid.clone(), 0xbeef);

    let updated = state.update_venue_order_id(cid.clone(), "V42".to_string());
    assert!(updated, "first update_venue_order_id must return true");

    let vid = state.get_venue_order_id(&cid);
    assert_eq!(vid, Some("V42"), "venue_order_id should be stored");
}

#[test]
fn get_venue_order_id_returns_none_for_unknown() {
    let state = make_state();
    let cid = ClientOrderId::try_new("nonexistent").unwrap();
    assert_eq!(state.get_venue_order_id(&cid), None);
}

#[test]
fn update_venue_order_id_does_not_overwrite_existing_venue_id() {
    let mut state = make_state();
    let cid = ClientOrderId::try_new("order-overwrite").unwrap();
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
    let cid_a = ClientOrderId::try_new("A").unwrap();
    let cid_b = ClientOrderId::try_new("B").unwrap();

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

// A-1: update_venue_order_id_from_list のマッチロジックテスト
// 2 件の None エントリが存在するとき、venue_order_id "VID-A" を渡すと
// 最初のエントリだけが更新され、2 件目は None のまま。
#[test]
fn update_venue_order_id_from_list_matches_specific_venue_order_id() {
    let mut state = OrderSessionState::new();
    let cid_a = ClientOrderId::try_new("cid-list-A").unwrap();
    let cid_b = ClientOrderId::try_new("cid-list-B").unwrap();

    // 両エントリとも venue_order_id = None（unknown 状態）
    state.try_insert(cid_a.clone(), 0xAAA1);
    state.try_insert(cid_b.clone(), 0xBBB1);

    // VID-A を渡す → cid_a または cid_b の 1 件のみ更新されるべき
    let updated = state.update_venue_order_id_from_list("VID-A", "ACCEPTED");
    assert!(updated.is_some(), "must return Some(client_order_id)");

    // 更新されたエントリは venue_order_id = Some("VID-A")
    // 更新されなかったエントリは venue_order_id = None のまま
    let updated_cid = updated.unwrap();
    assert_eq!(
        state.get_venue_order_id(&updated_cid),
        Some("VID-A"),
        "updated entry must have VID-A"
    );

    // 他方のエントリは None のまま
    let other_cid = if updated_cid == cid_a { &cid_b } else { &cid_a };
    assert_eq!(
        state.get_venue_order_id(other_cid),
        None,
        "the other entry must remain None"
    );
}

#[test]
fn update_venue_order_id_from_list_returns_none_when_no_unknown_entries() {
    let mut state = OrderSessionState::new();
    let cid = ClientOrderId::try_new("cid-no-unknown").unwrap();
    state.try_insert(cid.clone(), 0x1234);
    let _ = state.update_venue_order_id(cid.clone(), "ALREADY-SET".to_string());

    // None エントリがなければ None を返す
    let updated = state.update_venue_order_id_from_list("VID-NEW", "ACCEPTED");
    assert!(updated.is_none(), "no unknown entries → must return None");
}

// A-1-b: 既に venue_order_id が確定しているエントリを優先してステータス更新する
#[test]
fn update_venue_order_id_from_list_updates_existing_match_not_unknown() {
    let mut state = OrderSessionState::new();
    let cid_known = ClientOrderId::try_new("cid-known").unwrap();
    let cid_unknown = ClientOrderId::try_new("cid-unknown").unwrap();

    state.try_insert(cid_known.clone(), 0x1111);
    state.try_insert(cid_unknown.clone(), 0x2222);

    // cid_known に VID-K を割り当て済み
    let _ = state.update_venue_order_id(cid_known.clone(), "VID-K".to_string());

    // GetOrderList で VID-K のステータスが FILLED に更新された
    let updated = state.update_venue_order_id_from_list("VID-K", "FILLED");
    assert_eq!(
        updated,
        Some(cid_known.clone()),
        "must update the already-mapped entry"
    );

    // cid_unknown は None のまま（上書きされない）
    assert_eq!(state.get_venue_order_id(&cid_unknown), None);
    // cid_known は VID-K のまま、status=FILLED
    assert_eq!(state.get_venue_order_id(&cid_known), Some("VID-K"));
}

// A-2: try_new の境界値テスト
#[test]
fn try_new_empty_string_returns_none() {
    assert!(
        ClientOrderId::try_new("").is_none(),
        "empty string must return None"
    );
}

#[test]
fn try_new_36_chars_returns_some() {
    let s = "a".repeat(36);
    assert!(
        ClientOrderId::try_new(&s).is_some(),
        "36-char string must return Some"
    );
}

#[test]
fn try_new_37_chars_returns_none() {
    let s = "a".repeat(37);
    assert!(
        ClientOrderId::try_new(&s).is_none(),
        "37-char string must return None"
    );
}

#[test]
fn try_new_non_ascii_returns_none() {
    assert!(
        ClientOrderId::try_new("order-あ").is_none(),
        "non-ASCII must return None"
    );
}

#[test]
fn try_new_control_char_returns_none() {
    assert!(
        ClientOrderId::try_new("order\x01").is_none(),
        "control char must return None"
    );
}

#[test]
fn try_new_valid_ascii_printable_returns_some() {
    assert!(
        ClientOrderId::try_new("order-001").is_some(),
        "valid ASCII printable must return Some"
    );
}
