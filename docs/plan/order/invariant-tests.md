# 立花注文 不変条件 ↔ テスト対応表

> 管理ポリシー: `spec.md §6` の不変条件が増減したら本表を同時に更新すること。
> CI: `python/tests/test_invariant_tests_doc.py` で本ファイルの存在と不変条件 ID の網羅を assert する。

| 不変条件 ID | 説明 | テストファイル | 関数名 | ステータス |
|---|---|---|---|---|
| A-H2 | `reason_code` は SCREAMING_SNAKE_CASE 固定文字列のみ（spec.md §5.2） | TBD | TBD | TBD |
| C-H1 | 仮想 URL（sUrlRequest / sUrlEvent / sUrlEventWebSocket）と p_no クエリを WAL・ログ・reason_text に出さない。`mask_virtual_url()` 必須（spec.md §3.1 / §3.4） | `python/tests/test_url_masker.py` | TBD | TBD |
| C-H2 | 立花 HTTP リクエストは Shift-JIS + `func_replace_urlecnode` パーセントエンコード必須（spec.md §3.0） | TBD | TBD | TBD |
| C-H3 | 約定通知重複検知キーは `(venue_order_id, trade_id)` タプル。`trade_id` 単独では衝突しうるため `venue_order_id` と組で比較する（spec.md §3.3） | TBD | TBD | TBD |
| C-H4 | `replay_mode == true` のとき全 `/api/order/*` を 503 + `reason_code="REPLAY_MODE_ACTIVE"` で拒否。Rust HTTP 層最前段で判定し Python に到達させない（spec.md §3.2） | TBD | TBD | TBD |
| C-M2 | 第二暗証番号は Python メモリのみ保持。アイドル N 分・夜間閉局・仮想 URL refresh のいずれかで自動 forget する（spec.md §3.1） | `python/tests/test_second_password_idle_forget.py` | TBD | TBD |
| C-M3 | 同一 `(instrument_id, order_side, quantity, price)` が N 秒以内に Y 回以上送られたら 429 + `reason_code="RATE_LIMITED"`（spec.md §3.2） | TBD | `test_rate_limit_rejects_at_n_plus_1` / `test_rate_limit_resets_after_window` / `test_rate_limit_different_key_counts_independently` | TBD |
| C-M5 | `p_errno=2` 検知で `OrderSessionState` を frozen 遷移し、以降の全 `/api/order/*` を 503 + `reason_code="SESSION_EXPIRED"` で即時拒否（spec.md §3.3） | TBD | TBD | TBD |
| C-R2-M3 | `SubmitOrderRequest` / `OrderModifyChange` は `deny_unknown_fields` を付与し、`second_password` / `secondPassword` / `p_no` 等の混入を serde 段で弾く（architecture.md §10.0） | `engine-client/tests/dto_deny_unknown_fields.rs` | TBD | TBD |
| C-R2-H2 | 第二暗証番号 idle timer は monotonic clock で計測し、reset trigger は `SetSecondPassword` / `SubmitOrder` 受信時のみに限定する（architecture.md §5.3） | `python/tests/test_second_password_idle_forget.py` | TBD | TBD |
| C-R2-L1 | EVENT URL 構築時に `\n` / `\t` / `\x01-\x03` 等の制御文字を reject（除去ではなく reject に統一）（architecture.md §6） | `python/tests/test_event_url_sanitize.py` | TBD | TBD |
| C-R5-H2 | `SECOND_PASSWORD_INVALID` が連続 N 回（デフォルト 3 回）で lockout 状態に遷移し、`SubmitOrder` / `ModifyOrder` / `CancelOrder` を 423 + `reason_code="SECOND_PASSWORD_LOCKED"` で reject する（spec.md §5.2） | `python/tests/test_second_password_lockout.py` | TBD | TBD |

## 備考

- 上記テスト関数名欄が「TBD」のものは対応テストが未実装。実装時に本表を更新すること。
- `test_invariant_tests_doc.py` は本ファイルに登場する不変条件 ID がすべて `spec.md §6` の記述と一致することを assert し、陳腐化したら CI が落ちる運用にする。
- 本表は `docs/plan/order/implementation-plan.md` Tpre.6 受け入れ条件 D2-M1 R2 に対応する成果物。
