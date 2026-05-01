# 「売り」ボタンが押せない（Phase O3 解禁漏れ）の改修計画

作成日: 2026-05-01
ステータス: ✅ 実装完了（手動確認待ち）
親計画: [docs/✅order/](./README.md) Phase O3 派生バグ修正
関連: [implementation-plan.md](./implementation-plan.md) U3 / [spec.md §2.1](./spec.md)

## 1. 背景と問題

ユーザ報告（2026-05-01）:

- 7203.TSE BUY 100 / 1100 を発注 → 注文一覧に `FILLED` として表示される
- 続いて売却（決済）したいが、注文入力ペインの **「売り」ボタンが反応しない**
- 「買い」「成行/指値/逆指値指値」「現物/信用区分」「注文」ボタンは動作する

スクリーンショット上は「売り」ボタンが表示されているがクリックしても side が
`Sell` に切り替わらない（=押せない・disabled）。

## 2. 根本原因

[src/screen/dashboard/panel/order_entry.rs:220-224](../../src/screen/dashboard/panel/order_entry.rs#L220-L224):

```rust
// Phase O0: SELL は disabled
let sell_btn = button(text("売り").size(13))
    .style(|theme, status| crate::style::button::cancel(theme, status, false));
```

- `.on_press(...)` が**未設定** — iced の `button` は `on_press` 無しだと無効化される
- `is_active` も常に `false` 固定（`cancel(theme, status, false)`）
- 当該行のコメント「Phase O0: SELL は disabled」は **Phase O0 当時の名残**

**バックエンドは既に Phase O3 で SELL を解禁済み**（注: `spec.md §5.2` 脚注では「SELL は O1 以降」と設計当初の意図が記されているが、実装は O3 で後から追加された。この齟齬は意図的な後回しであり仕様と実装の不整合ではない）:

[python/engine/exchanges/tachibana_orders.py:211](../../python/engine/exchanges/tachibana_orders.py#L211):

```python
_ALLOWED_ORDER_SIDE = {"BUY", "SELL"}
```

[python/engine/exchanges/tachibana_orders.py:229-232](../../python/engine/exchanges/tachibana_orders.py#L229-L232) のコメント:

```
Phase O3 で解禁された種別:
  - order_type: STOP_MARKET, STOP_LIMIT（逆指値）
  - time_in_force: GTD（期日指定）
  - order_side: SELL
  - tags: margin_credit_*
```

つまり **バックエンドの解禁には UI が追従できておらず、Phase O3 のリグレッション** である。
コミット [`74791de` (feat(order/O0+O1+O2+O3))](commit-74791de) で Python 側は
O3 まで一括実装されたが、Rust UI 側は O0 状態のまま取り残された。

### モデル層は正常

`OrderEntryPanel` モデル自体は SELL を扱える:

- `OrderSide::Sell` enum バリアント存在 ([order_entry.rs:11](../../src/screen/dashboard/panel/order_entry.rs#L11))
- `Message::SideChanged(OrderSide::Sell)` を `update()` で正しく処理 ([order_entry.rs:126](../../src/screen/dashboard/panel/order_entry.rs#L126))
- `build_submit_action()` が `OrderSide::Sell → engine_client::dto::OrderSide::Sell` に正しく写す ([order_entry.rs:193-196](../../src/screen/dashboard/panel/order_entry.rs#L193-L196))
- 既存テスト `order_side_sell_produces_sell_in_action` が PASS ([order_entry.rs:509-528](../../src/screen/dashboard/panel/order_entry.rs#L509-L528))

**view 関数だけが旧仕様**で、モデル層への入口（`Message::SideChanged(Sell)`）が
塞がれている状態。

## 3. ゴール

- 「売り」ボタンを押すと `side = OrderSide::Sell` に切り替わる
- 「買い」ボタンと同じ視覚的フィードバック（選択中の側が confirm/cancel スタイルで強調表示される）
- 売却注文が `SubmitOrder` まで通り、Python 側で `_ALLOWED_ORDER_SIDE` を通過して立花 API に送られる
- **自動テストで守れる範囲**: model 経路（`Message::SideChanged` → `SubmitOrder/RequestConfirm`）のリグレッションを防ぐ。ただし view の `.on_press` 欠落そのものは iced の API 制約で単体テストで検出できない。view 側の同一クラスリグレッションは **§4.3 手動確認をマージ必須ゲートとして防ぐ**

## 4. 設計

### 4.1 view 関数の修正（最小差分）

[src/screen/dashboard/panel/order_entry.rs:214-225](../../src/screen/dashboard/panel/order_entry.rs#L214-L225) を以下に置き換える:

```rust
let side_row = {
    let is_buy = self.side == OrderSide::Buy;
    let is_sell = self.side == OrderSide::Sell;

    let buy_btn = button(text("買い").size(13))
        .on_press(Message::SideChanged(OrderSide::Buy))
        .style(move |theme, status| crate::style::button::confirm(theme, status, is_buy));

    let sell_btn = button(text("売り").size(13))
        .on_press(Message::SideChanged(OrderSide::Sell))
        .style(move |theme, status| crate::style::button::cancel(theme, status, is_sell));

    row![buy_btn, sell_btn].spacing(4)
};
```

ポイント:

- `.on_press(Message::SideChanged(OrderSide::Sell))` を追加
- `is_sell` を計算し `cancel(theme, status, is_sell)` に渡す（選択中ハイライト）
- 「Phase O0: SELL は disabled」コメントは削除
- `sell_btn` は `cancel` style を用い売り方向に警戒色を付与する（将来の両ボタン `confirm` 統一は禁止 — 売りは赤系で視覚的に区別する）

### 4.2 リグレッションガード（テスト追加）

`#[cfg(test)] mod tests` の末尾に model 経路のリグレッションテストを **2 件**追加する。
iced view を直接スナップショットする手段は無いため、**Message ハンドリングと
モデル状態の双方向**で SELL の 2 本の経路（確認ダイアログ経路 / 発注経路）をカバーする:

```rust
// ── Phase O3 リグレッション①: SubmitClicked → RequestConfirm に Sell が伝わること ──
//
// 確認ダイアログ側だけ壊れたとき（order_side が Buy のまま渡される等）を検出する。
// view の .on_press 欠落は検出できないが、build_request_confirm_action() の
// order_side 変換が Sell を正しく返すことを保護する。
#[test]
fn sell_side_submit_clicked_emits_request_confirm_with_sell() {
    let mut panel = OrderEntryPanel {
        quantity: "100".into(),
        instrument_id: Some("7203.TSE".into()),
        side: OrderSide::Sell,
        ..Default::default()
    };

    let action = panel.update(Message::SubmitClicked);
    assert!(
        matches!(
            action,
            Some(Action::RequestConfirm {
                order_side: engine_client::dto::OrderSide::Sell,
                ..
            })
        ),
        "SubmitClicked は RequestConfirm(Sell) を返すべき: {action:?}"
    );
    // 確認ダイアログを出すだけで submitting は立てない
    assert!(!panel.submitting);
}

// ── Phase O3 リグレッション②: SideChanged(Sell) → ConfirmSubmit → SubmitOrder ──
//
// 過去に「Phase O0: SELL は disabled」コメントが残っていたため
// view 側で Sell の .on_press() が抜け、UI から SELL 注文が出せない
// バグが発生した（2026-05-01）。Python 側 _ALLOWED_ORDER_SIDE は
// {BUY, SELL} なのに UI だけが O0 状態に取り残されていた。
//
// Message 経路で side を切替えてから注文が最終的に Sell で送られることを保護する。
#[test]
fn sell_side_toggle_then_confirm_emits_sell_order() {
    let mut panel = OrderEntryPanel {
        quantity: "100".into(),
        instrument_id: Some("7203.TSE".into()),
        venue: Some("tachibana".into()),
        ..Default::default()
    };

    // 1) 売りに切替
    panel.update(Message::SideChanged(OrderSide::Sell));
    assert_eq!(panel.side, OrderSide::Sell, "側が Sell に切り替わるべき");

    // 2) 確認ダイアログをスキップして直接 ConfirmSubmit → SubmitOrder
    let action = panel.update(Message::ConfirmSubmit);
    assert!(
        matches!(
            action,
            Some(Action::SubmitOrder {
                order_side: engine_client::dto::OrderSide::Sell,
                ..
            })
        ),
        "Sell 側の SubmitOrder が組み立てられるべき: {action:?}"
    );
}
```

> **既存の `order_side_sell_produces_sell_in_action` との違い**:
> 既存テストは `panel.side = Sell` を**直接初期化**してから `ConfirmSubmit` を
> 投げているため、`Message::SideChanged(Sell)` の入口が塞がれていても PASS する。
> 本テストは「Message 経路で side を切替えてから注文が出るか」を検証することで、
> model 経路のリグレッションを防ぐ（ただし view の `.on_press` 欠落そのものは検出できない。view 側の保護は §4.3 手動確認に依存する）。

view 関数本体（`.on_press` の有無）を直接 assert する単体テストは iced の
public API では難しい（`button` の `on_press` は private フィールド）。
**model 経路を保護する**ことを検査する上記テストで実用上の防御層になる。

> このテストは model 経路のリグレッションを防ぐが、view の `.on_press` 欠落そのものは検出できない。view 側の保護は §4.3 手動受け入れ基準に依存する。

`SideChanged` は外部 Action を返さない（`None` 返却）設計で十分。拡張が必要な場合は将来 Action variant を追加すること。

### 4.3 受け入れ基準

⚠ 以下のステップはすべて**デモ口座**で確認すること（`TACHIBANA_ALLOW_PROD=1` は設定しない）

`cargo build` 後に [運用クイックスタート](../../.claude/skills/tachibana/SKILL.md#運用クイックスタートローカル起動で立花セッションを作る)
の手順で flowsurface を debug 起動し、デモ口座で以下を確認:

1. 7203.TSE 100 株を BUY MARKET で発注 → FILLED を確認
2. 注文入力ペインで **「売り」ボタンをクリック → ボタンが選択スタイルに変化**
3. 数量 100 / 成行 / 現物 / 注文 → 確認ダイアログ → 確定。確認ダイアログに『売り』と表示されることを確認
4. 注文一覧に SELL 注文が ACCEPTED → FILLED で並ぶ
5. 買余力（現物可能額）が増えていることを確認
6. （補足）GUI を経由しない SELL 疎通確認は `uv run pytest python/tests/test_tachibana_submit_order.py -v` で担保する（ステップ F3 参照）

SELL 注文の `tachibana_orders.jsonl` WAL 記録はバックエンド既存実装（Phase O3 解禁済み）で保証済み — 本変更での WAL 修正は不要。

## 5. 変更範囲

| ファイル | 行数 | 内容 |
|---|---|---|
| [src/screen/dashboard/panel/order_entry.rs:214-225](../../src/screen/dashboard/panel/order_entry.rs#L214-L225) | -4 / +5 | `sell_btn` に `.on_press` と `is_sell` ハイライトを追加、O0 コメント削除 |
| [src/screen/dashboard/panel/order_entry.rs](../../src/screen/dashboard/panel/order_entry.rs) | +25 | `sell_side_toggle_then_confirm_emits_sell_order` テストを追加 |
| `python/tests/test_tachibana_submit_order.py` | +1ケース | SELL 正常系を追加（order_side="SELL" + cash_margin=cash） |

それ以外（Python 側 `tachibana_orders.py` / IPC schema / WAL / 注文一覧 UI など）
は **触らない** — バックエンドは既に Phase O3 まで対応済み。

## 6. 非ゴール（このフェーズで扱わない）

- 信用返済（`margin_credit_repay` / `margin_general_repay`）の建玉個別指定 UI
  → 別計画（implementation-plan.md T3.x で別途）
- 保有銘柄一覧の表示
  → [add-positions-pane-plan.md](./add-positions-pane-plan.md) で対応中
- 「売り」発注時の保有数量バリデーション（売り過ぎ防止）
  → 立花 API 側 `sResultCode` で reject される設計（[spec.md §6](./spec.md) のエラーマッピングに従う）。UI 側で先読みチェックは行わない

## 7. ロールアウト

1. ブランチ `sasa/fix-sell-button-disabled` を切る
2. テスト追加（※このテストは model 経路が既存コードで通るため view 修正前でも GREEN。リグレッション防止目的で追加する）→ view 修正（緑確認）→ `cargo clippy -- -D warnings` → `cargo test --workspace`。`uv run pytest python/tests/test_tachibana_submit_order.py -v` も実行する
3. (マージ前必須) `sTatebiType` 未確認リスクを `open-questions.md` に Q として起票する
4. **(マージ必須ゲート)** debug ビルドで §4.3 の受け入れ基準 1〜6 を実機確認（デモ口座）。
   view の `.on_press` 欠落は自動テストで検出できないため、この手動確認がリグレッション防止の最終防衛線。
   確認なしでマージしない。
5. PR を立て、レビューは `/e-station-review` で実施
6. マージ後 [bug-postmortem](../../.claude/skills/bug-postmortem/SKILL.md) を起動し
   `MISSES.md` パターン表に `バックエンド段階解禁 UI 追従漏れ` を追記。教訓: 「Phase コメントを残したまま on_press 解禁を忘れるパターン。Phase を上げるコミット時は Rust UI の同名ボタン全件を grep して on_press 有無を確認する」

## 8. リスク

- 売り発注は実弾リスクあり。**デモ環境で確認後に本番展開**すること
  （`TACHIBANA_ALLOW_PROD=1` を設定しない限り本番 URL は遮断されるので安全装置は効く）
- 信用建玉の返済は Phase O3 で立花 API としては対応済みだが、UI で `cash_margin`
  pick_list から `margin_credit_repay` / `margin_general_repay` を選んで「売り」を実行した場合、
  **建日種類（`sTatebiType`）のデフォルト挙動**に依存する。MarginCreditRepay/MarginGeneralRepay + Sell の組み合わせは `sTatebiType="*"`（一括）で立花へ送信される。現行コードで意図せず動作する可能性あり。`tachibana_orders.py` での `sTatebiType` 挙動は **マージ前に `open-questions.md` へ Q として起票すること**（§7 ステップ 3 参照）
