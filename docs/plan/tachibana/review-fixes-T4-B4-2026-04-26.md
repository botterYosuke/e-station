# T4 Phase B4 review-fix-loop ログ — 2026-04-26

対象: `engine-client/{src/backend.rs, src/connection.rs, src/tachibana_meta.rs, src/dto.rs, tests/ticker_meta_map_round_trip.rs, tests/capabilities_no_secret_keys.rs (新設)}` および `python/tests/test_server_ws_compat.py`
スキル: `.claude/skills/review-fix-loop/SKILL.md`
着手: 2026-04-26
ブランチ: tachibana/phase-1/T4-ui (worktree)

## ラウンド 1 (2026-04-26)

### 統一決定
1. 可視性降格 (pub type TickerMetaMap → pub(crate)、TickerDisplayMeta フィールド pub(crate) + #[cfg(test)] コンストラクタ)
2. handshake クリーンアップ (capabilities deep clone → 所有権移動、#[serde(default)] 追加、Ready broadcast コメント修正)
3. silent failure 解消 (RecvError::Lagged を AdapterError 化、pong write エラーを warn+break)
4. ticker_meta reconnect clear (reset_ticker_meta() public API 追加)
5. テスト補強 (ticker_meta_map_round_trip 真の roundtrip、capabilities secret-leak smoke、Python WS compat capabilities roundtrip)
6. 計画書同期 (L530 B5 繰越、min_ticksize=1.0 placeholder の B5 follow-up 追加)

### Finding ID → 修正概要マッピング
| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| H1-est, H1-rust | est+rust | backend.rs | reset_ticker_meta() 追加、reconnect clear、ループ内 Arc::clone 意図コメント |
| H2-est | est | connection.rs | capabilities() doc に「UI 側で独自 cache せず最新 EngineConnection 経由で参照」追記 |
| H2-rust | rust | connection.rs | capabilities deep clone → 所有権移動 (Arc::new(capabilities)) |
| H3-est, M3-rust | est+rust | implementation-plan.md L530 | UI 統合 [B5 繰越] に更新 (engine-client API は B4 着地) |
| H3-rust | rust | backend.rs ticker_meta_handle doc | try_lock 必須規約、UI 描画パスから lock().await 禁止 |
| L1-rust, M1-type | rust+type | backend.rs / tachibana_meta.rs | pub type → pub(crate)、TickerDisplayMeta フィールド pub(crate) |
| M2-type | type | tachibana_meta.rs | #[cfg(test)] for_test() コンストラクタ追加 |
| M1-rust | rust | tachibana_meta.rs | matches_tachibana_filter doc に String 戻り値前提注記 |
| M2-rust | rust | tachibana_meta.rs | TickerDisplayMeta 構造体 doc + parse_tachibana_ticker_dict # Returns |
| M1-silent | silent | connection.rs:348 | pong write エラー warn+break |
| M2-silent | silent | connection.rs:246 | Ready broadcast コメント修正 (subscriber 0 の事実反映) |
| M3-silent | silent | backend.rs fetch_ticker_metadata | RecvError::Lagged → AdapterError::WebsocketError |
| M1-ws | ws | dto.rs EngineEvent::Ready.capabilities | #[serde(default)] 追加 |
| M2-ws | ws | python/tests/test_server_ws_compat.py | test_capabilities_in_ready_roundtrip 追加 |
| M2-est | est | engine-client/tests/capabilities_no_secret_keys.rs (新設) | secret-leak smoke test |
| M3-est, M3-rust | est+rust | engine-client/tests/ticker_meta_map_round_trip.rs | 真の roundtrip 書換 + reset_ticker_meta() pin |
| M1-est | est | implementation-plan.md | min_ticksize=1.0 placeholder の B5 follow-up 行追加 |

### LOW 持ち越し
LOW 9 件は B5 へ繰越 (TickerMetaMap 再エクスポート、Option<&TickerDisplayMeta> doc、wait_ready スタブ将来リスク、try_send_now 戻り値、market_kind gating 等)。ユーザー判断 2(b) により LOW は同 PR 範囲外。

## ラウンド 3（2026-04-26）

### 統一決定
1. `reset_ticker_meta()` を `async fn` 化して `blocking_lock()` 規約違反を解消 (H1, backend.rs 別 implementer 担当)
2. callsite と reconnect モデルを計画書で明示 (H2, H3、本ドキュメント担当)
3. dto.rs / connection.rs に Python 互換と reconnect cache の仕様コメントを追記 (M1, M3、別 implementer 担当)

### Finding ID → 修正概要マッピング
| Finding ID | 観点 | 対象ファイル | 修正概要 |
|---|---|---|---|
| H1 | est | engine-client/src/backend.rs | `reset_ticker_meta` を `async fn` 化、`blocking_lock` 削除（別 implementer 担当） |
| H2 | est | implementation-plan.md §T4 (B5 繰越行) | `reset_ticker_meta()` callsite は B5 で `src/main.rs::on_ready` クロージャから呼ぶ旨を明記。R1 では public API 公開のみで callsite は B5 繰越であることを明示 |
| H3 | est | implementation-plan.md §T4 末尾 | Phase 1 reconnect モデル = `EngineClientBackend` 再構築前提（古いインスタンス drop で `ticker_meta` も drop）であることを明記。`EngineRehello` 由来の reset hook（`update_handles` 経路で同 backend を使い回すケース）は Phase 2 (T7) で追加。本 Phase は新規構築モデルで silent gap が閉じることを設計仮定とする |
| M1 | est | engine-client/src/dto.rs | Python 側は常に dict emit、`#[serde(default)]` は defensive 旨コメント追記（別 implementer 担当） |
| M2 | est | engine-client/src/backend.rs | 1 backend = 1 venue 不変を doc 明示（別 implementer 担当） |
| M3 | est | engine-client/src/connection.rs + implementation-plan.md §T7 | reconnect 時 capabilities snapshot 更新の `capabilities_changed_after_reconnect` pin test を T7 で追加予定の旨を計画書に 1 行追記 |

### 本ドキュメント担当の修正範囲
- `docs/plan/tachibana/implementation-plan.md` §T4 (B5 繰越行) に H2 callsite 明記を追記
- `docs/plan/tachibana/implementation-plan.md` §T4 末尾に H3 Phase 1 reconnect モデルの設計仮定を新項目として追加
- `docs/plan/tachibana/implementation-plan.md` §T7 に M3 `capabilities_changed_after_reconnect` pin test 追加予定を新項目として追加
- 本ログ末尾に R3 セクションを追記
