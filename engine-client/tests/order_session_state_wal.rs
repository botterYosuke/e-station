//! T0.8 WAL 復元テスト — `OrderSessionState::load_from_wal()`.
//!
//! WAL フォーマット（architecture.md §4.2）:
//!   submit 行   → client_order_id を unknown 状態（venue_order_id=None）で登録
//!   accepted 行 → venue_order_id を埋め status を "ACCEPTED" に更新
//!   rejected 行 → エントリを map から削除（再送防止対象外）
//!
//! truncated 行（末尾 \n なし）はスキップ + WARN ログ。

use std::io::Write;

use flowsurface_engine_client::order_session_state::{
    ClientOrderId, OrderSessionState, PlaceOrderOutcome,
};
use tempfile::NamedTempFile;

// ── Helpers ───────────────────────────────────────────────────────────────────

/// 今日の UTC ms タイムスタンプ（テスト用: 0:00 JST ≒ 15:00 UTC 前日だが
/// テストでは UTC date に基づく実装なので now() で代用する）。
fn today_ts_ms() -> i64 {
    // 現在のシステム時刻を ms で返す。
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

/// submit 行を作る。
fn submit_line(cid: &str, request_key: u64) -> String {
    format!(
        r#"{{"phase":"submit","ts":{ts},"client_order_id":"{cid}","request_key":{request_key},"instrument_id":"7203.TSE","order_side":"BUY","order_type":"MARKET","quantity":"100"}}"#,
        ts = today_ts_ms(),
        cid = cid,
        request_key = request_key,
    )
}

/// accepted 行を作る。
fn accepted_line(cid: &str, venue_order_id: &str) -> String {
    format!(
        r#"{{"phase":"accepted","ts":{ts},"client_order_id":"{cid}","venue_order_id":"{venue_order_id}","p_no":1700000001,"warning_code":null,"warning_text":null}}"#,
        ts = today_ts_ms(),
        cid = cid,
        venue_order_id = venue_order_id,
    )
}

/// rejected 行を作る。
fn rejected_line(cid: &str) -> String {
    format!(
        r#"{{"phase":"rejected","ts":{ts},"client_order_id":"{cid}","reason_code":"SESSION_EXPIRED","reason_text":"セッション切れ"}}"#,
        ts = today_ts_ms(),
        cid = cid,
    )
}

/// WAL ファイルを作成してパスを返す。lines は各要素をそのまま書く（\n は呼び出し元が制御）。
fn write_wal(lines: &[String]) -> NamedTempFile {
    let mut f = NamedTempFile::new().expect("failed to create temp WAL file");
    for line in lines {
        write!(f, "{line}").expect("write failed");
    }
    f
}

// ── Tests ─────────────────────────────────────────────────────────────────────

/// submit 行のみ → unknown 状態で復元される。
#[test]
fn test_wal_restore_submit_only() {
    let line = format!("{}\n", submit_line("cid-wal-001", 0xdead));
    let f = write_wal(&[line]);

    let state = OrderSessionState::load_from_wal(f.path());

    // 同一 cid・同一 key → IdempotentReplay (venue_order_id = None)
    let mut state = state;
    let outcome = state.try_insert(ClientOrderId("cid-wal-001".to_string()), 0xdead);
    assert!(
        matches!(
            outcome,
            PlaceOrderOutcome::IdempotentReplay {
                venue_order_id: None
            }
        ),
        "submit-only WAL must restore as unknown (IdempotentReplay with None), got {outcome:?}",
    );
}

/// submit + accepted → venue_order_id が埋まった状態で復元される。
#[test]
fn test_wal_restore_accepted() {
    let submit = format!("{}\n", submit_line("cid-wal-002", 0xbeef));
    let accepted = format!("{}\n", accepted_line("cid-wal-002", "ORD-999"));
    let f = write_wal(&[submit, accepted]);

    let mut state = OrderSessionState::load_from_wal(f.path());

    let outcome = state.try_insert(ClientOrderId("cid-wal-002".to_string()), 0xbeef);
    assert!(
        matches!(
            outcome,
            PlaceOrderOutcome::IdempotentReplay {
                venue_order_id: Some(ref v)
            } if v == "ORD-999"
        ),
        "accepted WAL must restore venue_order_id, got {outcome:?}",
    );
}

/// submit + rejected → エントリが除去されているので次の try_insert は Created になる。
#[test]
fn test_wal_restore_rejected_removes_entry() {
    let submit = format!("{}\n", submit_line("cid-wal-003", 0xcafe));
    let rejected = format!("{}\n", rejected_line("cid-wal-003"));
    let f = write_wal(&[submit, rejected]);

    let mut state = OrderSessionState::load_from_wal(f.path());

    // rejected 済み → 再送は Created として処理される（再発注可能）
    let outcome = state.try_insert(ClientOrderId("cid-wal-003".to_string()), 0xcafe);
    assert!(
        matches!(outcome, PlaceOrderOutcome::Created { .. }),
        "rejected entry must be removed, allowing re-submit as Created, got {outcome:?}",
    );
}

/// 末尾行に \n が無い（truncated）場合はその行をスキップする。
#[test]
fn test_wal_restore_truncated_line() {
    // 1 行目は正常（\n あり）、2 行目は truncated（\n なし）。
    let good = format!("{}\n", submit_line("cid-wal-004", 0x1111));
    let truncated = submit_line("cid-wal-005", 0x2222); // \n なし
    let f = write_wal(&[good, truncated]);

    let mut state = OrderSessionState::load_from_wal(f.path());

    // cid-004 は復元されている。
    let out_004 = state.try_insert(ClientOrderId("cid-wal-004".to_string()), 0x1111);
    assert!(
        matches!(
            out_004,
            PlaceOrderOutcome::IdempotentReplay {
                venue_order_id: None
            }
        ),
        "first (valid) line must be restored, got {out_004:?}",
    );

    // cid-005 は truncated なのでスキップ → Created になる（= 未登録）。
    let out_005 = state.try_insert(ClientOrderId("cid-wal-005".to_string()), 0x2222);
    assert!(
        matches!(out_005, PlaceOrderOutcome::Created { .. }),
        "truncated line must be skipped (cid-005 should be Created, not IdempotentReplay), got {out_005:?}",
    );
}

/// 存在しない WAL ファイル → 空 map で初期化される。
#[test]
fn test_wal_restore_nonexistent_file() {
    let path = std::path::Path::new("/tmp/nonexistent_wal_for_test_XXXXXXXXXXX.jsonl");
    // Ensure the file truly does not exist.
    let _ = std::fs::remove_file(path);

    let mut state = OrderSessionState::load_from_wal(path);

    // 空なので try_insert は Created になる。
    let outcome = state.try_insert(ClientOrderId("cid-nonexistent".to_string()), 0xabcd);
    assert!(
        matches!(outcome, PlaceOrderOutcome::Created { .. }),
        "non-existent WAL must give empty map (Created), got {outcome:?}",
    );
}

/// 空 WAL ファイル → 空 map で初期化される。
#[test]
fn test_wal_restore_empty_file() {
    let f = write_wal(&[]);

    let mut state = OrderSessionState::load_from_wal(f.path());

    let outcome = state.try_insert(ClientOrderId("cid-empty".to_string()), 0xffff);
    assert!(
        matches!(outcome, PlaceOrderOutcome::Created { .. }),
        "empty WAL must give empty map (Created), got {outcome:?}",
    );
}

/// WAL 復元後の同一 client_order_id 再送 → IdempotentReplay を返す（冪等性確認）。
#[test]
fn test_wal_restore_idempotent_replay() {
    let submit = format!("{}\n", submit_line("cid-wal-replay", 0x4242));
    let f = write_wal(&[submit]);

    let mut state = OrderSessionState::load_from_wal(f.path());

    // 1 回目: IdempotentReplay（復元済み）
    let out1 = state.try_insert(ClientOrderId("cid-wal-replay".to_string()), 0x4242);
    assert!(
        matches!(
            out1,
            PlaceOrderOutcome::IdempotentReplay {
                venue_order_id: None
            }
        ),
        "first try_insert after restore must be IdempotentReplay, got {out1:?}",
    );

    // 2 回目も IdempotentReplay
    let out2 = state.try_insert(ClientOrderId("cid-wal-replay".to_string()), 0x4242);
    assert!(
        matches!(
            out2,
            PlaceOrderOutcome::IdempotentReplay {
                venue_order_id: None
            }
        ),
        "second try_insert must also be IdempotentReplay, got {out2:?}",
    );
}

/// unknown 状態（venue_order_id=None）で request_key が不一致 → Conflict を返す。
#[test]
fn test_wal_restore_unknown_conflict_on_different_key() {
    let submit = format!("{}\n", submit_line("cid-wal-conflict", 0xAAAA));
    let f = write_wal(&[submit]);

    let mut state = OrderSessionState::load_from_wal(f.path());

    // 異なる key → Conflict
    let outcome = state.try_insert(ClientOrderId("cid-wal-conflict".to_string()), 0xBBBB);
    assert!(
        matches!(outcome, PlaceOrderOutcome::Conflict { .. }),
        "unknown state with different key must return Conflict, got {outcome:?}",
    );
}
