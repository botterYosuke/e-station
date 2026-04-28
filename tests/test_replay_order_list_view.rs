/// N1.15: ソースピン — OrdersPanel に is_replay フィールドと new_replay() が実装されていることを確認。
///
/// `OrdersPanel` は iced GUI 依存のため直接インスタンス化が困難。
/// ソースコード検査で不変条件をピンする（他の structural-pin テストと同方式）。

fn read_orders_panel_source() -> String {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/src/screen/dashboard/panel/orders.rs"
    );
    std::fs::read_to_string(path).expect("read orders.rs")
}

/// is_replay フィールドが pub で宣言されている
#[test]
fn orders_panel_has_pub_is_replay_field() {
    let src = read_orders_panel_source();
    assert!(
        src.contains("pub is_replay: bool"),
        "OrdersPanel must have `pub is_replay: bool` field; got:\n{src}"
    );
}

/// new_replay() コンストラクタが存在し is_replay: true を設定する
#[test]
fn orders_panel_new_replay_sets_is_replay_true() {
    let src = read_orders_panel_source();
    assert!(
        src.contains("fn new_replay"),
        "OrdersPanel must have a `new_replay` constructor; got:\n{src}"
    );
    assert!(
        src.contains("is_replay: true"),
        "new_replay() must set `is_replay: true`; got:\n{src}"
    );
}

/// new() コンストラクタは is_replay をデフォルト (false) にする。
/// Default derive または明示的 is_replay: false のどちらでも可。
#[test]
fn orders_panel_new_has_is_replay_false() {
    let src = read_orders_panel_source();
    // is_replay は bool のデフォルト (false) か明示的に false を設定するかのどちらか。
    // #[derive(Default)] を使う場合は is_replay: false が本文に現れないことがある。
    // new_replay() だけが true を設定し、new() は Default::default() を使う実装を許容する。
    let uses_derive_default = src.contains("#[derive(Debug, Default)]")
        || src.contains("derive(Default)")
        || src.contains("Self::default()");
    let uses_explicit_false = src.contains("is_replay: false");
    assert!(
        uses_derive_default || uses_explicit_false,
        "OrdersPanel::new() must use Default or set `is_replay: false`; got:\n{src}"
    );
}

/// ビュー関数がバナーを表示する分岐を持つ
#[test]
fn orders_panel_view_shows_replay_banner() {
    let src = read_orders_panel_source();
    assert!(
        src.contains("is_replay") && src.contains("REPLAY"),
        "view() must check is_replay and show REPLAY banner; got:\n{src}"
    );
}
